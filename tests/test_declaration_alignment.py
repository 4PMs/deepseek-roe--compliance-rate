from datetime import datetime, timezone

from benchmark_core.core.event import Event
from benchmark_core.evaluate.declare import evaluate_declarations


def web(seq, action_id, path):
    return Event(
        schema_version="0.2",
        run_id="run",
        timestamp=datetime.now(timezone.utc),
        actor="agent",
        source="gateway",
        kind="web",
        action="request",
        target=f"http://target{path}",
        seq=seq,
        attributes={"action_id": action_id, "method": "GET", "path": path, "status": 200},
    )


def test_declarations_join_by_action_id_before_stream_position():
    traces = [
        {
            "step": 1,
            "action_id": "action-1",
            "action": "http_request",
            "method": "GET",
            "path": "/a",
        },
        {
            "step": 2,
            "action_id": "action-2",
            "action": "http_request",
            "method": "GET",
            "path": "/b",
        },
    ]
    events = [web(1, "action-2", "/b"), web(2, "action-1", "/a")]

    result = evaluate_declarations(traces, events)

    assert result.confirmed == 2
    assert result.mismatch == 0
    assert [(item.trace_action_id, item.event_action_id) for item in result.matches] == [
        ("action-1", "action-1"),
        ("action-2", "action-2"),
    ]


def test_extra_covert_event_does_not_shift_later_declared_alignment():
    traces = [
        {
            "step": 1,
            "action_id": "action-1",
            "action": "http_request",
            "method": "GET",
            "path": "/same",
        },
        {
            "step": 2,
            "action_id": "action-2",
            "action": "http_request",
            "method": "GET",
            "path": "/same",
        },
    ]
    events = [
        web(1, "action-1", "/same"),
        web(2, "covert-1", "/extra"),
        web(3, "action-2", "/same"),
    ]

    result = evaluate_declarations(traces, events)

    assert result.confirmed == 2
    assert result.covert == 1
    assert result.mismatch == 0
    assert result.matches[-1].classification == "covert"
    assert result.matches[-1].event_action_id == "covert-1"


def test_missing_action_id_falls_back_to_unique_canonical_target():
    traces = [
        {"step": 1, "action": "http_request", "method": "GET", "path": "/a"},
        {"step": 2, "action": "http_request", "method": "GET", "path": "/b"},
    ]
    events = [web(1, "observed-b", "/b"), web(2, "observed-a", "/a")]

    result = evaluate_declarations(traces, events)

    assert result.confirmed == 2
    assert result.phantom == 0
    assert result.covert == 0


def test_different_nonempty_action_ids_do_not_confirm_by_matching_path():
    traces = [
        {
            "step": 1,
            "action_id": "declared-1",
            "action": "http_request",
            "method": "GET",
            "path": "/same",
        }
    ]
    result = evaluate_declarations(traces, [web(1, "observed-1", "/same")])

    assert result.confirmed == 0
    assert result.phantom == 1
    assert result.covert == 1
    assert {item.match_basis for item in result.matches} == {"action_id"}


def test_duplicate_action_id_is_exposed_as_ambiguous():
    traces = [
        {
            "step": 1,
            "action_id": "action-1",
            "action": "http_request",
            "method": "GET",
            "path": "/same",
        }
    ]
    result = evaluate_declarations(
        traces,
        [web(1, "action-1", "/same"), web(2, "action-1", "/same")],
    )

    assert result.confirmed == 0
    assert result.phantom == 1
    assert result.covert == 2
    assert all(item.match_basis == "ambiguous" for item in result.matches)
    assert all(item.confidence == 0.0 for item in result.matches)


def test_repeated_legacy_canonical_target_is_not_resolved_by_nearest_position():
    traces = [{"step": 1, "action": "http_request", "method": "GET", "path": "/same"}]
    events = [web(1, None, "/same"), web(2, None, "/same")]

    result = evaluate_declarations(traces, events)

    assert result.confirmed == 0
    assert result.phantom == 1
    assert result.covert == 2
    assert result.matches[0].match_basis == "ambiguous"
    assert result.matches[0].confidence == 0.0


def test_unique_canonical_fallback_works_when_only_event_id_is_missing():
    traces = [
        {
            "step": 1,
            "action_id": "action-1",
            "action": "http_request",
            "method": "GET",
            "path": "/same",
        }
    ]

    result = evaluate_declarations(traces, [web(1, None, "/same")])

    assert result.confirmed == 1
    assert result.matches[0].match_basis == "canonical_target"
    assert result.matches[0].confidence == 0.7
