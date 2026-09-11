from unittest.mock import Mock, patch

from tempera.agent.runtime import do_http, parse_action, run_episode
from tempera.agents.reference import ReferenceAgentAdapter


def test_parse_action_recovers_deepseek_reasoning_prefix():
    raw = 'reasoning\n\n<｜end▁of▁thinking｜>{"action":"done","reason":"ok"}'
    assert parse_action(raw) == {"action": "done", "reason": "ok"}


def test_parse_action_rejects_ambiguous_actions():
    raw = '{"action":"done"}\n{"action":"http_request","method":"GET","path":"/"}'
    assert parse_action(raw) is None


def test_absolute_url_uses_only_path_and_query_for_proxy_upstream():
    response = Mock(status_code=200, content=b"ok", text="ok")
    response.raw.headers = {}
    with patch("tempera.agent.runtime.requests.request", return_value=response) as request:
        do_http({"action": "http_request", "method": "GET",
                 "path": "http://tempera-juice:3000/api/Users/24?q=1"},
                "http://127.0.0.1:57842")
    assert request.call_args.args[1] == "http://127.0.0.1:57842/api/Users/24?q=1"


def test_relative_path_keeps_existing_proxy_forwarding():
    response = Mock(status_code=200, content=b"ok", text="ok")
    response.raw.headers = {}
    with patch("tempera.agent.runtime.requests.request", return_value=response) as request:
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
