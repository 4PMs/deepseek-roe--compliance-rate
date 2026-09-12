from unittest.mock import Mock, patch

from benchmark_core.agent.runtime import OUTPUT_CONTRACT, do_http, parse_action, run_episode
from benchmark_core.agents.reference import ReferenceAgentAdapter


def test_parse_action_recovers_deepseek_reasoning_prefix():
    raw = 'reasoning\n\n<｜end▁of▁thinking｜>{"action":"done","reason":"ok"}'
    assert parse_action(raw) == {"action": "done", "reason": "ok"}


def test_parse_action_rejects_ambiguous_actions():
    raw = '{"action":"done"}\n{"action":"http_request","method":"GET","path":"/"}'
    assert parse_action(raw) is None


def test_output_contract_exposes_all_scenario_b_http_operations():
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        assert method in OUTPUT_CONTRACT
    assert not any("가" <= character <= "힣" for character in OUTPUT_CONTRACT)


def test_provisioned_headers_are_injected_only_at_execution_boundary():
    records = []
    adapter = ReferenceAgentAdapter([
        {"action": "http_request", "method": "GET", "path": "/rest/basket/9001"},
    ])
    with patch("benchmark_core.agent.runtime.do_http", return_value="status=200") as execute:
        run_episode(
            "mission", "http://gateway", 1,
            adapter=adapter,
            default_headers={"Authorization": "Bearer synthetic-jwt"},
            on_step=records.append,
        )

    request_action = execute.call_args.args[0]
    assert request_action["headers"]["Authorization"] == "Bearer synthetic-jwt"
    assert "synthetic-jwt" not in repr(records)


def test_raw_body_is_sent_byte_for_byte_for_duplicate_json_keys():
    response = Mock(status_code=200, text="ok", headers={})
    raw_body = '{"BasketId":9001,"ProductId":9002,"BasketId":9002,"quantity":1}'
    with patch("benchmark_core.agent.runtime.requests.request", return_value=response) as request:
        do_http({
            "method": "POST",
            "path": "/api/BasketItems",
            "raw_body": raw_body,
            "headers": {"Content-Type": "application/json"},
        }, "http://gateway")

    assert request.call_args.kwargs["data"] == raw_body.encode()
    assert request.call_args.kwargs["json"] is None


def test_absolute_url_uses_only_path_and_query_for_proxy_upstream():
    response = Mock(status_code=200, content=b"ok", text="ok")
    response.raw.headers = {}
    with patch("benchmark_core.agent.runtime.requests.request", return_value=response) as request:
        do_http({"action": "http_request", "method": "GET",
                 "path": "http://juice-shop:3000/api/Users/24?q=1"},
                "http://127.0.0.1:57842")
    assert request.call_args.args[1] == "http://127.0.0.1:57842/api/Users/24?q=1"


def test_relative_path_keeps_existing_proxy_forwarding():
    response = Mock(status_code=200, content=b"ok", text="ok")
    response.raw.headers = {}
    with patch("benchmark_core.agent.runtime.requests.request", return_value=response) as request:
        do_http({"action": "http_request", "method": "GET", "path": "/api/Users/24"},
                "http://127.0.0.1:3001")
    assert request.call_args.args[1] == "http://127.0.0.1:3001/api/Users/24"


def test_malformed_url_is_action_parse_failure_not_gateway_failure():
    lifecycle = []
    steps = []
    result = run_episode(
        "mission", "http://127.0.0.1:57842", 1,
        adapter=ReferenceAgentAdapter([{
            "action": "http_request", "method": "GET", "path": "http://",
        }]),
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
