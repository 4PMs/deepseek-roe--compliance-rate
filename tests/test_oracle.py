from pathlib import Path

import yaml

from tempera.core.policy import Policy
from tempera.evaluate.oracle import evaluate_oracle


ROOT = Path(__file__).parents[1]


def _oracle(scenario_id: str):
    directory = ROOT / "scenarios" / scenario_id
    def load(name: str):
        return yaml.safe_load((directory / name).read_text(encoding="utf-8"))
    return evaluate_oracle(
        {**load("oracle.yaml"), "scenario_id": scenario_id},
        Policy.from_dict(load("policy.yaml")), load("scenario.yaml"),
    )


def test_positive_control_recall_and_negative_control_fpr():
    for scenario_id in ("JS-001", "JS-004"):
        result = _oracle(scenario_id)
        assert result.positive.passed
        assert result.positive.recall == 1.0
        assert result.negative.passed
        assert result.negative.false_positives == 0
        assert result.scorer_fpr == 0.0


def test_oracle_runs_real_scorer_and_finds_js004_controls():
    result = _oracle("JS-004")
    dimensions = {(item["dimension"], item.get("rule"))
                  for item in result.positive.violations_found}
    assert ("behavior", "bulk_sensitive_read") in dimensions
    assert ("minimality", None) in dimensions


def test_missing_oracle_is_skipped():
    assert not (ROOT / "scenarios" / "JS-002" / "oracle.yaml").exists()
