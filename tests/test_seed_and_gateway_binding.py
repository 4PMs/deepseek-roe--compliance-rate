from unittest.mock import Mock, patch

from benchmark_core.agent.runtime import call_llm, provider_seed_supported, run_episode
from benchmark_core.observe.gateway import ActionBindingRegistry, create_app
from benchmark_core.core.sequence import SequenceAllocator


def test_supported_provider_includes_seed_in_request():
    response = Mock()
    response.json.return_value = {"message": {"content": "{}"}}
    response.raise_for_status.return_value = None
    with patch("benchmark_core.agent.runtime.requests.post", return_value=response) as post:
        call_llm([], provider="ollama", model="test", seed=7)
    assert post.call_args.kwargs["json"]["options"]["seed"] == 7
    assert provider_seed_supported("ollama") is True


def test_unsupported_provider_reports_not_applied_and_does_not_send_seed():
    assert provider_seed_supported("deepseek") is False
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
    response.raise_for_status.return_value = None
    with (
        patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test"}),
        patch("benchmark_core.agent.runtime.requests.post", return_value=response) as post,
    ):
        call_llm([], provider="deepseek", model="test", seed=None)
    assert "seed" not in post.call_args.kwargs["json"]


def test_episode_result_records_unsupported_seed(monkeypatch):
    monkeypatch.setattr(
        "benchmark_core.agent.runtime.call_llm", lambda *args, **kwargs: '{"action":"done"}'
    )
    result = run_episode("mission", "gateway", 1, provider="deepseek", seed=7)
    assert result["reproducibility"] == {
        "seed_requested": 7,
        "seed_supported": False,
        "seed_applied": False,
        "status": "seed_not_supported",
        "reason": "provider_does_not_support_seed",
    }


def _app(registry, enforce=True, events=None):
    return create_app(
        "http://target.test",
        "run-1",
        "agent",
        events.append if events is not None else lambda event: None,
        action_registry=registry,
        enforce_policy=enforce,
        sequence_allocator=SequenceAllocator(),
    )


def test_valid_binding_is_observed_and_single_use():
    registry = ActionBindingRegistry()
    events = []
    token = registry.register("run-1", "action-1", "allow")
    client = _app(registry, events=events).test_client()
    with patch("benchmark_core.observe.gateway.requests.request") as request:
        request.return_value.status_code = 200
        request.return_value.content = b"ok"
        request.return_value.raw.headers = {}
        first = client.get("/", headers={"X-Action-Id": "action-1", "X-Correlation-Token": token})
        replay = client.get("/", headers={"X-Action-Id": "action-1", "X-Correlation-Token": token})
    assert first.status_code == 200
    assert replay.status_code == 403
    assert request.call_count == 1
    assert events[0].attributes["action_id"] == "action-1"


def test_spoofed_or_cross_action_token_fails_without_target_call():
    registry = ActionBindingRegistry()
    token = registry.register("run-1", "action-1", "allow")
    other = registry.register("run-1", "action-2", "allow")
    client = _app(registry).test_client()
    with patch("benchmark_core.observe.gateway.requests.request") as request:
        assert (
            client.get(
                "/", headers={"X-Action-Id": "spoof", "X-Correlation-Token": token}
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/", headers={"X-Action-Id": "action-1", "X-Correlation-Token": other}
            ).status_code
            == 403
        )
    assert request.call_count == 0


def test_direct_bypass_and_denied_action_are_blocked_when_enforced():
    registry = ActionBindingRegistry()
    denied = registry.register("run-1", "action-denied", "deny")
    client = _app(registry).test_client()
    with patch("benchmark_core.observe.gateway.requests.request") as request:
        assert client.get("/").status_code == 403
        assert (
            client.get(
                "/", headers={"X-Action-Id": "action-denied", "X-Correlation-Token": denied}
            ).status_code
            == 403
        )
    assert request.call_count == 0
