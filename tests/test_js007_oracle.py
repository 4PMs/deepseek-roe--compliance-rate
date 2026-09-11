from pathlib import Path

import yaml

from tempera.core.policy import Policy
from tempera.evaluate.oracle import evaluate_oracle


ROOT = Path(__file__).parents[1]


def test_js007_oracle_positive_and_negative_controls_pass():
    directory = ROOT / "scenarios" / "JS-007"
    def load(name):
        return yaml.safe_load((directory / name).read_text(encoding="utf-8"))
    result = evaluate_oracle(
        {**load("oracle.yaml"), "scenario_id": "JS-007"},
        Policy.from_dict(load("policy.yaml")), load("scenario.yaml"),
    )

    assert result.passed
    assert result.positive.passed
    assert result.positive.recall == 1.0
    assert result.negative.passed
    assert result.negative.false_positives == 0
    categories = {
        category
        for item in result.positive.violations_found
        for category in item.get("roe_categories", ())
    }
    assert categories >= {"R3", "R6"}


def test_js007_capability_policy_explicitly_allows_cross_user_activity():
    directory = ROOT / "scenarios" / "JS-007"
    capability = yaml.safe_load((directory / "policy-capability.yaml").read_text(encoding="utf-8"))
    activity = capability["roe"]["activity_authorization"]

    assert "other_user_account_data_access" in activity["allowed"]
    assert "other_user_account_data_access" not in activity.get("prohibited", [])
