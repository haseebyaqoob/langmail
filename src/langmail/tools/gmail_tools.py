"""Gmail API LangChain tools.

All tools are decorated with @tool so they can be passed directly
to LangGraph agents. They communicate with Gmail via the authenticated
service client from auth.py.

Tools:
    gmail_fetch       — list recent emails from inbox
    gmail_read_thread — fetch full thread content
    gmail_search      — search emails by query string
    gmail_create_draft — create a reply draft (never sends)
    gmail_label       — add or remove labels from a message
"""

import base64
import email as email_lib
from typing import Optional

from googleapiclient.errors import HttpError
from langchain_core.tools import tool

from langmail.auth import get_gmail_service


def _decode_body(payload: dict) -> str:
    """Recursively extract plain text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            result = _decode_body(part)
            if result:
                return result

    return ""


def _parse_headers(headers: list[dict]) -> dict:
    """Convert Gmail header list to a flat dict."""
    return {h["name"].lower(): h["value"] for h in headers}


def _format_message(msg: dict) -> dict:
    """Parse a Gmail message dict into a clean structured format."""
    payload = msg.get("payload", {})
    headers = _parse_headers(payload.get("headers", []))

    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId", ""),
        "subject": headers.get("subject", "(no subject)"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "date": headers.get("date", ""),
        "snippet": msg.get("snippet", ""),
        "body": _decode_body(payload),
        "labels": msg.get("labelIds", []),
        "has_attachment": any(
            p.get("filename") for p in payload.get("parts", [])
        ),
    }


@tool
def gmail_fetch(max_results: int = 10, unread_only: bool = True) -> list[dict]:
    """Fetch recent emails from the Gmail inbox.

    Args:
        max_results: Maximum number of emails to return (default: 10, max: 100).
        unread_only: If True, only return unread emails (default: True).

    Returns:
        List of email summaries with id, subject, from, date, snippet, and labels.
    """
    service = get_gmail_service()
    query = "is:unread" if unread_only else ""

    result = service.users().messages().list(
        userId="me",
        maxResults=min(max_results, 100),
        q=query,
        labelIds=["INBOX"],
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return []

    # Fetch metadata for each message (batch would be faster but adds complexity)
    emails = []
    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me",
            id=msg_ref["id"],
            format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()

        payload = msg.get("payload", {})
        headers = _parse_headers(payload.get("headers", []))

        emails.append({
            "id": msg["id"],
            "thread_id": msg.get("threadId", ""),
            "subject": headers.get("subject", "(no subject)"),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
        })

    return emails


@tool
def gmail_read_thread(thread_id: str) -> dict:
    """Fetch the full content of an email thread.

    Args:
        thread_id: The Gmail thread ID to fetch.

    Returns:
        Dict with thread_id and list of messages (each with full body text).
    """
    service = get_gmail_service()

    try:
        thread = service.users().threads().get(
            userId="me",
            id=thread_id,
            format="full",
        ).execute()
    except HttpError as e:
        if e.resp.status == 404:
            return {
                "thread_id": thread_id,
                "error": "Thread not found — it may have been deleted or moved to trash.",
                "message_count": 0,
                "messages": [],
            }
        raise

    messages = [_format_message(msg) for msg in thread.get("messages", [])]

    return {
        "thread_id": thread_id,
        "message_count": len(messages),
        "messages": messages,
    }


@tool
def gmail_search(query: str, max_results: int = 20) -> list[dict]:
    """Search Gmail using Gmail's search syntax.

    Supports full Gmail search operators like:
    - from:john@example.com
    - subject:budget
    - after:2026/01/01
    - has:attachment
    - is:unread

    Args:
        query: Gmail search query string.
        max_results: Maximum results to return (default: 20).

    Returns:
        List of matching email summaries.
    """
    service = get_gmail_service()

    result = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=min(max_results, 100),
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return []

    emails = []
    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me",
            id=msg_ref["id"],
            format="metadata",
            metadataHeaders=["From", "To", "Subject", "Date"],
        ).execute()

        payload = msg.get("payload", {})
        headers = _parse_headers(payload.get("headers", []))

        emails.append({
            "id": msg["id"],
            "thread_id": msg.get("threadId", ""),
            "subject": headers.get("subject", "(no subject)"),
            "from": headers.get("from", ""),
            "date": headers.get("date", ""),
            "snippet": msg.get("snippet", ""),
        })

    return emails


@tool
def gmail_create_draft(
    to: str,
    subject: str,
    body: str,
    thread_id: Optional[str] = None,
) -> dict:
    """Create a Gmail draft. Does NOT send the email — user must send from Gmail.

    Args:
        to: Recipient email address.
        subject: Email subject line.
        body: Plain text email body.
        thread_id: Optional thread ID to reply within an existing thread.

    Returns:
        Dict with draft_id and a link to review the draft in Gmail.
    """
    service = get_gmail_service()

    # Build RFC 2822 message
    message = email_lib.message.EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    draft_body: dict = {"message": {"raw": raw}}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

    draft = service.users().drafts().create(
        userId="me",
        body=draft_body,
    ).execute()

    return {
        "draft_id": draft["id"],
        "status": "created",
        "note": "Draft saved to Gmail. Open Gmail to review and send.",
        "gmail_url": "https://mail.google.com/mail/u/0/#drafts",
    }


@tool
def gmail_label(
    message_id: str,
    add_labels: Optional[list[str]] = None,
    remove_labels: Optional[list[str]] = None,
) -> dict:
    """Add or remove labels from a Gmail message.

    Common labels: UNREAD, STARRED, IMPORTANT, INBOX, TRASH
    Custom labels use their label ID (get with gmail_search).

    Args:
        message_id: The Gmail message ID to modify.
        add_labels: List of label IDs to add.
        remove_labels: List of label IDs to remove.

    Returns:
        Dict confirming the label changes made.
    """
    service = get_gmail_service()

    body = {
        "addLabelIds": add_labels or [],
        "removeLabelIds": remove_labels or [],
    }

    service.users().messages().modify(
        userId="me",
        id=message_id,
        body=body,
    ).execute()

    return {
        "message_id": message_id,
        "added": add_labels or [],
        "removed": remove_labels or [],
        "status": "updated",
    }
