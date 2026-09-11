"""Strix proposal-stream adapter.

The upstream Strix CLI executes its own tools in a Docker sandbox.  Tempera
therefore consumes an explicit JSON/JSONL proposal stream instead of allowing
that execution path to cross the benchmark boundary.  Each proposal is still
re-normalized and gated by Tempera's normal runtime.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Iterable, Mapping, Sequence

from .base import (
    AgentContext,
    AgentMetadata,
    AgentProposal,
    AgentProviderError,
    AgentTimeoutError,
    MalformedAgentAction,
)


class ExternalStrixAgentAdapter:
    """Adapt Strix tool-call records into Tempera ``AgentProposal`` objects."""

    def __init__(
        self,
        raw_actions: Iterable[Mapping[str, Any] | str] | None = None,
        *,
        command: Sequence[str] | None = None,
        timeout: float = 30.0,
        agent_version: str = "unknown",
        installation_source_version: str = "strix-agent@0.1.6",
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        if raw_actions is None and command is None:
            raise ValueError("raw_actions or command is required")
        self._raw_actions = list(raw_actions or [])
        self._command = list(command) if command is not None else None
        self._timeout = timeout
        self._index = 0
        self._metadata = AgentMetadata(
            adapter_name="external-strix",
            adapter_version="1",
            agent_name="strix",
            agent_version=agent_version,
            provider=provider,
            model=model,
            declared_capabilities=("http_request", "browser", "shell-proposal"),
            installation_source_version=installation_source_version,
        )

    def metadata(self) -> AgentMetadata:
        return self._metadata

    def prepare(self, context: AgentContext) -> None:
        self._index = 0
        if self._command is None:
            return
        try:
            completed = subprocess.run(
                self._command, capture_output=True, text=True, timeout=self._timeout, check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentTimeoutError(f"Strix command timed out after {self._timeout}s") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise AgentProviderError(f"Strix process unavailable: {exc}") from exc
        self._raw_actions = [line for line in completed.stdout.splitlines() if line.strip()]
        if not self._raw_actions:
            raise MalformedAgentAction("Strix process produced no proposal records")

    def next_action(self, context: AgentContext) -> AgentProposal:
        if self._index >= len(self._raw_actions):
            raise MalformedAgentAction("Strix proposal stream ended before an action")
        payload = self._raw_actions[self._index]
        self._index += 1
        raw_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise MalformedAgentAction(f"invalid Strix JSON: {exc.msg}") from exc
        if not isinstance(payload, Mapping):
            raise MalformedAgentAction("Strix proposal must be a JSON object")
        action = _to_tempera_action(payload)
        return AgentProposal(
            agent_id=self._metadata.agent_name,
            step=context.step,
            raw=action,
            tool=action.get("tool") if isinstance(action.get("tool"), Mapping) else None,
            arguments=action,
            reasoning=_reasoning(payload),
            raw_text=raw_text,
        )

    def receive_observation(self, observation: Any) -> None:
        return None

    def finalize(self) -> Mapping[str, Any]:
        return {"status": "completed", "proposals_consumed": self._index, "execution": "tempera-gateway"}


def _reasoning(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("reasoning", payload.get("thought"))
    return str(value) if value is not None else None


def _to_tempera_action(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Convert common Strix tool-call envelopes without trusting their semantics."""
    call = payload.get("tool_call")
    if isinstance(call, Mapping):
        name = call.get("name")
        arguments = call.get("arguments", {})
    else:
        name = payload.get("tool", payload.get("tool_name", payload.get("action")))
        arguments = payload.get("arguments", payload.get("input", {}))
    if isinstance(name, Mapping):
        name = name.get("name")
    if not isinstance(name, str) or not name:
        raise MalformedAgentAction("Strix proposal has no tool name")
    if not isinstance(arguments, Mapping):
        raise MalformedAgentAction("Strix tool arguments must be an object")
    action = dict(arguments)
    action["action"] = "done" if name in {"done", "finish"} else name
    if name not in {"done", "finish"}:
        action["tool"] = {"name": name, "type": name, "family": "transport" if name == "http_request" else "other"}
    if _reasoning(payload) is not None:
        action["thought"] = _reasoning(payload)
    return action
