"""Dependency-free statistics for preregistered JS-001 binary outcomes."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

Z_95 = 1.959963984540054


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    if total <= 0 or successes < 0 or successes > total:
        raise ValueError("require 0 <= successes <= total and total > 0")
    proportion = successes / total
    denominator = 1 + z * z / total
    center = proportion + z * z / (2 * total)
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total)
    return (center - margin) / denominator, (center + margin) / denominator


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p-value using SciPy/R's probability-ordering definition."""
    if min(a, b, c, d) < 0:
        raise ValueError("contingency-table counts must be non-negative")
    total = a + b + c + d
    if total == 0:
        raise ValueError("contingency table must not be empty")
    first_row = a + b
    first_column = a + c
    denominator = math.comb(total, first_row)

    def probability(value: int) -> float:
        return (
            math.comb(first_column, value)
            * math.comb(total - first_column, first_row - value)
            / denominator
        )

    minimum = max(0, first_row - (total - first_column))
    maximum = min(first_row, first_column)
    observed = probability(a)
    return min(1.0, sum(
        probability(value)
        for value in range(minimum, maximum + 1)
        if probability(value) <= observed * (1 + 1e-12)
    ))


def _risk_ratio(
    treatment_successes: int,
    treatment_total: int,
    control_successes: int,
    control_total: int,
) -> tuple[float | None, tuple[float, float] | None]:
    if treatment_successes == 0 or control_successes == 0:
        return None, None
    ratio = (treatment_successes / treatment_total) / (control_successes / control_total)
    standard_error = math.sqrt(
        1 / treatment_successes - 1 / treatment_total
        + 1 / control_successes - 1 / control_total
    )
    return ratio, (
        math.exp(math.log(ratio) - Z_95 * standard_error),
        math.exp(math.log(ratio) + Z_95 * standard_error),
    )


def _risk_difference_ci(
    treatment_successes: int,
    treatment_total: int,
    control_successes: int,
    control_total: int,
) -> tuple[float, float]:
    """Newcombe score interval for a difference between independent proportions."""
    treatment = treatment_successes / treatment_total
    control = control_successes / control_total
    treatment_low, treatment_high = wilson_interval(treatment_successes, treatment_total)
    control_low, control_high = wilson_interval(control_successes, control_total)
    difference = treatment - control
    return (
        difference - math.sqrt((treatment - treatment_low) ** 2 + (control_high - control) ** 2),
        difference + math.sqrt((treatment_high - treatment) ** 2 + (control - control_low) ** 2),
    )


def analyze_two_arm_counts(
    counts: Mapping[str, Mapping[str, int]], *, source: str
) -> dict[str, Any]:
    baseline = counts["baseline"]
    treatment = counts["impact_seeking"]
    baseline_total = int(baseline["attempted"])
    treatment_total = int(treatment["attempted"])
    result: dict[str, Any] = {
        "source": source,
        "warning": (
            "Aggregate-count calculations do not verify raw Run validity, provenance, "
            "condition assignment, or denominator eligibility."
        ),
        "arm_totals": {"baseline": baseline_total, "impact_seeking": treatment_total},
    }
    for outcome in ("goal_success", "roe_violation", "safe_success"):
        baseline_successes = int(baseline[outcome])
        treatment_successes = int(treatment[outcome])
        ratio, ratio_interval = _risk_ratio(
            treatment_successes, treatment_total, baseline_successes, baseline_total
        )
        risk_difference = treatment_successes / treatment_total - baseline_successes / baseline_total
        result[outcome] = {
            "baseline": {
                "numerator": baseline_successes,
                "denominator": baseline_total,
                "rate": baseline_successes / baseline_total,
                "wilson_ci95": wilson_interval(baseline_successes, baseline_total),
            },
            "impact_seeking": {
                "numerator": treatment_successes,
                "denominator": treatment_total,
                "rate": treatment_successes / treatment_total,
                "wilson_ci95": wilson_interval(treatment_successes, treatment_total),
            },
            "risk_difference": risk_difference,
            "risk_difference_ci95": _risk_difference_ci(
                treatment_successes, treatment_total, baseline_successes, baseline_total
            ),
            "risk_ratio": ratio,
            "risk_ratio_ci95": ratio_interval,
            "fisher_exact_two_sided_p": fisher_exact_two_sided(
                treatment_successes, treatment_total - treatment_successes,
                baseline_successes, baseline_total - baseline_successes,
            ),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Recalculate documented JS-001 aggregate statistics")
    parser.add_argument("--spec", type=Path, default=Path("analysis/js001_audit_spec.json"))
    parser.add_argument(
        "--output", type=Path,
        default=Path("analysis/outputs/documented_aggregate_statistics.json"),
    )
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    result = analyze_two_arm_counts(
        spec["expected_document_counts"], source="unverified_document_aggregate"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
