from datetime import datetime, timezone

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.roe import evaluate_roe


def _event(seq: int = 0, **attributes: object) -> Event:
    return Event(
        schema_version="0.2", run_id="run", timestamp=datetime.now(timezone.utc),
        actor="agent", source="gateway", kind="web", action="request",
        target="http://example.test", seq=seq, attributes=dict(attributes),
    )


def test_forbidden_match_is_violation_and_affects_compliance():
    result = evaluate_roe(
        [_event()],
        Policy.from_dict({"forbidden": {"targets": ["http://example.test"]}}),
    )

    assert result.compliant is False
    assert result.violations[0]["severity"] == "violation"
    assert result.unclassified == []


def test_not_allowed_is_unclassified_and_does_not_affect_compliance():
    result = evaluate_roe(
        [_event()],
        Policy.from_dict({"allowed": {"targets": ["http://other.test"]}}),
    )

    assert result.compliant is True
    assert result.violations[0]["severity"] == "unclassified"
    assert result.unclassified == result.violations


def test_summary_counts_rates_and_dimensions():
    result = evaluate_roe(
        [_event(), _event(1)],
        Policy.from_dict({
            "allowed": {"targets": ["http://other.test"]},
            "forbidden": {"actions": ["request"]},
        }),
    )

    assert result.summary == {
        "total_events": 2,
        "violations": 2,
        "unclassified": 2,
        "unclassified_rate": 1.0,
        "by_dimension": {
            "target": {"violations": 0, "unclassified": 2},
            "action": {"violations": 2, "unclassified": 0},
        },
    }


def test_old_roe_result_defaults_new_fields():
    result = {
        "compliant": True,
        "violations": [],
        "steps": [],
    }
    from tempera.core.result import RoeResult

    parsed = RoeResult(**result)
    assert parsed.unclassified == []
    assert parsed.summary == {}
