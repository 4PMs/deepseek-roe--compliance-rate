from datetime import datetime, timezone

from benchmark_core.core.event import Event
from benchmark_core.core.policy import Policy
from benchmark_core.core.result import GoalResult
from benchmark_core.evaluate.matching import matches
from benchmark_core.evaluate.progress import evaluate_progress
from benchmark_core.evaluate.roe import evaluate_roe
from benchmark_core.observe.gateway import WebObserver
from benchmark_core.observe.normalizer import RawObservation


def _event(**attributes: object) -> Event:
    return Event(
        schema_version="0.2", run_id="run", timestamp=datetime.now(timezone.utc),
        actor="agent", source="gateway", kind="web", action="request",
        target="http://example.test", seq=0, attributes=dict(attributes),
    )


def test_progress_stage_matches_marker_subset():
    event = _event(matched_markers=["BENCHMARK-SENSITIVE-004", "OTHER"])
    result = evaluate_progress([event], stage_rules=[{
        "stage": 2, "name": "goal",
        "when": {"attributes": {"matched_markers": ["BENCHMARK-SENSITIVE-004"]}},
    }])
    assert result.current_stage == 2


def test_progress_stage_does_not_match_when_marker_is_absent():
    result = evaluate_progress([_event(matched_markers=["OTHER"])], stage_rules=[{
        "stage": 2, "name": "goal",
        "when": {"attributes": {"matched_markers": ["BENCHMARK-SENSITIVE-004"]}},
    }])
    assert result.current_stage == 0


def test_list_attribute_subset_matching_is_order_independent():
    assert matches(
        _event(matched_markers=["B", "A"]),
        {"attributes": {"matched_markers": ["A", "B"]}},
    )


def test_scalar_attribute_matches_list_expectation():
    assert matches(_event(marker="A"), {"attributes": {"marker": ["A", "B"]}})


def test_string_port_matches_allowed_integer_port():
    event = _event(host="example.test", port="3000", scheme="http")
    policy = Policy.from_dict({"scope": {"allowed_ports": [3000]}})
    result = evaluate_roe([event], policy, goal=GoalResult(False))
    assert result.compliant is True
    assert result.violations == []


def test_web_normalizer_converts_string_port_to_int():
    event = WebObserver().normalize(
        "run",
        RawObservation(
            datetime.now(timezone.utc), "agent", "gateway", "web", "request",
            "http://example.test", {"port": "3000"},
        ),
        seq=0,
    )
    assert event.attributes["port"] == 3000
