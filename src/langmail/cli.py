"""Langmail CLI — user-facing commands built with Typer + Rich.

Commands:
    setup    — OAuth2 setup wizard
    check    — triage unread emails
    draft    — draft a reply to an email
    tasks    — show extracted action items
    watch    — continuous polling mode
    sync     — sync email history
    privacy  — manage locally stored data
    config   — view/edit settings
"""

import os
import sys
import time
from pathlib import Path
from typing import Optional

# Force UTF-8 output on Windows so Rich spinner/box characters render correctly
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr.encoding != "utf-8":
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from langmail.config import ensure_dirs, load_settings, save_settings, settings

app = typer.Typer(
    name="langmail",
    help="Multi-agent Gmail assistant powered by LangChain and LangGraph.",
    no_args_is_help=True,
)

console = Console()


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

@app.command()
def setup(
    credentials_file: str = typer.Option(
        "credentials.json",
        "--credentials",
        "-c",
        help="Path to the credentials.json downloaded from Google Cloud Console",
    )
):
    """Set up Gmail OAuth2 authentication.

    You'll need a credentials.json from Google Cloud Console.
    See docs/SETUP.md for step-by-step instructions.
    """
    ensure_dirs()

    if not Path(credentials_file).exists():
        rprint(
            Panel(
                "[red]credentials.json not found.[/red]\n\n"
                "1. Go to [link=https://console.cloud.google.com]console.cloud.google.com[/link]\n"
                "2. Create a project and enable the Gmail API\n"
                "3. Create OAuth2 credentials (Desktop app type)\n"
                "4. Download credentials.json and place it here\n"
                "5. Re-run: [bold]langmail setup[/bold]",
                title="Setup Required",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    from langmail.auth import run_oauth_flow

    console.print("[yellow]Opening browser for Gmail authorization...[/yellow]")
    try:
        run_oauth_flow(credentials_file)
        rprint(
            Panel(
                "[green]Gmail authorization successful![/green]\n\n"
                "Your credentials are encrypted and stored locally.\n"
                "Run [bold]langmail check[/bold] to see your inbox.",
                title="Setup Complete",
                border_style="green",
            )
        )
    except Exception as e:
        rprint(f"[red]Authorization failed: {e}[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

@app.command()
def check(
    urgent_only: bool = typer.Option(False, "--urgent", help="Show urgent emails only"),
    max_emails: int = typer.Option(20, "--max", "-n", help="Max emails to fetch"),
):
    """Check and triage your unread emails."""
    _require_auth()

    console.print("[bold blue]Checking inbox...[/bold blue]")

    from langmail.agents.supervisor import create_supervisor

    supervisor = create_supervisor()

    instruction = "Check my unread emails, classify them by priority, and scan each for security threats."
    if urgent_only:
        instruction += " Only show me urgent and high priority emails."
    instruction += f" Fetch up to {max_emails} emails."

    result = supervisor.invoke({
        "messages": [{"role": "user", "content": instruction}],
        "files": {},
    })

    _display_result(result)


# ---------------------------------------------------------------------------
# draft
# ---------------------------------------------------------------------------

@app.command()
def draft(
    email_id: str = typer.Argument(..., help="The email ID to reply to (from 'langmail check')"),
    instruction: Optional[str] = typer.Option(
        None, "--instruction", "-i",
        help="What to say in the reply (e.g. 'accept the meeting, suggest Tuesday 2pm')"
    ),
):
    """Draft a reply to an email. Saves to Gmail drafts — never sends automatically."""
    _require_auth()

    if not instruction:
        instruction = typer.prompt("What should the reply say?")

    console.print(f"[bold blue]Drafting reply to email {email_id}...[/bold blue]")

    from langmail.agents.supervisor import create_supervisor

    supervisor = create_supervisor()

    result = supervisor.invoke({
        "messages": [{
            "role": "user",
            "content": (
                f"Draft a reply to email ID {email_id}. "
                f"Instruction: {instruction}. "
                "Use the draft-writer sub-agent with full context."
            ),
        }],
        "files": {},
        "current_email_id": email_id,
    })

    _display_result(result)
    rprint(Panel(
        "[green]Draft saved to Gmail.[/green]\n"
        "Open [link=https://mail.google.com/mail/u/0/#drafts]Gmail Drafts[/link] to review and send.",
        border_style="green",
    ))


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------

@app.command()
def tasks():
    """Show all action items extracted from recent emails."""
    _require_auth()

    console.print("[bold blue]Loading action items...[/bold blue]")

    from langmail.agents.supervisor import create_supervisor

    supervisor = create_supervisor()

    result = supervisor.invoke({
        "messages": [{
            "role": "user",
            "content": "Show me all action items extracted from my recent emails.",
        }],
        "files": {},
    })

    _display_result(result)


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

@app.command()
def sync(
    incremental: bool = typer.Option(
        False, "--incremental", help="Only sync recent changes (faster)"
    ),
    months: int = typer.Option(
        settings.sync_depth_months,
        "--months", "-m",
        help="How many months of history to sync",
    ),
):
    """Sync email history to local index for faster context retrieval."""
    _require_auth()

    mode = "incremental" if incremental else f"full ({months} months)"
    console.print(f"[bold blue]Starting {mode} sync...[/bold blue]")
    console.print("[yellow]This may take a few minutes on first run.[/yellow]")

    from langmail.sync import run_incremental_sync, run_initial_sync

    try:
        if incremental:
            stats = run_incremental_sync()
            rprint(
                Panel(
                    f"[green]Incremental sync complete.[/green]\n"
                    f"Added: {stats['added']}  Updated: {stats['updated']}  "
                    f"Removed: {stats['removed']}  "
                    f"({stats['duration_seconds']}s)",
                    border_style="green",
                )
            )
        else:
            stats = run_initial_sync(months=months)
            rprint(
                Panel(
                    f"[green]Full sync complete.[/green]\n"
                    f"Messages indexed: {stats['messages_synced']}\n"
                    f"Contacts found:   {stats['contacts_found']}\n"
                    f"Duration:         {stats['duration_seconds']}s",
                    border_style="green",
                )
            )
    except Exception as e:
        rprint(f"[red]Sync failed: {e}[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------

@app.command()
def watch(
    interval: int = typer.Option(
        settings.poll_interval_minutes,
        "--interval", "-i",
        help="Polling interval in minutes",
    ),
):
    """Continuously monitor Gmail and alert on urgent emails."""
    _require_auth()

    rprint(Panel(
        f"[bold green]Watching inbox every {interval} minutes.[/bold green]\n"
        "Press Ctrl+C to stop.",
        border_style="green",
    ))

    try:
        while True:
            console.print(f"\n[dim]{_now()} — Checking inbox...[/dim]")
            check(urgent_only=True, max_emails=10)
            console.print(f"[dim]Next check in {interval} minutes. Ctrl+C to stop.[/dim]")
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        rprint("\n[yellow]Watch stopped.[/yellow]")


# ---------------------------------------------------------------------------
# privacy
# ---------------------------------------------------------------------------

privacy_app = typer.Typer(help="Manage locally stored data.")
app.add_typer(privacy_app, name="privacy")


@privacy_app.command("show")
def privacy_show():
    """Show what data is stored locally."""
    from langmail.config import CACHE_DIR, DB_FILE

    table = Table(title="Local Data", border_style="blue")
    table.add_column("Location", style="cyan")
    table.add_column("Purpose")
    table.add_column("Size")

    def _size(path: Path) -> str:
        if not path.exists():
            return "[dim]not created[/dim]"
        if path.is_file():
            return f"{path.stat().st_size / 1024:.1f} KB"
        total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return f"{total / 1024:.1f} KB"

    table.add_row(str(DB_FILE), "Email index (metadata only, no bodies)", _size(DB_FILE))
    table.add_row(str(CACHE_DIR), "Cached email bodies (encrypted, auto-expire)", _size(CACHE_DIR))

    console.print(table)


@privacy_app.command("clear-cache")
def privacy_clear_cache():
    """Delete all cached email content (keeps the metadata index)."""
    from langmail.config import CACHE_DIR

    if not CACHE_DIR.exists():
        rprint("[yellow]Cache directory does not exist — nothing to clear.[/yellow]")
        return

    count = sum(1 for _ in CACHE_DIR.rglob("*.json"))
    if count == 0:
        rprint("[yellow]Cache is already empty.[/yellow]")
        return

    confirm = typer.confirm(f"Delete {count} cached email files?")
    if confirm:
        for f in CACHE_DIR.rglob("*.json"):
            f.unlink()
        rprint(f"[green]OK: Deleted {count} cached files.[/green]")


@privacy_app.command("clear-all")
def privacy_clear_all():
    """Delete ALL locally stored data and start fresh."""
    from langmail.config import DATA_DIR

    confirm = typer.confirm(
        "[red]This will delete all local data including your Gmail credentials.[/red]\n"
        "You will need to run 'langmail setup' again. Continue?"
    )
    if confirm:
        import shutil
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        rprint("[green]OK: All local data deleted.[/green]")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

@app.command()
def config(
    key: Optional[str] = typer.Argument(None, help="Setting key to view or set"),
    value: Optional[str] = typer.Argument(None, help="New value for the setting"),
):
    """View or edit langmail settings."""
    current = load_settings()

    if key is None:
        # Show all settings
        table = Table(title="Current Settings", border_style="blue")
        table.add_column("Key", style="cyan")
        table.add_column("Value")
        for k, v in current.model_dump().items():
            table.add_row(k, str(v))
        console.print(table)
        return

    if value is None:
        # Show single setting
        val = getattr(current, key, None)
        if val is None:
            rprint(f"[red]Unknown setting: {key}[/red]")
            raise typer.Exit(1)
        rprint(f"{key} = {val}")
        return

    # Set a value
    data = current.model_dump()
    field_type = type(data.get(key))
    try:
        if field_type is bool:
            data[key] = value.lower() in ("true", "1", "yes")
        elif field_type is int:
            data[key] = int(value)
        else:
            data[key] = value
    except (ValueError, KeyError):
        rprint(f"[red]Invalid value '{value}' for setting '{key}'[/red]")
        raise typer.Exit(1)

    from langmail.config import Settings
    save_settings(Settings(**data))
    rprint(f"[green]OK: {key} = {data[key]}[/green]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_auth() -> None:
    """Exit with a helpful message if not authenticated."""
    from langmail.auth import is_authenticated
    if not is_authenticated():
        rprint(
            "[red]Not authenticated.[/red] Run [bold]langmail setup[/bold] first."
        )
        raise typer.Exit(1)


def _display_result(result: dict) -> None:
    """Print the last AI message from a supervisor result."""
    messages = result.get("messages", [])
    if not messages:
        rprint("[yellow]No response from agent.[/yellow]")
        return

    last = messages[-1]
    content = last.content if hasattr(last, "content") else str(last)
    rprint(Panel(content, title="[bold blue]langmail[/bold blue]", border_style="blue"))


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


if __name__ == "__main__":
    app()
