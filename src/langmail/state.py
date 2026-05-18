"""LangGraph agent state schema for langmail.

The state is passed between all nodes in the agent graph.
Each sub-agent receives the relevant slice and returns updates via Command.
"""

from typing import Annotated, Literal, NotRequired

from langchain.agents import AgentState
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Typed sub-structures
# ---------------------------------------------------------------------------

class EmailSummary(TypedDict):
    """A processed email ready for agent decision-making."""
    id: str
    thread_id: str
    subject: str
    from_email: str
    from_name: str
    date: str
    snippet: str
    priority: Literal["urgent", "high", "medium", "low"]
    category: Literal["actionable", "fyi", "newsletter", "spam", "security_risk"]
    security_risk: Literal["safe", "caution", "danger"]


class Task(TypedDict):
    """An action item extracted from an email."""
    content: str
    source_email_id: str
    source_from: str
    due_date: NotRequired[str]
    priority: Literal["high", "medium", "low"]
    status: Literal["pending", "in_progress", "completed"]


class Draft(TypedDict):
    """A reply draft created by the Draft Writer agent."""
    draft_id: str
    thread_id: str
    to: str
    subject: str
    body_preview: str   # first 200 chars for display
    gmail_url: str


class SecurityAlert(TypedDict):
    """A security risk flagged by the Security Assessor."""
    email_id: str
    from_email: str
    subject: str
    risk_level: Literal["caution", "danger"]
    indicators: list[str]   # list of specific red flags found
    recommendation: str


# ---------------------------------------------------------------------------
# File system reducer
# (same pattern as deep-agents course — merges file dicts instead of replacing)
# ---------------------------------------------------------------------------

def _file_reducer(left: dict | None, right: dict | None) -> dict:
    """Merge two file dicts, right takes precedence on key conflicts."""
    if left is None:
        return right or {}
    if right is None:
        return left
    return {**left, **right}


# ---------------------------------------------------------------------------
# Main agent state
# ---------------------------------------------------------------------------

class GmailAgentState(AgentState):
    """Full agent state for the langmail supervisor and sub-agents.

    Inherits `messages` (with add_messages reducer) from AgentState.
    Adds email-specific fields for the full processing pipeline.
    """

    # Processed emails from current check
    emails: NotRequired[list[EmailSummary]]

    # Extracted action items
    tasks: NotRequired[list[Task]]

    # Reply drafts created this session
    drafts: NotRequired[list[Draft]]

    # Security alerts from current check
    security_alerts: NotRequired[list[SecurityAlert]]

    # Virtual file system for context offloading
    # (thread content, contact profiles, research notes)
    files: Annotated[NotRequired[dict[str, str]], _file_reducer]

    # Current email being processed (set by supervisor before delegation)
    current_email_id: NotRequired[str]
    current_thread_id: NotRequired[str]
    current_contact_email: NotRequired[str]
