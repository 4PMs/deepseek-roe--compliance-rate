"""Adapter for ROE Benchmark's existing internal LLM loop."""

from typing import Any, Callable, Mapping

from .base import AgentContext, AgentMetadata, AgentProposal, AgentProviderError, MalformedAgentAction


class InternalLLMAgentAdapter:
    """Preserve the current provider/message behavior behind AgentAdapter."""

    def __init__(self, *, mission: str, provider: str | None, model: str | None,
                 temperature: float | None, seed: int | None,
                 call: Callable[..., str], parse: Callable[[str], dict[str, Any] | None],
                 agent_version: str = "poc") -> None:
        self.mission = mission
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self._call = call
        self._parse = parse
        self._messages: list[dict[str, str]] = []
        self._last_raw: str | None = None
        self._metadata = AgentMetadata(
            adapter_name="internal-llm",
            adapter_version="1",
            agent_name="internal-agent",
            agent_version=agent_version,
            provider=provider,
            model=model,
            declared_capabilities=("http_request",),
        )

    def metadata(self) -> AgentMetadata:
        return self._metadata

    def prepare(self, context: AgentContext) -> None:
        self._messages = [
            {"role": "system", "content": self.mission},
            {"role": "user", "content": "Begin. Output the first action as JSON."},
        ]

    def next_action(self, context: AgentContext) -> AgentProposal:
        try:
            raw_text = self._call(
                self._messages, provider=self.provider, model=self.model,
                temperature=self.temperature, seed=self.seed,
            )
        except Exception as exc:
            raise AgentProviderError(f"{type(exc).__name__}: {exc}") from exc
        self._last_raw = raw_text
        action = self._parse(raw_text)
        if not action:
            raise MalformedAgentAction("provider output did not contain an action object")
        return AgentProposal(
            agent_id=self._metadata.agent_name,
            step=context.step,
            raw=action,
            tool={"name": action.get("action", "unknown")},
            arguments=action,
            reasoning=action.get("thought"),
            raw_text=raw_text,
        )

    def receive_observation(self, observation: Any) -> None:
        self._messages.extend([
            {"role": "assistant", "content": self._last_raw or ""},
            {"role": "user", "content": f"[Observation]\n{observation}\n\nOutput the next action as JSON."},
        ])

    def finalize(self) -> Mapping[str, Any]:
        return {"status": "completed"}
