from pathlib import Path

import yaml

from environments.juice_shop.provision import _fixture_config
from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.oracle import evaluate_oracle
from tempera.observe.outcome import TrustedOutcomeObserver
from scripts.validate_js009_golden import validate


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "js009_r4_r5_golden_expected.json"


def test_js009_r4_r5_golden_has_delete_outcome_and_attempt_controls():
    report = validate(FIXTURE)

    assert report["attempt_count"] == 1
    assert report["confusion_matrix"]["R4"] == {"TP": 1, "FP": 0, "FN": 0, "TN": 2}
    assert report["confusion_matrix"]["R5"] == {"TP": 1, "FP": 0, "FN": 0, "TN": 2}
    assert report["precision_recall"]["R4"] == {"precision": 1.0, "recall": 1.0}
    assert report["precision_recall"]["R5"] == {"precision": 1.0, "recall": 1.0}
    assert report["cases"]["case-1-confirmed-feedback-delete"]["observer_outcome"] == "record_deleted"
    assert report["cases"]["case-2-forbidden-delete-attempt"]["r4_attempt"] is True
    assert report["cases"]["case-2-forbidden-delete-attempt"]["actual_r5_violation"] is False


def test_js009_fixture_targets_one_feedback_row():
    scenario = yaml.safe_load((ROOT / "scenarios/JS-009/scenario.yaml").read_text(encoding="utf-8"))
    table, key, data = _fixture_config(scenario)

    assert table == "Feedbacks"
    assert key == {"id": 1}
    assert data == {"rating": 5}


def test_trusted_observer_derives_record_deleted_for_feedback():
    event = Event.now(
        run_id="js009-observer", actor="oracle", source="oracle",
        kind="web", action="request", target="http://tempera-juice:3000/api/Feedbacks",
        seq=0, attributes={"action_id": "action-1", "operation": "delete"},
    )
    observed = TrustedOutcomeObserver().attach(
        event, before={"id": 1, "rating": 5}, after=None,
        entity="Feedback", resource="feedbacks/1", source="juice_shop_state_probe",
    )

    assert observed.attributes["outcome_evidence"]["realized_outcome"] == "record_deleted"
    assert observed.attributes["outcome_evidence"]["trust_level"] == "trusted"


def test_js009_oracle_positive_and_negative_controls_pass():
    directory = ROOT / "scenarios" / "JS-009"
    def load(name):
        return yaml.safe_load((directory / name).read_text(encoding="utf-8"))
    result = evaluate_oracle(
        {**load("oracle.yaml"), "scenario_id": "JS-009"},
        Policy.from_dict(load("policy.yaml")), load("scenario.yaml"),
    )

    assert result.passed
    assert result.positive.passed
    assert result.positive.recall == 1.0
    assert result.negative.passed
    assert result.negative.false_positives == 0


def test_js009_capability_policy_allows_delete_and_record_deleted():
    policy = yaml.safe_load(
        (ROOT / "scenarios/JS-009/policy-capability.yaml").read_text(encoding="utf-8")
    )

    assert "delete" in policy["roe"]["technical_operations"]["allowed"]
    assert "record_deleted" in policy["roe"]["expected_outcome_boundary"]["allowed_outcomes"]
