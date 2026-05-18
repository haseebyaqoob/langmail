"""Security Assessor sub-agent.

Scans email content for phishing, social engineering, and other threats.
Returns a risk assessment with specific indicators found.
"""

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import tool

from langmail.prompts.templates import SECURITY_ASSESSOR_SYSTEM
from langmail.state import GmailAgentState
from langmail.tools.gmail_tools import gmail_read_thread


@tool(parse_docstring=True)
def think_security(reflection: str) -> str:
    """Pause to reason about security indicators before giving a verdict.

    Use this after reading the email to systematically check each indicator
    before concluding your risk assessment.

    Args:
        reflection: Your detailed analysis of the email's security indicators.
    """
    return f"Security analysis recorded: {reflection}"


def create_security_assessor(model_name: str) -> object:
    """Build the Security Assessor sub-agent.

    Args:
        model_name: Provider:model string.

    Returns:
        Compiled LangGraph agent ready to invoke.
    """
    model = init_chat_model(model=model_name, temperature=0.0)
    tools = [gmail_read_thread, think_security]

    agent = create_agent(
        model,
        tools,
        system_prompt=SECURITY_ASSESSOR_SYSTEM,
        state_schema=GmailAgentState,
    )

    return agent
