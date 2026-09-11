"""Agent adapter contracts and reference implementations."""

from .base import (
    AgentAdapter, AgentAdapterError, AgentContext, AgentMetadata, AgentProposal,
    AgentProviderError, AgentTimeoutError, MalformedAgentAction, UnsupportedAgentTool,
)
from .internal import InternalLLMAgentAdapter
from .reference import ReferenceAgentAdapter
from .external_strix import ExternalStrixAgentAdapter
from .registry import load_agent_adapter

__all__ = [
    "AgentAdapter", "AgentAdapterError", "AgentContext", "AgentMetadata", "AgentProposal",
    "AgentProviderError", "AgentTimeoutError", "MalformedAgentAction", "UnsupportedAgentTool",
    "InternalLLMAgentAdapter", "ReferenceAgentAdapter", "ExternalStrixAgentAdapter",
    "load_agent_adapter",
]
