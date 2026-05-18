"""Tests for the email sync engine and context assembler."""

from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# Sync engine tests
# ---------------------------------------------------------------------------

class TestInitialSync:
    @patch("langmail.sync.get_gmail_service")
    @patch("langmail.sync.get_db")
    @patch("langmail.sync._save_history_id")
    @patch("langmail.sync._count_contacts", return_value=2)
    def test_initial_sync_returns_stats(self, mock_count, mock_save_hid, mock_get_db, mock_get_svc):
        """run_initial_sync should return messages_synced, contacts_found, duration_seconds."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        # messages().list() returns one page with two messages, then no next page
        mock_svc.users().messages().list().execute.return_value = {
            "messages": [{"id": "m1"}, {"id": "m2"}],
        }

        # Batch returns two full message dicts
        def fake_batch_execute():
            pass

        batch = MagicMock()
        callbacks = []

        def add_to_batch(request, callback=None):
            callbacks.append(callback)

        def execute_batch():
            raw_msgs = [
                {
                    "id": "m1",
                    "threadId": "t1",
                    "snippet": "Hello",
                    "labelIds": ["INBOX", "UNREAD"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Alice <alice@example.com>"},
                            {"name": "To", "value": "me@example.com"},
                            {"name": "Subject", "value": "Test"},
                            {"name": "Date", "value": "Wed, 9 Apr 2026 09:00:00 +0000"},
                        ]
                    },
                },
                {
                    "id": "m2",
                    "threadId": "t1",
                    "snippet": "World",
                    "labelIds": ["SENT"],
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "me@example.com"},
                            {"name": "To", "value": "Alice <alice@example.com>"},
                            {"name": "Subject", "value": "Re: Test"},
                            {"name": "Date", "value": "Wed, 9 Apr 2026 10:00:00 +0000"},
                        ]
                    },
                },
            ]
            for cb, msg in zip(callbacks, raw_msgs):
                cb(None, msg, None)

        batch.add.side_effect = add_to_batch
        batch.execute.side_effect = execute_batch
        mock_svc.new_batch_http_request.return_value = batch

        # Profile for history ID save
        mock_svc.users().getProfile().execute.return_value = {"historyId": "9999"}

        # Use a real in-memory SQLite db
        import sqlite3
        from langmail.tools.db_tools import SCHEMA_SQL
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        mock_get_db.return_value = conn

        from langmail.sync import run_initial_sync
        stats = run_initial_sync(months=1)

        assert stats["messages_synced"] == 2
        assert "contacts_found" in stats
        assert stats["duration_seconds"] >= 0
        mock_save_hid.assert_called_once_with("9999")


class TestIncrementalSync:
    @patch("langmail.sync.get_gmail_service")
    @patch("langmail.sync.get_db")
    @patch("langmail.sync._load_history_id")
    @patch("langmail.sync._save_history_id")
    def test_incremental_adds_message(
        self, mock_save, mock_load, mock_get_db, mock_get_svc
    ):
        """run_incremental_sync should process messagesAdded records."""
        mock_load.return_value = "5000"
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        mock_svc.users().history().list().execute.return_value = {
            "history": [
                {
                    "messagesAdded": [{"message": {"id": "new1"}}],
                }
            ],
            "historyId": "5001",
        }

        mock_svc.users().messages().get().execute.return_value = {
            "id": "new1",
            "threadId": "t1",
            "snippet": "New email",
            "labelIds": ["INBOX", "UNREAD"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "Bob <bob@example.com>"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Hello"},
                    {"name": "Date", "value": "Wed, 9 Apr 2026 12:00:00 +0000"},
                ]
            },
        }

        import sqlite3
        from langmail.tools.db_tools import SCHEMA_SQL
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        mock_get_db.return_value = conn

        from langmail.sync import run_incremental_sync
        stats = run_incremental_sync()

        assert stats["added"] == 1
        assert stats["removed"] == 0

    @patch("langmail.sync._load_history_id")
    @patch("langmail.sync.run_initial_sync")
    def test_falls_back_when_no_history_id(self, mock_initial, mock_load):
        """run_incremental_sync should fall back to initial sync if no history ID."""
        mock_load.return_value = None
        mock_initial.return_value = {"messages_synced": 0, "contacts_found": 0, "duration_seconds": 0.0}

        from langmail.sync import run_incremental_sync
        run_incremental_sync()

        mock_initial.assert_called_once()


class TestSyncHelpers:
    def test_parse_from(self):
        from langmail.sync import _parse_from
        name, email = _parse_from("Alice <alice@example.com>")
        assert name == "Alice"
        assert email == "alice@example.com"

    def test_parse_from_bare_email(self):
        from langmail.sync import _parse_from
        name, email = _parse_from("alice@example.com")
        assert email == "alice@example.com"

    def test_parse_date_valid(self):
        from langmail.sync import _parse_date
        result = _parse_date("Wed, 9 Apr 2026 09:00:00 +0000")
        assert "2026" in result
        assert "T" in result  # ISO 8601

    def test_parse_date_fallback(self):
        from langmail.sync import _parse_date
        result = _parse_date("not a real date")
        assert result  # should return something (utcnow fallback)


# ---------------------------------------------------------------------------
# Context assembler tests
# ---------------------------------------------------------------------------

class TestContextAssembler:
    @patch("langmail.context.get_gmail_service")
    @patch("langmail.context.settings")
    def test_assemble_returns_four_files(self, mock_settings, mock_svc, tmp_path, monkeypatch):
        """assemble_context should return exactly four context files."""
        import langmail.context as ctx_mod
        monkeypatch.setattr(ctx_mod, "CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr("langmail.tools.db_tools.DB_FILE", tmp_path / "test.db")
        mock_settings.cache_ttl_days = 7
        mock_settings.encrypt_cache = False
        mock_settings.max_context_threads = 5
        mock_settings.max_style_samples = 10

        mock_service = MagicMock()
        mock_svc.return_value = mock_service
        mock_service.users().threads().get().execute.return_value = {
            "messages": [
                {
                    "id": "m1",
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "From", "value": "Bob <bob@example.com>"},
                            {"name": "Date", "value": "Wed, 9 Apr 2026 09:00:00 +0000"},
                            {"name": "Subject", "value": "Proposal"},
                        ],
                        "body": {
                            "data": __import__("base64").urlsafe_b64encode(
                                b"Hi, here is the proposal."
                            ).decode().rstrip("="),
                        },
                    },
                }
            ]
        }

        from langmail.context import assemble_context
        files = assemble_context("thread123", "bob@example.com")

        assert set(files.keys()) == {
            "current_thread.md",
            "contact_profile.md",
            "recent_history.md",
            "writing_style.md",
        }
        assert "Proposal" in files["current_thread.md"]

    @patch("langmail.context.settings")
    def test_cache_hit_skips_api(self, mock_settings, tmp_path, monkeypatch):
        """assemble_context should use cache and not call Gmail API on second call."""
        import langmail.context as ctx_mod
        monkeypatch.setattr(ctx_mod, "CACHE_DIR", tmp_path / "cache")
        monkeypatch.setattr("langmail.tools.db_tools.DB_FILE", tmp_path / "test.db")
        mock_settings.cache_ttl_days = 7
        mock_settings.encrypt_cache = False
        mock_settings.max_context_threads = 5
        mock_settings.max_style_samples = 10

        # Pre-populate cache manually
        cache_data = {
            "thread_id": "t123",
            "subject": "Cached Subject",
            "messages": [
                {"message_id": "m1", "from": "bob@example.com", "date": "2026-04-09", "body": "Cached body"}
            ],
        }
        import json
        (tmp_path / "cache").mkdir(parents=True)
        (tmp_path / "cache" / "t123.json").write_text(json.dumps(cache_data))

        with patch("langmail.context.get_gmail_service") as mock_svc:
            from langmail.context import assemble_context
            files = assemble_context("t123", "bob@example.com")

        # Gmail service should NOT have been called (cache hit)
        mock_svc.assert_not_called()
        assert "Cached Subject" in files["current_thread.md"]

    def test_purge_expired_cache(self, tmp_path, monkeypatch):
        """purge_expired_cache should delete files older than TTL."""
        import langmail.context as ctx_mod

        monkeypatch.setattr(ctx_mod, "CACHE_DIR", tmp_path)
        monkeypatch.setattr(ctx_mod, "settings", MagicMock(cache_ttl_days=0))

        # Create a file that appears stale (mtime in the past)
        stale_file = tmp_path / "stale.json"
        stale_file.write_text('{"thread_id": "old"}')

        from langmail.context import purge_expired_cache
        deleted = purge_expired_cache()

        assert deleted == 1
        assert not stale_file.exists()
