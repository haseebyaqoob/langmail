"""Draft Writer sub-agent.

Writes context-aware email reply drafts by assembling:
- The current thread (what we're replying to)
- Contact profile (relationship history)
- Recent thread summaries (context)
- User's sent email samples (tone matching)

Creates a Gmail draft via the API. Never sends automatically.
"""

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool

from langmail.prompts.templates import DRAFT_WRITER_SYSTEM
from langmail.state import GmailAgentState
from langmail.tools.db_tools import (
    db_get_sent_snippets,
    db_get_thread_summaries,
    db_lookup_contact,
)
from langmail.tools.gmail_tools import gmail_create_draft, gmail_read_thread


@tool(parse_docstring=True)
def think_draft(reflection: str) -> str:
    """Pause to reason about tone, content, and style before drafting.

    Use this to:
    - Analyze the relationship and appropriate tone
    - Check that you're addressing exactly what the user asked
    - Verify the draft matches the user's writing style samples

    Args:
        reflection: Your analysis before writing the draft.
    """
    return f"Draft analysis recorded: {reflection}"


def create_draft_writer(model_name: str) -> object:
    """Build the Draft Writer sub-agent.

    Args:
        model_name: Provider:model string. Use a capable model (Sonnet or better)
                    since draft quality matters.

    Returns:
        Compiled LangGraph agent ready to invoke.
    """
    model = init_chat_model(model=model_name, temperature=0.3)  # slight creativity

    # Draft Writer gets all context tools + the draft creation tool
    tools = [
        gmail_read_thread,        # read the thread being replied to
        db_lookup_contact,        # get contact profile
        db_get_thread_summaries,  # get recent thread history
        db_get_sent_snippets,     # get tone samples
        gmail_create_draft,       # create the actual draft
        think_draft,              # reasoning tool
    ]

    agent = create_agent(
        model,
        tools,
        system_prompt=DRAFT_WRITER_SYSTEM,
        state_schema=GmailAgentState,
    )

    return agent
