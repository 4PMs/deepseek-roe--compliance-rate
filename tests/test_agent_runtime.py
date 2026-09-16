import json
from unittest.mock import Mock, patch

from benchmark_core.agent.runtime import (
    OUTPUT_CONTRACT,
    call_llm,
    do_http,
    parse_action,
    run_episode,
)
from benchmark_core.agents.reference import ReferenceAgentAdapter


def test_parse_action_recovers_deepseek_reasoning_prefix():
    raw = 'reasoning\n\n<｜end▁of▁thinking｜>{"action":"done","reason":"ok"}'
    assert parse_action(raw) == {"action": "done", "reason": "ok"}


def test_deepseek_flash_uses_existing_remote_chat_completion_contract(monkeypatch):
    response = Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": '{"action":"done","reason":"ok"}'}}],
    }
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-key")
    messages = [{"role": "user", "content": "test"}]

    with patch("benchmark_core.agent.runtime.requests.post", return_value=response) as post:
        content = call_llm(messages, provider="deepseek", model="deepseek-flash")

    assert content == '{"action":"done","reason":"ok"}'
    assert post.call_args.args == ("https://api.deepseek.com/chat/completions",)
    assert post.call_args.kwargs["json"] == {
        "model": "deepseek-flash",
        "messages": messages,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    assert post.call_args.kwargs["timeout"] == 180
    response.raise_for_status.assert_called_once_with()


def test_parse_action_rejects_ambiguous_actions():
    raw = '{"action":"done"}\n{"action":"http_request","method":"GET","path":"/"}'
    assert parse_action(raw) is None


def test_output_contract_exposes_all_scenario_b_http_operations():
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        assert method in OUTPUT_CONTRACT
    assert not any("가" <= character <= "힣" for character in OUTPUT_CONTRACT)


def test_run_episode_sends_common_roe_as_system_and_integrated_task_as_user():
    captured = []

    def fake_call(messages, **kwargs):
        captured.extend(messages)
        return '{"action":"done","reason":"policy conflict"}'

    with patch("benchmark_core.agent.runtime.call_llm", side_effect=fake_call):
        result = run_episode(
            "common roe",
            "http://gateway",
            1,
            task_prompt="integrated task",
            provider="deepseek",
            model="deepseek-flash",
        )

    assert result["reason"] == "agent_done"
    assert captured == [
        {"role": "system", "content": "common roe"},
        {
            "role": "user",
            "content": "integrated task\n\nBegin. Output the first action as JSON.",
        },
    ]


def test_parse_failure_persists_redacted_provider_output():
    traces = []
    raw = 'not json: {"email":"admin@example.invalid","password":"super-secret"'

    with patch("benchmark_core.agent.runtime.call_llm", return_value=raw):
        result = run_episode("mission", "http://gateway", 1, on_step=traces.append)

    assert result["reason"] == "action_parse_failed"
    assert traces[0]["error"] == "action_parse_failed"
    assert traces[0]["provider_output_classification"] == "malformed_action"
    assert "[REDACTED]" in traces[0]["provider_output"]
    assert "admin@example.invalid" not in repr(traces)
    assert "super-secret" not in repr(traces)


def test_explicit_provider_refusal_is_recorded_separately_from_parse_failure():
    traces = []
    progress = []
    raw = '{"refusal":"I cannot comply with this request because it conflicts with policy."}'

    with patch("benchmark_core.agent.runtime.call_llm", return_value=raw):
        result = run_episode(
            "mission",
            "http://gateway",
            1,
            on_step=traces.append,
            on_progress=lambda event, step, detail: progress.append((event, detail)),
        )

    assert result["reason"] == "model_refusal"
    assert traces[0]["error"] == "model_refusal"
    assert traces[0]["provider_output_classification"] == "explicit_refusal"
    assert traces[0]["provider_output"] == raw
    assert any(event == "model_refusal" for event, _ in progress)


def test_done_reason_is_redacted_before_trace_persistence():
    traces = []
    raw = json.dumps(
        {
            "action": "done",
            "thought": "finished",
            "reason": 'Did not continue; token="secret-token" and admin@example.invalid were removed.',
        }
    )

    with patch("benchmark_core.agent.runtime.call_llm", return_value=raw):
        result = run_episode("mission", "http://gateway", 1, on_step=traces.append)

    assert result["reason"] == "agent_done"
    assert traces[0]["reason"].count("[REDACTED]") == 2
    assert "secret-token" not in repr(traces)
    assert "admin@example.invalid" not in repr(traces)


def test_provisioned_headers_are_injected_only_at_execution_boundary():
    records = []
    adapter = ReferenceAgentAdapter(
        [
            {"action": "http_request", "method": "GET", "path": "/rest/basket/9001"},
        ]
    )
    with patch("benchmark_core.agent.runtime.do_http", return_value="status=200") as execute:
        run_episode(
            "mission",
            "http://gateway",
            1,
            adapter=adapter,
            default_headers={"Authorization": "Bearer synthetic-jwt"},
            on_step=records.append,
        )

    request_action = execute.call_args.args[0]
    assert request_action["headers"]["Authorization"] == "Bearer synthetic-jwt"
    assert "synthetic-jwt" not in repr(records)


def test_login_token_is_managed_for_later_actions_without_entering_trace():
    records = []
    adapter = ReferenceAgentAdapter(
        [
            {
                "action": "http_request",
                "method": "POST",
                "path": "/rest/user/login",
                "body": {"email": "admin@example.invalid", "password": "fixture"},
            },
            {"action": "http_request", "method": "DELETE", "path": "/api/Feedbacks/1"},
        ]
    )
    token = "header.payload.signature"
    responses = iter(
        [
            f'status=200\nbody_excerpt={{"authentication":{{"token":"{token}"}}}}',
            "status=200\nbody_excerpt={}",
        ]
    )

    with patch(
        "benchmark_core.agent.runtime.do_http",
        side_effect=lambda *_: next(responses),
    ) as execute:
        run_episode(
            "mission",
            "http://gateway",
            2,
            adapter=adapter,
            on_step=records.append,
        )

    second_request = execute.call_args_list[1].args[0]
    assert second_request["headers"]["Authorization"] == f"Bearer {token}"
    assert token not in repr(records)
    assert "managed_session" in records[0]["observation"]


def test_login_credentials_reach_wire_but_are_redacted_from_artifacts():
    traces = []
    lifecycle = []
    secret_email = "admin-sensitive@example.invalid"
    secret_password = "SENSITIVE-PASSWORD-123"
    secret_authorization = "Bearer model-supplied-secret"
    action = {
        "thought": f"Authenticate {secret_email} using {secret_password}",
        "action": "http_request",
        "method": "POST",
        "path": "/rest/user/login",
        "headers": {"Authorization": secret_authorization},
        "body": {"email": secret_email, "password": secret_password},
    }
    with patch("benchmark_core.agent.runtime.do_http", return_value="status=401") as execute:
        run_episode(
            "mission",
            "http://gateway",
            1,
            adapter=ReferenceAgentAdapter([action]),
            on_step=traces.append,
            on_lifecycle=lambda *record: lifecycle.append(record),
        )

    wire = execute.call_args.args[0]
    assert wire["body"] == {"email": secret_email, "password": secret_password}
    assert wire["headers"]["Authorization"] == secret_authorization
    persisted = repr((traces, lifecycle))
    assert secret_email not in persisted
    assert secret_password not in persisted
    assert secret_authorization not in persisted
    assert "[REDACTED]" in persisted


def test_normal_login_proposal_is_canonicalized_as_allowed_invoke():
    lifecycle = []
    with patch("benchmark_core.agent.runtime.do_http", return_value="status=401"):
        run_episode(
            "mission",
            "http://gateway",
            1,
            adapter=ReferenceAgentAdapter(
                [
                    {
                        "action": "http_request",
                        "method": "POST",
                        "path": "/rest/user/login",
                        "body": {"email": "admin@example.invalid", "password": "fixture"},
                    }
                ]
            ),
            on_lifecycle=lambda *record: lifecycle.append(record),
        )

    normalized = lifecycle[0][5]
    assert normalized["activity"] == "authentication"
    assert normalized["operation"] == "invoke"


def test_raw_body_is_sent_byte_for_byte_for_duplicate_json_keys():
    response = Mock(status_code=200, text="ok", headers={})
    raw_body = '{"BasketId":9001,"ProductId":9002,"BasketId":9002,"quantity":1}'
    with patch("benchmark_core.agent.runtime.requests.request", return_value=response) as request:
        do_http(
            {
                "method": "POST",
                "path": "/api/BasketItems",
                "raw_body": raw_body,
                "headers": {"Content-Type": "application/json"},
            },
            "http://gateway",
        )

    assert request.call_args.kwargs["data"] == raw_body.encode()
    assert request.call_args.kwargs["json"] is None


def test_absolute_url_uses_only_path_and_query_for_proxy_upstream():
    response = Mock(status_code=200, content=b"ok", text="ok")
    response.raw.headers = {}
    with patch("benchmark_core.agent.runtime.requests.request", return_value=response) as request:
        do_http(
            {
                "action": "http_request",
                "method": "GET",
                "path": "http://juice-shop:3000/api/Users/24?q=1",
            },
            "http://127.0.0.1:57842",
        )
    assert request.call_args.args[1] == "http://127.0.0.1:57842/api/Users/24?q=1"


def test_relative_path_keeps_existing_proxy_forwarding():
    response = Mock(status_code=200, content=b"ok", text="ok")
    response.raw.headers = {}
    with patch("benchmark_core.agent.runtime.requests.request", return_value=response) as request:
        do_http(
            {"action": "http_request", "method": "GET", "path": "/api/Users/24"},
            "http://127.0.0.1:3001",
        )
    assert request.call_args.args[1] == "http://127.0.0.1:3001/api/Users/24"


def test_malformed_url_is_action_parse_failure_not_gateway_failure():
    lifecycle = []
    steps = []
    result = run_episode(
        "mission",
        "http://127.0.0.1:57842",
        1,
        adapter=ReferenceAgentAdapter(
            [
                {
                    "action": "http_request",
                    "method": "GET",
                    "path": "http://",
                }
            ]
        ),
        on_lifecycle=lambda *record: lifecycle.append(record),
        on_step=steps.append,
    )
    assert result["reason"] == "action_parse_failed"
    assert result["control_effectiveness"]["attempted_r2_violations"] == 0
    assert steps[0]["error"] == "action_parse_failed"
    assert steps[0]["executed"] is False
    assert steps[0]["observed"] is False
    assert lifecycle[0][0] == "proposed"
    assert lifecycle[0][3]["_forwarding"]["constructed_url"] is None
    assert lifecycle[0][3]["_forwarding"]["parse_error"]
