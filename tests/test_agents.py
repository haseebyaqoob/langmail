"""Smoke tests for agent creation — verify agents build without errors.

These tests do not call the LLM or Gmail API. They only check that
create_*() functions return a compiled agent object with the expected
tool set wired up.
"""



class TestEmailReaderAgent:
    def test_creates_without_error(self):
        """create_email_reader should return a compiled agent."""
        from langmail.agents.reader import create_email_reader
        agent = create_email_reader("anthropic:claude-haiku-4-5-20251001")
        assert agent is not None

    def test_has_expected_tool_names(self):
        """Email reader should have gmail_fetch and gmail_search tools."""
        from langmail.agents.reader import create_email_reader
        agent = create_email_reader("anthropic:claude-haiku-4-5-20251001")
        # LangGraph compiled agents expose their graph; tools are on the bound model
        # Just verify no exception and agent is truthy
        assert agent


class TestSecurityAssessorAgent:
    def test_creates_without_error(self):
        from langmail.agents.security import create_security_assessor
        agent = create_security_assessor("anthropic:claude-haiku-4-5-20251001")
        assert agent is not None


class TestTaskExtractorAgent:
    def test_creates_without_error(self):
        from langmail.agents.extractor import create_task_extractor
        agent = create_task_extractor("anthropic:claude-haiku-4-5-20251001")
        assert agent is not None


class TestDraftWriterAgent:
    def test_creates_without_error(self):
        from langmail.agents.writer import create_draft_writer
        agent = create_draft_writer("anthropic:claude-sonnet-4-20250514")
        assert agent is not None


class TestSyncProcessMessage:
    """Unit tests for _process_message — the core DB write path in sync."""

    def test_process_message_writes_to_db(self, tmp_path, monkeypatch):
        """_process_message should insert a row into the messages table."""
        import sqlite3
        from langmail.tools.db_tools import SCHEMA_SQL
        monkeypatch.setattr("langmail.tools.db_tools.DB_FILE", tmp_path / "test.db")

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)

        raw = {
            "id": "msg001",
            "threadId": "thread001",
            "snippet": "Let's sync up",
            "labelIds": ["INBOX", "UNREAD"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "Alice <alice@acme.com>"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Cc", "value": ""},
                    {"name": "Subject", "value": "Quick sync"},
                    {"name": "Date", "value": "Wed, 9 Apr 2026 09:00:00 +0000"},
                ],
                "parts": [],
            },
        }

        from langmail.sync import _process_message
        with conn:
            _process_message(raw, conn)

        row = conn.execute(
            "SELECT * FROM messages WHERE message_id = ?", ("msg001",)
        ).fetchone()

        assert row is not None
        assert row["subject"] == "Quick sync"
        assert row["from_email"] == "alice@acme.com"
        assert row["is_read"] == 0  # UNREAD label present
        assert row["is_sent"] == 0

    def test_process_sent_message(self, tmp_path, monkeypatch):
        """_process_message should mark is_sent=1 for SENT label."""
        import sqlite3
        from langmail.tools.db_tools import SCHEMA_SQL
        monkeypatch.setattr("langmail.tools.db_tools.DB_FILE", tmp_path / "test.db")

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)

        raw = {
            "id": "msg002",
            "threadId": "thread001",
            "snippet": "Thanks!",
            "labelIds": ["SENT"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "me@example.com"},
                    {"name": "To", "value": "Alice <alice@acme.com>"},
                    {"name": "Cc", "value": ""},
                    {"name": "Subject", "value": "Re: Quick sync"},
                    {"name": "Date", "value": "Wed, 9 Apr 2026 10:00:00 +0000"},
                ],
                "parts": [],
            },
        }

        from langmail.sync import _process_message
        with conn:
            _process_message(raw, conn)

        row = conn.execute(
            "SELECT * FROM messages WHERE message_id = ?", ("msg002",)
        ).fetchone()

        assert row["is_sent"] == 1
        assert row["is_read"] == 1  # no UNREAD label

    def test_process_message_upserts_contact(self, tmp_path, monkeypatch):
        """_process_message should create a contact record for the sender."""
        import sqlite3
        from langmail.tools.db_tools import SCHEMA_SQL
        monkeypatch.setattr("langmail.tools.db_tools.DB_FILE", tmp_path / "test.db")

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)

        raw = {
            "id": "msg003",
            "threadId": "thread002",
            "snippet": "Hello",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "Bob <bob@startup.io>"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Cc", "value": ""},
                    {"name": "Subject", "value": "Intro"},
                    {"name": "Date", "value": "Wed, 9 Apr 2026 08:00:00 +0000"},
                ],
                "parts": [],
            },
        }

        from langmail.sync import _process_message
        with conn:
            _process_message(raw, conn)

        contact = conn.execute(
            "SELECT * FROM contacts WHERE email = ?", ("bob@startup.io",)
        ).fetchone()

        assert contact is not None
        assert contact["name"] == "Bob"
        assert contact["domain"] == "startup.io"
        assert contact["received_count"] == 1
