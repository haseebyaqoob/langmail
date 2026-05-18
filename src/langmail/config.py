"""Settings management for langmail.

Loads configuration from ~/.langmail/config.json with environment variable overrides.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env from cwd or any parent directory before settings are read
load_dotenv()


# Default data directory
DATA_DIR = Path.home() / ".langmail"
CONFIG_FILE = DATA_DIR / "config.json"
CREDENTIALS_FILE = DATA_DIR / "credentials.enc"
DB_FILE = DATA_DIR / "gmail.db"
CACHE_DIR = DATA_DIR / "cache" / "threads"
LOG_DIR = DATA_DIR / "logs"


class Settings(BaseModel):
    """All user-configurable settings for langmail."""

    # Models
    model: str = Field(
        default="anthropic:claude-sonnet-4-20250514",
        description="Main agent model (must support tool calls + structured output)",
    )
    summarization_model: str = Field(
        default="anthropic:claude-haiku-4-5-20251001",
        description="Lightweight model used for email summarization",
    )

    # Sync settings
    sync_depth_months: int = Field(
        default=6,
        description="How many months of email history to sync on first run",
    )

    # Cache settings
    cache_ttl_days: int = Field(
        default=7,
        description="How long to keep cached email bodies before auto-expiry",
    )
    encrypt_cache: bool = Field(
        default=True,
        description="Encrypt cached email content at rest",
    )

    # Context assembly
    max_context_threads: int = Field(
        default=5,
        description="Max recent threads to include as context for Draft Writer",
    )
    max_style_samples: int = Field(
        default=10,
        description="Max sent-email snippets to include for tone matching",
    )

    # Behavior
    auto_sync_on_check: bool = Field(
        default=True,
        description="Run incremental sync automatically on gmail-agent check",
    )
    poll_interval_minutes: int = Field(
        default=5,
        description="How often to poll for new emails in watch mode",
    )
    log_level: str = Field(default="info")


def load_settings() -> Settings:
    """Load settings from config file, with env var overrides."""
    data = {}

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            data = json.load(f)

    # Environment variable overrides
    if model := os.getenv("LANGMAIL_MODEL"):
        data["model"] = model
    if summ_model := os.getenv("LANGMAIL_SUMMARIZATION_MODEL"):
        data["summarization_model"] = summ_model

    return Settings(**data)


def save_settings(settings: Settings) -> None:
    """Persist settings to config file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(settings.model_dump(), f, indent=2)


def ensure_dirs() -> None:
    """Create all required local directories if they don't exist."""
    for directory in [DATA_DIR, CACHE_DIR, LOG_DIR]:
        directory.mkdir(parents=True, exist_ok=True)


# Module-level singleton
settings = load_settings()
