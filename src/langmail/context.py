"""Context assembler for the Draft Writer agent.

Builds four markdown context files that give the Draft Writer everything
it needs to write a context-aware, tone-matched reply:

    current_thread.md   — full decoded thread content (sender, date, body)
    contact_profile.md  — relationship stats and category from local DB
    recent_history.md   — past thread summaries with this contact
    writing_style.md    — snippets of emails the user previously sent to them

Thread bodies are cached locally as encrypted JSON files (CACHE_DIR) with
TTL-based expiry to avoid repeated Gmail API calls.

Usage:
    from langmail.context import assemble_context

    files = assemble_context(thread_id="abc123", contact_email="john@client.com")
    # files is a dict[str, str] ready to inject into GmailAgentState["files"]
"""

import json
import time
from pathlib import Path
from typing import Optional

from googleapiclient.errors import HttpError

from langmail.auth import get_gmail_service
from langmail.config import CACHE_DIR, settings
from langmail.tools.db_tools import get_db


# ---------------------------------------------------------------------------
# Thread cache — encrypted JSON files with TTL
# ---------------------------------------------------------------------------

def _cache_path(thread_id: str) -> Path:
    return CACHE_DIR / f"{thread_id}.json"


def _is_cache_fresh(path: Path) -> bool:
    """Return True if the cache file exists and is within TTL."""
    if not path.exists():
        return False
    age_days = (time.time() - path.stat().st_mtime) / 86400
    return age_days < settings.cache_ttl_days


def _write_cache(thread_id: str, data: dict) -> None:
    """Write thread data to cache, encrypting if configured."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False).encode()

    if settings.encrypt_cache:
        from langmail.auth import _get_fernet
        raw = _get_fernet().encrypt(raw)
        _cache_path(thread_id).write_bytes(raw)
    else:
        _cache_path(thread_id).write_bytes(raw)


def _read_cache(thread_id: str) -> Optional[dict]:
    """Read thread data from cache, decrypting if needed."""
    path = _cache_path(thread_id)
    if not _is_cache_fresh(path):
        return None

    raw = path.read_bytes()

    if settings.encrypt_cache:
        try:
            from langmail.auth import _get_fernet
            raw = _get_fernet().decrypt(raw)
        except Exception:
            # Cache corrupted or key changed — treat as miss
            path.unlink(missing_ok=True)
            return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        path.unlink(missing_ok=True)
        return None


# ---------------------------------------------------------------------------
# Thread fetching
# ---------------------------------------------------------------------------

def _decode_part(part: dict) -> str:
    """Recursively decode a MIME part to plain text."""
    import base64

    mime = part.get("mimeType", "")

    if mime == "text/plain":
        data = part.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    if mime.startswith("multipart/"):
        return "\n".join(_decode_part(p) for p in part.get("parts", []))

    return ""


def _header(headers: list[dict], name: str) -> str:
    name_lower = name.lower()
    for h in headers:
        if h["name"].lower() == name_lower:
            return h["value"]
    return ""


def _fetch_thread(thread_id: str) -> dict:
    """Fetch a full thread from Gmail, using cache if available.

    Returns a dict with keys: thread_id, subject, messages (list of
    {message_id, from, date, body}).
    """
    cached = _read_cache(thread_id)
    if cached:
        return cached

    service = get_gmail_service()
    try:
        response = service.users().threads().get(
            userId="me",
            id=thread_id,
            format="full",
        ).execute()
    except HttpError as e:
        if e.resp.status == 404:
            return {"thread_id": thread_id, "subject": "(not found)", "messages": []}
        raise

    messages = []
    subject = ""

    for msg in response.get("messages", []):
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])

        if not subject:
            subject = _header(headers, "Subject") or "(no subject)"

        body = _decode_part(payload).strip()
        # Trim very long bodies to keep context bounded
        if len(body) > 3000:
            body = body[:3000] + "\n… [truncated]"

        messages.append({
            "message_id": msg["id"],
            "from": _header(headers, "From"),
            "date": _header(headers, "Date"),
            "body": body,
        })

    data = {
        "thread_id": thread_id,
        "subject": subject,
        "messages": messages,
    }
    _write_cache(thread_id, data)
    return data


# ---------------------------------------------------------------------------
# Context file builders
# ---------------------------------------------------------------------------

def _build_current_thread(thread_data: dict) -> str:
    lines = [f"# Thread: {thread_data['subject']}\n"]
    for msg in thread_data["messages"]:
        lines.append(f"**From:** {msg['from']}")
        lines.append(f"**Date:** {msg['date']}")
        lines.append("")
        lines.append(msg["body"] or "(no body)")
        lines.append("\n---\n")
    return "\n".join(lines)


def _build_contact_profile(contact_email: str) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM contacts WHERE email = ?", (contact_email,)
        ).fetchone()

    if not row:
        return f"# Contact: {contact_email}\n\nNo contact record found in local index."

    name = row["name"] or contact_email
    lines = [
        f"# Contact: {name} <{contact_email}>",
        "",
        f"- **Relationship:** {row['relationship']}",
        f"- **Category:** {row['category']}",
        f"- **Total emails:** {row['message_count']} "
        f"({row['sent_count']} sent, {row['received_count']} received)",
        f"- **First contact:** {row['first_seen']}",
        f"- **Last contact:** {row['last_seen']}",
    ]
    return "\n".join(lines)


def _build_recent_history(contact_email: str) -> str:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT subject, summary, last_updated, message_count
               FROM thread_summaries
               WHERE contact_email = ?
               ORDER BY last_updated DESC
               LIMIT ?""",
            (contact_email, settings.max_context_threads),
        ).fetchall()

    if not rows:
        return (
            f"# Recent Thread History with {contact_email}\n\n"
            "No previous thread summaries found. This may be a first contact."
        )

    lines = [f"# Recent Thread History with {contact_email}\n"]
    for row in rows:
        lines.append(f"## {row['subject']} ({row['message_count']} messages)")
        lines.append(f"*Last active: {row['last_updated']}*\n")
        lines.append(row["summary"] or "(no summary)")
        lines.append("")
    return "\n".join(lines)


def _build_writing_style(contact_email: str) -> str:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT snippet, date, subject FROM messages
               WHERE is_sent = 1 AND to_emails LIKE ?
               ORDER BY date DESC
               LIMIT ?""",
            (f"%{contact_email}%", settings.max_style_samples),
        ).fetchall()

    if not rows:
        return (
            "# Writing Style Samples\n\n"
            "No previous sent emails to this contact. Match a professional, clear tone."
        )

    lines = [
        "# Writing Style Samples",
        "",
        "These are snippets of emails you previously sent to this contact.",
        "Match this tone and style in your reply.",
        "",
    ]
    for row in rows:
        lines.append(f"**{row['subject']}** ({row['date'][:10]})")
        lines.append(f"> {row['snippet']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def assemble_context(
    thread_id: str,
    contact_email: str,
) -> dict[str, str]:
    """Assemble context files for the Draft Writer agent.

    Fetches the current thread (from cache or Gmail API), then queries
    the local DB for contact profile, thread history, and writing style.

    Args:
        thread_id: Gmail thread ID to build context for.
        contact_email: Primary contact's email (used for DB lookups).

    Returns:
        Dict mapping filename → markdown content, ready to be merged
        into GmailAgentState["files"].
    """
    thread_data = _fetch_thread(thread_id)

    return {
        "current_thread.md": _build_current_thread(thread_data),
        "contact_profile.md": _build_contact_profile(contact_email),
        "recent_history.md": _build_recent_history(contact_email),
        "writing_style.md": _build_writing_style(contact_email),
    }


def purge_expired_cache() -> int:
    """Delete cache files older than the configured TTL.

    Returns:
        Number of files deleted.
    """
    if not CACHE_DIR.exists():
        return 0

    deleted = 0
    for path in CACHE_DIR.glob("*.json"):
        if not _is_cache_fresh(path):
            path.unlink(missing_ok=True)
            deleted += 1
    return deleted
