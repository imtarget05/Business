"""Support Agent with tools (Phase 3, Task 3.3)."""

from agents.support.agent import SupportAgent, create_support_agent
from agents.support.tools import (
    CreateTicketTool,
    LookupCustomerTool,
    SendEmailReplyTool,
    create_support_tools,
)

__all__ = [
    "SupportAgent",
    "create_support_agent",
    "SendEmailReplyTool",
    "CreateTicketTool",
    "LookupCustomerTool",
    "create_support_tools",
]
