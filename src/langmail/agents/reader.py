"""Email Reader sub-agent.

Fetches unread emails and classifies each one by priority and category.
Returns a structured list of EmailSummary objects to the supervisor.
"""


from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from pydantic import BaseModel

from langmail.prompts.templates import EMAIL_READER_SYSTEM
from langmail.state import GmailAgentState
from langmail.tools.gmail_tools import gmail_fetch, gmail_search


class EmailClassification(BaseModel):
    """Structured classification result for a single email."""
    id: str
    priority: str
    category: str
    reason: str


class ClassificationResult(BaseModel):
    """Batch classification result for all fetched emails."""
    classifications: list[EmailClassification]


def create_email_reader(model_name: str) -> object:
    """Build the Email Reader sub-agent.

    Args:
        model_name: Provider:model string (e.g. 'anthropic:claude-haiku-4-5-20251001')

    Returns:
        Compiled LangGraph agent ready to invoke.
    """
    model = init_chat_model(model=model_name, temperature=0.0)
    tools = [gmail_fetch, gmail_search]

    agent = create_agent(
        model,
        tools,
        system_prompt=EMAIL_READER_SYSTEM,
        state_schema=GmailAgentState,
    )

    return agent
