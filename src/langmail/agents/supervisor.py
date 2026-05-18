"""Supervisor agent — orchestrates all sub-agents.

Delegates to sub-agents via the task() tool pattern from the deep-agents course.
Each sub-agent runs in isolated context to prevent confusion and context clash.
"""

from datetime import datetime
from typing import Annotated

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from langmail.agents.extractor import create_task_extractor
from langmail.agents.reader import create_email_reader
from langmail.agents.security import create_security_assessor
from langmail.agents.writer import create_draft_writer
from langmail.config import settings
from langmail.context import assemble_context
from langmail.prompts.templates import SUPERVISOR_SYSTEM
from langmail.state import GmailAgentState
from langmail.tools.db_tools import (
    db_get_inbox_stats,
    db_get_sent_snippets,
    db_get_thread_summaries,
    db_lookup_contact,
    db_search_contacts,
)
from langmail.tools.gmail_tools import gmail_fetch, gmail_label, gmail_search


# Sub-agent registry — built once at startup
def _build_subagent_registry(model_name: str, summarization_model: str) -> dict:
    """Build all sub-agents and return them in a registry dict."""
    return {
        "email-reader": create_email_reader(summarization_model),
        "security-assessor": create_security_assessor(summarization_model),
        "task-extractor": create_task_extractor(summarization_model),
        "draft-writer": create_draft_writer(model_name),
    }


SUBAGENT_DESCRIPTIONS = {
    "email-reader": "Fetches and classifies unread emails by priority and category",
    "security-assessor": "Scans an email for phishing, social engineering, and security threats",
    "task-extractor": "Extracts action items and deadlines from an email thread",
    "draft-writer": "Writes a context-aware reply draft based on thread history and user instruction",
}


def _create_task_tool(registry: dict) -> BaseTool:
    """Create the task() delegation tool with the given sub-agent registry."""

    @tool(parse_docstring=True)
    def task(
        description: str,
        subagent_type: str,
        state: Annotated[GmailAgentState, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Delegate a task to a specialized sub-agent with isolated context.

        Each sub-agent starts fresh with only the task description — no parent history.
        This prevents context clash and keeps sub-agents focused.

        Args:
            description: Clear, complete description of what the sub-agent should do.
                         Include all relevant context since the sub-agent cannot see
                         the parent conversation.
            subagent_type: Which sub-agent to use. One of: email-reader,
                           security-assessor, task-extractor, draft-writer.
        """
        if subagent_type not in registry:
            valid = list(registry.keys())
            return f"Error: unknown subagent_type '{subagent_type}'. Valid options: {valid}"

        sub_agent = registry[subagent_type]

        # Isolated context — sub-agent only sees the task description
        isolated_state = dict(state)
        isolated_state["messages"] = [{"role": "user", "content": description}]

        result = sub_agent.invoke(isolated_state)

        # Merge any file updates from sub-agent back to parent state
        return Command(
            update={
                "files": result.get("files", {}),
                "messages": [
                    ToolMessage(
                        result["messages"][-1].content,
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    return task


def _create_load_context_tool() -> BaseTool:
    """Create the load_draft_context() tool that pre-assembles reply context."""

    @tool(parse_docstring=True)
    def load_draft_context(
        thread_id: str,
        contact_email: str,
        state: Annotated[GmailAgentState, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command:
        """Pre-load context files for the Draft Writer before delegating.

        Assembles current_thread.md, contact_profile.md, recent_history.md,
        and writing_style.md into state files. Thread bodies are cached
        locally (encrypted) to avoid repeat API calls.
        Call this before delegating to draft-writer.

        Args:
            thread_id: Gmail thread ID being replied to.
            contact_email: Email address of the primary contact.
        """
        try:
            files = assemble_context(thread_id, contact_email)
            file_list = ", ".join(files.keys())
            return Command(
                update={
                    "files": files,
                    "messages": [
                        ToolMessage(
                            f"Context loaded: {file_list}",
                            tool_call_id=tool_call_id,
                        )
                    ],
                }
            )
        except Exception as e:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            f"Context load failed: {e}. Draft writer will fetch context via tools.",
                            tool_call_id=tool_call_id,
                        )
                    ]
                }
            )

    return load_draft_context


@tool(parse_docstring=True)
def think_tool(reflection: str) -> str:
    """Pause to reason about the current state and plan next steps.

    Use after receiving sub-agent results to decide what to do next.

    Args:
        reflection: Your analysis of what you've learned and what to do next.
    """
    return f"Reflection recorded: {reflection}"


def create_supervisor() -> object:
    """Build the supervisor agent with all sub-agents registered.

    Returns:
        Compiled LangGraph supervisor agent.
    """
    registry = _build_subagent_registry(
        model_name=settings.model,
        summarization_model=settings.summarization_model,
    )

    task_tool = _create_task_tool(registry)
    load_context_tool = _create_load_context_tool()

    subagent_desc_str = "\n".join(
        f"- {name}: {desc}" for name, desc in SUBAGENT_DESCRIPTIONS.items()
    )

    system_prompt = SUPERVISOR_SYSTEM.format(
        subagent_descriptions=subagent_desc_str,
        date=datetime.now().strftime("%A, %B %d, %Y"),
    )

    model = init_chat_model(model=settings.model, temperature=0.0)

    supervisor_tools = [
        task_tool,
        load_context_tool,
        think_tool,
        gmail_fetch,
        gmail_search,
        gmail_label,
        db_get_inbox_stats,
        db_lookup_contact,
        db_search_contacts,
        db_get_thread_summaries,
        db_get_sent_snippets,
    ]

    agent = create_agent(
        model,
        supervisor_tools,
        system_prompt=system_prompt,
        state_schema=GmailAgentState,
    )

    return agent
