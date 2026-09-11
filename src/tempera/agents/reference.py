"""Deterministic scripted adapter for contract and pipeline tests."""

from typing import Any, Iterable, Mapping

from .base import AgentContext, AgentMetadata, AgentProposal


class ReferenceAgentAdapter:
    """Return scripted raw actions; not intended as an attack benchmark agent."""

    def __init__(self, actions: Iterable[Mapping[str, Any]], *, agent_name: str = "reference-agent") -> None:
        self._actions = [dict(action) for action in actions]
        self._index = 0
        self._metadata = AgentMetadata(
            adapter_name="reference-scripted",
            adapter_version="1",
            agent_name=agent_name,
            agent_version="1",
            provider="none",
            model=None,
            declared_capabilities=("http_request",),
        )

    def metadata(self) -> AgentMetadata:
        return self._metadata

    def prepare(self, context: AgentContext) -> None:
        self._index = 0

    def next_action(self, context: AgentContext) -> AgentProposal:
        action = self._actions[min(self._index, len(self._actions) - 1)]
        self._index += 1
        return AgentProposal(
            agent_id=self._metadata.agent_name,
            step=context.step,
            raw=action,
            tool=action.get("tool") if isinstance(action.get("tool"), Mapping)
            else {"name": action.get("action", "unknown")},
            arguments=action.get("arguments", action),
            reasoning=action.get("thought"),
        )

    def receive_observation(self, observation: Any) -> None:
        return None

    def finalize(self) -> Mapping[str, Any]:
        return {"status": "completed", "actions_consumed": self._index}
