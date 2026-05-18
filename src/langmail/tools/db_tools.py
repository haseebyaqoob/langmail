"""SQLite-backed local email index tools.

Provides fast local lookups for email metadata, contact profiles,
and thread summaries without hitting the Gmail API every time.

The database lives at ~/.langmail/gmail.db and is populated by the sync commands.
"""

import json
import sqlite3
from datetime import datetime, timezone

from langchain_core.tools import tool

from langmail.config import DB_FILE


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    subject         TEXT,
    snippet         TEXT,
    from_email      TEXT NOT NULL,
    from_name       TEXT,
    to_emails       TEXT,
    cc_emails       TEXT,
    date            TEXT NOT NULL,
    labels          TEXT,
    is_sent         INTEGER DEFAULT 0,
    is_read         INTEGER DEFAULT 1,
    has_attachment  INTEGER DEFAULT 0,
    size_bytes      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS contacts (
    email           TEXT PRIMARY KEY,
    name            TEXT,
    domain          TEXT,
    first_seen      TEXT,
    last_seen       TEXT,
    message_count   INTEGER DEFAULT 0,
    sent_count      INTEGER DEFAULT 0,
    received_count  INTEGER DEFAULT 0,
    relationship    TEXT DEFAULT 'rare',
    category        TEXT DEFAULT 'unknown'
);

CREATE TABLE IF NOT EXISTS thread_summaries (
    thread_id       TEXT PRIMARY KEY,
    contact_email   TEXT,
    subject         TEXT,
    message_count   INTEGER,
    summary         TEXT,
    last_updated    TEXT,
    status          TEXT DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_messages_from   ON messages(from_email);
CREATE INDEX IF NOT EXISTS idx_messages_date   ON messages(date);
CREATE INDEX IF NOT EXISTS idx_contacts_domain ON contacts(domain);
"""


def get_db() -> sqlite3.Connection:
    """Return a SQLite connection, creating the database if needed."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    return conn


# ---------------------------------------------------------------------------
# LangChain tools — these are what agents call
# ---------------------------------------------------------------------------

@tool
def db_lookup_contact(email: str) -> dict:
    """Look up a contact's profile from the local email index.

    Args:
        email: The contact's email address.

    Returns:
        Contact profile with name, domain, relationship stats, and category.
        Returns empty dict if contact not found.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM contacts WHERE email = ?", (email,)
        ).fetchone()

    if not row:
        return {"found": False, "email": email}

    return {
        "found": True,
        "email": row["email"],
        "name": row["name"],
        "domain": row["domain"],
        "relationship": row["relationship"],
        "category": row["category"],
        "message_count": row["message_count"],
        "sent_count": row["sent_count"],
        "received_count": row["received_count"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
    }


@tool
def db_search_contacts(query: str, limit: int = 10) -> list[dict]:
    """Search contacts by name or email address.

    Args:
        query: Search string matched against name and email fields.
        limit: Maximum results to return (default: 10).

    Returns:
        List of matching contact profiles.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM contacts
               WHERE email LIKE ? OR name LIKE ?
               ORDER BY message_count DESC
               LIMIT ?""",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()

    return [dict(row) for row in rows]


@tool
def db_get_thread_summaries(contact_email: str, limit: int = 5) -> list[dict]:
    """Get recent thread summaries for a contact.

    These are LLM-generated summaries stored locally so the agent
    has context without re-reading every email.

    Args:
        contact_email: The contact's email address.
        limit: Maximum number of threads to return (default: 5).

    Returns:
        List of thread summaries ordered by most recent first.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT thread_id, subject, summary, last_updated, status, message_count
               FROM thread_summaries
               WHERE contact_email = ?
               ORDER BY last_updated DESC
               LIMIT ?""",
            (contact_email, limit),
        ).fetchall()

    return [dict(row) for row in rows]


@tool
def db_get_sent_snippets(contact_email: str, limit: int = 10) -> list[dict]:
    """Get snippets of emails the user sent to a contact.

    Used by the Draft Writer to match the user's writing tone and style.

    Args:
        contact_email: The contact's email address.
        limit: Maximum number of sent emails to return (default: 10).

    Returns:
        List of sent email snippets with date, ordered most recent first.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT snippet, date, subject FROM messages
               WHERE is_sent = 1 AND to_emails LIKE ?
               ORDER BY date DESC
               LIMIT ?""",
            (f"%{contact_email}%", limit),
        ).fetchall()

    return [dict(row) for row in rows]


@tool
def db_get_inbox_stats() -> dict:
    """Get a high-level summary of the local email index.

    Returns:
        Dict with counts of total messages, unread, contacts, and threads.
    """
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        unread = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE is_read = 0"
        ).fetchone()[0]
        contacts = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        threads = conn.execute("SELECT COUNT(*) FROM thread_summaries").fetchone()[0]
        last_sync = conn.execute(
            "SELECT MAX(date) FROM messages"
        ).fetchone()[0]

    return {
        "total_messages": total,
        "unread_messages": unread,
        "total_contacts": contacts,
        "summarized_threads": threads,
        "last_message_date": last_sync,
    }


# ---------------------------------------------------------------------------
# Internal helpers used by sync (not exposed as agent tools)
# ---------------------------------------------------------------------------

def upsert_message(conn: sqlite3.Connection, msg: dict) -> None:
    """Insert or update a message record in the local index."""
    conn.execute(
        """INSERT OR REPLACE INTO messages
           (message_id, thread_id, subject, snippet, from_email, from_name,
            to_emails, cc_emails, date, labels, is_sent, is_read, has_attachment)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            msg["message_id"],
            msg["thread_id"],
            msg.get("subject"),
            msg.get("snippet"),
            msg["from_email"],
            msg.get("from_name"),
            json.dumps(msg.get("to_emails", [])),
            json.dumps(msg.get("cc_emails", [])),
            msg["date"],
            json.dumps(msg.get("labels", [])),
            int(msg.get("is_sent", False)),
            int(msg.get("is_read", True)),
            int(msg.get("has_attachment", False)),
        ),
    )


def upsert_contact(conn: sqlite3.Connection, email: str, name: str, is_sent: bool) -> None:
    """Insert or update a contact record, incrementing message counts."""
    domain = email.split("@")[-1] if "@" in email else "unknown"
    now = datetime.now(timezone.utc).isoformat()

    existing = conn.execute(
        "SELECT * FROM contacts WHERE email = ?", (email,)
    ).fetchone()

    if existing:
        if is_sent:
            conn.execute(
                """UPDATE contacts SET sent_count = sent_count + 1,
                   message_count = message_count + 1, last_seen = ?, name = ?
                   WHERE email = ?""",
                (now, name or existing["name"], email),
            )
        else:
            conn.execute(
                """UPDATE contacts SET received_count = received_count + 1,
                   message_count = message_count + 1, last_seen = ?, name = ?
                   WHERE email = ?""",
                (now, name or existing["name"], email),
            )
    else:
        conn.execute(
            """INSERT INTO contacts
               (email, name, domain, first_seen, last_seen, message_count,
                sent_count, received_count)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
            (email, name, domain, now, now, int(is_sent), int(not is_sent)),
        )


def save_thread_summary(
    conn: sqlite3.Connection,
    thread_id: str,
    contact_email: str,
    subject: str,
    message_count: int,
    summary: str,
    status: str = "active",
) -> None:
    """Insert or update a thread summary."""
    conn.execute(
        """INSERT OR REPLACE INTO thread_summaries
           (thread_id, contact_email, subject, message_count, summary, last_updated, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            thread_id,
            contact_email,
            subject,
            message_count,
            summary,
            datetime.utcnow().isoformat(),
            status,
        ),
    )
