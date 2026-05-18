"""Shared pytest fixtures for langmail tests."""

import pytest


@pytest.fixture
def mock_email():
    """A minimal mock Gmail message dict."""
    return {
        "id": "18f3a2b1c4d5e6f7",
        "threadId": "18f3a2b1c4d5e6f7",
        "snippet": "Hi, can we schedule a meeting to review the Q3 budget?",
        "payload": {
            "headers": [
                {"name": "From", "value": "John Smith <john@client.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Q3 Budget Review"},
                {"name": "Date", "value": "Wed, 9 Apr 2026 09:00:00 +0000"},
            ],
            "body": {"data": "SGksIGNhbiB3ZSBzY2hlZHVsZSBhIG1lZXRpbmc/"},
        },
        "labelIds": ["INBOX", "UNREAD"],
    }


@pytest.fixture
def mock_thread(mock_email):
    """A minimal mock Gmail thread dict."""
    return {
        "id": "18f3a2b1c4d5e6f7",
        "messages": [mock_email],
    }
