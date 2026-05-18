"""Task Extractor sub-agent.

Reads email threads and extracts actionable tasks, deadlines, and commitments.
Writes extracted tasks to the GmailAgentState todos list.
"""

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool

from langmail.prompts.templates import TASK_EXTRACTOR_SYSTEM
from langmail.state import GmailAgentState
from langmail.tools.gmail_tools import gmail_read_thread


@tool(parse_docstring=True)
def think_tasks(reflection: str) -> str:
    """Pause to reason about what tasks are genuinely actionable.

    Use this before finalizing your task list to avoid over-extracting
    vague or already-completed items.

    Args:
        reflection: Your analysis of what requires action vs. what doesn't.
    """
    return f"Task analysis recorded: {reflection}"


def create_task_extractor(model_name: str) -> object:
    """Build the Task Extractor sub-agent.

    Args:
        model_name: Provider:model string.

    Returns:
        Compiled LangGraph agent ready to invoke.
    """
    model = init_chat_model(model=model_name, temperature=0.0)
    tools = [gmail_read_thread, think_tasks]

    agent = create_agent(
        model,
        tools,
        system_prompt=TASK_EXTRACTOR_SYSTEM,
        state_schema=GmailAgentState,
    )

    return agent
