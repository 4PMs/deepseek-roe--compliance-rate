"""Minimal agent selection boundary; evaluator and gate remain agent-agnostic."""

from __future__ import annotations

from collections.abc import Sequence

from .base import AgentAdapter
from .external_strix import ExternalStrixAgentAdapter


def load_agent_adapter(
    specification: str,
    *,
    command: Sequence[str] | None = None,
    agent_version: str = "unknown",
    provider: str | None = None,
    model: str | None = None,
) -> AgentAdapter | None:
    """Load the one supported external adapter without a runner agent chain."""
    if specification == "internal":
        return None
    if specification == "external:strix":
        return ExternalStrixAgentAdapter(
            raw_actions=[] if command is None else None,
            command=command,
            agent_version=agent_version,
            provider=provider,
            model=model,
        )
    raise ValueError(f"unsupported agent specification: {specification}")
