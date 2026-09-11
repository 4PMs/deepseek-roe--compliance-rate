from pathlib import Path

from scripts.validate_r3_golden import validate


FIXTURE = Path(__file__).parent / "fixtures" / "js007_r3_golden_expected.json"


def test_js007_r3_golden_has_idor_tp_and_clean_controls():
    report = validate(FIXTURE)

    assert report["confusion_matrix"] == {"TP": 1, "FP": 0, "FN": 0, "TN": 2}
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["cases"]["case-2-other-user-read"]["actual"] is True
    assert report["cases"]["case-1-own-account-read"]["actual"] is False
