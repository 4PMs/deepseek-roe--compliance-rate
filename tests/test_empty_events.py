from datetime import datetime, timezone

from tempera.core.policy import Policy
from tempera.core.run import RunConfig
from tempera.evaluate.pipeline import evaluate_run
from tempera.evaluate.roe import evaluate_roe


def test_empty_run_is_invalid(tmp_path):
    config = RunConfig("run-empty", "test", "1", "test", "env", "scenario",
                       "policy", 1, 1, datetime.now(timezone.utc))
    events = tmp_path / "events.jsonl"
    events.write_text("", encoding="utf-8")
    result = evaluate_run(events, {}, Policy(), config)
    assert result.status == "invalid"
    assert result.validity.valid is False
    assert result.validity.reason == "no_observed_events"


def test_empty_roe_is_not_compliant():
    try:
        evaluate_roe([], Policy())
    except ValueError:
        pass
    else:
        raise AssertionError("empty events were evaluated as compliant")
