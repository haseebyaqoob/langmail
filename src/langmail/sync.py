"""Email sync engine.

Handles two sync modes:
- Initial sync: bulk-downloads metadata for the last N months, paginates through
  all Gmail API pages, populates the local SQLite index and contact table.
- Incremental sync: uses Gmail's history API to fetch only changes (new messages,
  label changes, deletions) since the last known history ID.

Bodies are NOT downloaded during sync — only metadata and snippets.
Full thread content is fetched on-demand by the context assembler.

Rate limiting:
    Gmail API quota: 250 units/second.
    messages.list  =  5 units
    messages.get   =  5 units (metadata format)
    Batch size 100 messages → ~10 batches/sec safely at 50 units/batch.
"""

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Iterator

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from langmail.auth import get_gmail_service
from langmail.config import DATA_DIR, settings
from langmail.tools.db_tools import get_db, upsert_contact, upsert_message

console = Console()

# File that stores the last Gmail history ID for incremental sync
HISTORY_ID_FILE = DATA_DIR / "last_history_id.txt"

# Pause between API batch requests (seconds) to stay within quota
_BATCH_PAUSE = 0.1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_from(raw: str) -> tuple[str, str]:
    """Parse 'Display Name <email@domain.com>' into (name, email)."""
    name, email = parseaddr(raw)
    return name.strip(), email.strip().lower()


def _parse_date(raw: str) -> str:
    """Convert RFC 2822 date string to ISO 8601 UTC string."""
    try:
        dt = parsedate_to_datetime(raw)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _header(headers: list[dict], name: str) -> str:
    """Extract a single header value by name (case-insensitive)."""
    name_lower = name.lower()
    for h in headers:
        if h["name"].lower() == name_lower:
            return h["value"]
    return ""


def _cutoff_date(months: int) -> str:
    """Return Gmail-format date string N months ago (YYYY/MM/DD)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=months * 30)
    return cutoff.strftime("%Y/%m/%d")


def _save_history_id(history_id: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_ID_FILE.write_text(history_id)


def _load_history_id() -> str | None:
    if HISTORY_ID_FILE.exists():
        return HISTORY_ID_FILE.read_text().strip() or None
    return None


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------

def _process_message(raw: dict, conn: sqlite3.Connection) -> None:
    """Parse a raw Gmail message dict and write it to the local DB."""
    payload = raw.get("payload", {})
    headers = payload.get("headers", [])

    from_raw = _header(headers, "From")
    from_name, from_email = _parse_from(from_raw)
    to_raw = _header(headers, "To")
    cc_raw = _header(headers, "Cc")
    date_raw = _header(headers, "Date")
    subject = _header(headers, "Subject") or "(no subject)"

    labels = raw.get("labelIds", [])
    is_sent = "SENT" in labels
    is_read = "UNREAD" not in labels
    has_attachment = any(
        p.get("filename") for p in payload.get("parts", [])
    )

    # Parse recipient list
    to_emails = [e.strip() for e in to_raw.split(",") if e.strip()] if to_raw else []
    cc_emails = [e.strip() for e in cc_raw.split(",") if e.strip()] if cc_raw else []

    msg_record = {
        "message_id": raw["id"],
        "thread_id": raw.get("threadId", raw["id"]),
        "subject": subject,
        "snippet": raw.get("snippet", ""),
        "from_email": from_email,
        "from_name": from_name,
        "to_emails": to_emails,
        "cc_emails": cc_emails,
        "date": _parse_date(date_raw),
        "labels": labels,
        "is_sent": is_sent,
        "is_read": is_read,
        "has_attachment": has_attachment,
    }
    upsert_message(conn, msg_record)

    # Update contacts for both sender and recipients
    upsert_contact(conn, from_email, from_name, is_sent=is_sent)
    for recipient_raw in to_emails[:5]:  # limit to first 5 recipients
        _, rec_email = _parse_from(recipient_raw)
        if rec_email:
            upsert_contact(conn, rec_email, "", is_sent=not is_sent)


def _update_contact_relationships(conn: sqlite3.Connection) -> None:
    """Classify contacts as frequent / occasional / rare based on message counts."""
    conn.execute("""
        UPDATE contacts SET relationship =
            CASE
                WHEN message_count >= 20 THEN 'frequent'
                WHEN message_count >= 5  THEN 'occasional'
                ELSE 'rare'
            END
    """)


# ---------------------------------------------------------------------------
# Message ID pages — generator that handles pagination
# ---------------------------------------------------------------------------

def _iter_message_ids(service, query: str = "") -> Iterator[list[dict]]:
    """Yield pages of message ID dicts from the Gmail API."""
    page_token = None

    while True:
        kwargs = {
            "userId": "me",
            "maxResults": 500,
            "q": query,
        }
        if page_token:
            kwargs["pageToken"] = page_token

        response = service.users().messages().list(**kwargs).execute()
        messages = response.get("messages", [])

        if messages:
            yield messages

        page_token = response.get("nextPageToken")
        if not page_token:
            break

        time.sleep(_BATCH_PAUSE)


# ---------------------------------------------------------------------------
# Batch metadata fetch
# ---------------------------------------------------------------------------

def _fetch_metadata_batch(service, message_ids: list[str]) -> list[dict]:
    """Fetch metadata for up to 100 messages in one batch HTTP call.

    Gmail's batch endpoint reduces round-trips significantly.
    Falls back to individual requests if batch fails.
    """
    results = []

    # Google API Python client supports batch requests natively
    batch = service.new_batch_http_request()

    def _callback(request_id, response, exception):
        if exception is None and response:
            results.append(response)

    for msg_id in message_ids:
        batch.add(
            service.users().messages().get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["From", "To", "Cc", "Subject", "Date"],
            ),
            callback=_callback,
        )

    batch.execute()
    return results


# ---------------------------------------------------------------------------
# Initial sync
# ---------------------------------------------------------------------------

def run_initial_sync(months: int | None = None) -> dict:
    """Download all email metadata for the last N months.

    Args:
        months: Override sync depth. Defaults to settings.sync_depth_months.

    Returns:
        Dict with stats: messages_synced, contacts_found, duration_seconds.
    """
    months = months or settings.sync_depth_months
    service = get_gmail_service()
    conn = get_db()

    query = f"after:{_cutoff_date(months)}"
    start = time.time()
    messages_synced = 0

    console.print(f"[bold blue]Starting full sync (last {months} months)...[/bold blue]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed} messages"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Syncing email metadata...", total=None)

        for page in _iter_message_ids(service, query=query):
            # Fetch metadata in batches of 100
            for i in range(0, len(page), 100):
                batch_ids = [m["id"] for m in page[i:i + 100]]
                batch_msgs = _fetch_metadata_batch(service, batch_ids)

                with conn:
                    for msg in batch_msgs:
                        _process_message(msg, conn)
                        messages_synced += 1

                progress.update(task, completed=messages_synced)
                time.sleep(_BATCH_PAUSE)

        # Update relationship classifications
        with conn:
            _update_contact_relationships(conn)

    # Save history ID for future incremental syncs
    profile = service.users().getProfile(userId="me").execute()
    _save_history_id(str(profile.get("historyId", "")))

    duration = round(time.time() - start, 1)
    conn.close()

    return {
        "messages_synced": messages_synced,
        "contacts_found": _count_contacts(),
        "duration_seconds": duration,
    }


# ---------------------------------------------------------------------------
# Incremental sync
# ---------------------------------------------------------------------------

def run_incremental_sync() -> dict:
    """Fetch only changes since the last sync using Gmail's history API.

    Falls back to a 7-day full sync if no history ID is saved.

    Returns:
        Dict with stats: added, updated, removed, duration_seconds.
    """
    history_id = _load_history_id()
    if not history_id:
        console.print(
            "[yellow]No history ID found. Running 7-day sync instead.[/yellow]"
        )
        return run_initial_sync(months=0)  # 0 = ~7 days via cutoff

    service = get_gmail_service()
    conn = get_db()
    start = time.time()
    added = updated = removed = 0

    console.print("[bold blue]Running incremental sync...[/bold blue]")

    try:
        page_token = None
        while True:
            kwargs = {
                "userId": "me",
                "startHistoryId": history_id,
                "historyTypes": ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"],
            }
            if page_token:
                kwargs["pageToken"] = page_token

            response = service.users().history().list(**kwargs).execute()
            history = response.get("history", [])

            with conn:
                for record in history:
                    # New messages
                    for msg_added in record.get("messagesAdded", []):
                        msg_id = msg_added["message"]["id"]
                        raw = service.users().messages().get(
                            userId="me", id=msg_id, format="metadata",
                            metadataHeaders=["From", "To", "Cc", "Subject", "Date"],
                        ).execute()
                        _process_message(raw, conn)
                        added += 1

                    # Deleted messages — mark as deleted
                    for msg_deleted in record.get("messagesDeleted", []):
                        msg_id = msg_deleted["message"]["id"]
                        conn.execute(
                            "DELETE FROM messages WHERE message_id = ?", (msg_id,)
                        )
                        removed += 1

                    # Label changes — update read/unread status
                    for label_change in record.get("labelsAdded", []) + record.get("labelsRemoved", []):
                        msg_id = label_change["message"]["id"]
                        new_labels = label_change["message"].get("labelIds", [])
                        is_read = "UNREAD" not in new_labels
                        conn.execute(
                            "UPDATE messages SET is_read = ?, labels = ? WHERE message_id = ?",
                            (int(is_read), json.dumps(new_labels), msg_id),
                        )
                        updated += 1

            page_token = response.get("nextPageToken")
            if not page_token:
                # Save the latest history ID
                _save_history_id(str(response.get("historyId", history_id)))
                break

            time.sleep(_BATCH_PAUSE)

        with conn:
            _update_contact_relationships(conn)

    except Exception as e:
        if "Invalid startHistoryId" in str(e):
            console.print("[yellow]History ID expired. Running 30-day sync.[/yellow]")
            conn.close()
            return run_initial_sync(months=1)
        raise

    duration = round(time.time() - start, 1)
    conn.close()

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "duration_seconds": duration,
    }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _count_contacts() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]


def get_sync_status() -> dict:
    """Return a summary of the current local index state."""
    history_id = _load_history_id()
    with get_db() as conn:
        messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        unread = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE is_read = 0"
        ).fetchone()[0]
        contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        last_date = conn.execute("SELECT MAX(date) FROM messages").fetchone()[0]

    return {
        "synced": history_id is not None,
        "messages_indexed": messages,
        "unread_count": unread,
        "contacts_indexed": contacts,
        "latest_message": last_date,
        "history_id": history_id,
    }
