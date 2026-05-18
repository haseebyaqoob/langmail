"""Tests for Gmail and DB tools using mocked API responses."""

from unittest.mock import MagicMock, patch



class TestGmailFetch:
    @patch("langmail.tools.gmail_tools.get_gmail_service")
    def test_fetch_returns_email_list(self, mock_service):
        """gmail_fetch should return a list of email summaries."""
        mock_svc = MagicMock()
        mock_service.return_value = mock_svc

        mock_svc.users().messages().list().execute.return_value = {
            "messages": [{"id": "abc123", "threadId": "thread1"}]
        }
        mock_svc.users().messages().get().execute.return_value = {
            "id": "abc123",
            "threadId": "thread1",
            "snippet": "Hi, can we schedule a meeting?",
            "labelIds": ["INBOX", "UNREAD"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "John <john@client.com>"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Q3 Budget"},
                    {"name": "Date", "value": "Wed, 9 Apr 2026 09:00:00 +0000"},
                ]
            },
        }

        from langmail.tools.gmail_tools import gmail_fetch
        result = gmail_fetch.invoke({"max_results": 5, "unread_only": True})

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == "abc123"
        assert result[0]["subject"] == "Q3 Budget"
        assert result[0]["from"] == "John <john@client.com>"

    @patch("langmail.tools.gmail_tools.get_gmail_service")
    def test_fetch_empty_inbox(self, mock_service):
        """gmail_fetch should return empty list when no messages."""
        mock_svc = MagicMock()
        mock_service.return_value = mock_svc
        mock_svc.users().messages().list().execute.return_value = {"messages": []}

        from langmail.tools.gmail_tools import gmail_fetch
        result = gmail_fetch.invoke({"max_results": 10, "unread_only": True})

        assert result == []


class TestGmailCreateDraft:
    @patch("langmail.tools.gmail_tools.get_gmail_service")
    def test_creates_draft_not_send(self, mock_service):
        """gmail_create_draft should create a draft and never send."""
        mock_svc = MagicMock()
        mock_service.return_value = mock_svc
        mock_svc.users().drafts().create().execute.return_value = {
            "id": "draft_xyz",
        }

        from langmail.tools.gmail_tools import gmail_create_draft
        result = gmail_create_draft.invoke({
            "to": "john@client.com",
            "subject": "Re: Q3 Budget",
            "body": "Hi John, Thursday at 2pm works for me.",
        })

        assert result["draft_id"] == "draft_xyz"
        assert result["status"] == "created"
        # Must NOT have called send
        mock_svc.users().messages().send.assert_not_called()


class TestDbTools:
    def test_lookup_contact_not_found(self, tmp_path, monkeypatch):
        """db_lookup_contact should return found=False for unknown contacts."""
        monkeypatch.setattr("langmail.tools.db_tools.DB_FILE", tmp_path / "test.db")

        from langmail.tools.db_tools import db_lookup_contact
        result = db_lookup_contact.invoke({"email": "unknown@example.com"})

        assert result["found"] is False
        assert result["email"] == "unknown@example.com"

    def test_inbox_stats_empty_db(self, tmp_path, monkeypatch):
        """db_get_inbox_stats should return zeros on empty database."""
        monkeypatch.setattr("langmail.tools.db_tools.DB_FILE", tmp_path / "test.db")

        from langmail.tools.db_tools import db_get_inbox_stats
        result = db_get_inbox_stats.invoke({})

        assert result["total_messages"] == 0
        assert result["total_contacts"] == 0
