from pathlib import Path

from scripts.validate_r5_golden import validate


FIXTURE = Path(__file__).parent / "fixtures" / "r5_golden_expected.json"


def test_r5_golden_has_positive_and_negative_samples():
    report = validate(FIXTURE)

    assert report["confusion_matrix"] == {"TP": 2, "FP": 0, "FN": 0, "TN": 2}
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["cases"]["case-5-observer-uncertain"]["actual"] is None
