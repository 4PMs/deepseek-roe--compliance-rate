"""Agent adapter contracts and reference implementations."""

from .base import (
    AgentAdapter,
    AgentAdapterError,
    AgentContext,
    AgentMetadata,
    AgentProposal,
    AgentProviderError,
    AgentTimeoutError,
    MalformedAgentAction,
    UnsupportedAgentTool,
)
from .internal import InternalLLMAgentAdapter
from .reference import ReferenceAgentAdapter

__all__ = [
    "AgentAdapter",
    "AgentAdapterError",
    "AgentContext",
    "AgentMetadata",
    "AgentProposal",
    "AgentProviderError",
    "AgentTimeoutError",
    "MalformedAgentAction",
    "UnsupportedAgentTool",
    "InternalLLMAgentAdapter",
    "ReferenceAgentAdapter",
]
