import pytest

from analysis.statistics import analyze_two_arm_counts, fisher_exact_two_sided, wilson_interval


COUNTS = {
    "baseline": {
        "attempted": 44, "goal_success": 38, "roe_violation": 3, "safe_success": 36,
    },
    "impact_seeking": {
        "attempted": 49, "goal_success": 38, "roe_violation": 29, "safe_success": 9,
    },
}


def test_fisher_exact_matches_reference_values():
    assert fisher_exact_two_sided(29, 20, 3, 41) == pytest.approx(5.60579337242836e-8)
    assert fisher_exact_two_sided(38, 11, 38, 6) == pytest.approx(0.2974033009101887)
    assert fisher_exact_two_sided(9, 40, 36, 8) == pytest.approx(5.678228878072689e-10)


def test_wilson_intervals_match_reference_values():
    assert wilson_interval(3, 44) == pytest.approx((0.023459395907381626, 0.18225044370221527))
    assert wilson_interval(38, 44) == pytest.approx((0.7329054695148455, 0.9359704550244412))
    assert wilson_interval(36, 44) == pytest.approx((0.6803944911281532, 0.9048719428437227))


def test_documented_count_analysis_labels_source_and_effect_direction():
    result = analyze_two_arm_counts(COUNTS, source="unverified_document_aggregate")

    assert result["source"] == "unverified_document_aggregate"
    assert result["roe_violation"]["risk_ratio"] == pytest.approx(8.680272108843537)
    assert result["roe_violation"]["risk_ratio_ci95"] == pytest.approx(
        (2.8412684236173327, 26.518833369372253)
    )
    assert result["safe_success"]["risk_ratio"] == pytest.approx(0.22448979591836735)
    assert result["goal_success"]["fisher_exact_two_sided_p"] == pytest.approx(
        0.2974033009101887
    )
