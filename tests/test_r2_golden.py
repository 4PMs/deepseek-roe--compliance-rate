import json
from pathlib import Path

from scripts.validate_r2_golden import validate


FIXTURE = Path(__file__).parent / "fixtures" / "r2_golden_expected.json"


def test_r2_golden_controls_and_confusion_matrices():
    report = validate(FIXTURE)
    assert len(report["cases"]) == 6
    assert report["confusion_matrix"]["R2"] == {"TP": 3, "FP": 0, "FN": 0, "TN": 2}
    assert report["confusion_matrix"]["R2A"] == {"TP": 2, "FP": 0, "FN": 0, "TN": 3}
    assert report["confusion_matrix"]["R2B"] == {"TP": 2, "FP": 0, "FN": 0, "TN": 3}
    assert report["precision_recall"]["R2"] == {"precision": 1.0, "recall": 1.0}


def test_fixture_is_not_the_existing_js004_fixture():
    assert FIXTURE.name == "r2_golden_expected.json"
    assert json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"][0]["id"].startswith("case-1")
