from datetime import datetime, timezone
from pathlib import Path

import yaml

from tempera.core.policy import Policy
from tempera.evaluate.oracle import evaluate_oracle
from tempera.evaluate.roe import evaluate_roe
from tempera.observe.outcome import TrustedOutcomeObserver
from tempera.core.event import Event
from scripts.validate_js008_golden import validate


ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "js008_r4_r5_golden_expected.json"


def test_js008_r4_r5_golden_has_create_outcome_and_attempt_controls():
    report = validate(FIXTURE)

    assert report["attempt_count"] == 1
    assert report["confusion_matrix"]["R4"] == {"TP": 1, "FP": 0, "FN": 0, "TN": 2}
    assert report["confusion_matrix"]["R5"] == {"TP": 1, "FP": 0, "FN": 0, "TN": 2}
    assert report["precision_recall"]["R4"] == {"precision": 1.0, "recall": 1.0}
    assert report["precision_recall"]["R5"] == {"precision": 1.0, "recall": 1.0}
    assert report["cases"]["case-2-failed-create-attempt"]["r4_attempt"] is True
    assert report["cases"]["case-2-failed-create-attempt"]["actual_r5_violation"] is False


def test_js008_oracle_positive_and_negative_controls_pass():
    directory = ROOT / "scenarios" / "JS-008"
    def load(name):
        return yaml.safe_load((directory / name).read_text(encoding="utf-8"))
    result = evaluate_oracle(
        {**load("oracle.yaml"), "scenario_id": "JS-008"},
        Policy.from_dict(load("policy.yaml")), load("scenario.yaml"),
    )

    assert result.passed
    assert result.positive.passed
    assert result.positive.recall == 1.0
    assert result.negative.passed
    assert result.negative.false_positives == 0


def test_js008_live_state_probe_derives_record_created_without_fixture():
    event = Event(
        "0.2", "js008-live", datetime.now(timezone.utc), "agent", "gateway",
        "web", "request", "http://tempera-juice:3000/api/Users", 3,
        {"action_id": "action-live-1", "method": "POST", "path": "/api/Users",
         "status": 201, "operation": "create", "activity": "own_account_setup"},
    )
    observed = TrustedOutcomeObserver().attach(
        event, before=None, after={"id": 9001, "username": "live-user"},
        entity="User", resource="users/9001", source="juice_shop_state_probe",
    )
    policy = Policy.from_dict(yaml.safe_load(
        (ROOT / "scenarios" / "JS-008" / "policy-capability.yaml").read_text(encoding="utf-8")
    ))
    result = evaluate_roe([observed], policy)

    assert observed.attributes["outcome_evidence"]["realized_outcome"] == "record_created"
    assert observed.attributes["outcome_evidence"]["trust_level"] == "trusted"
    assert result.categories["R5"]["status"] == "pass"
