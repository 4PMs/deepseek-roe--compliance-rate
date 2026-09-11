#!/usr/bin/env python3
"""Run declarative scorer controls for one or all scenarios."""

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tempera.core.policy import Policy
from tempera.evaluate.oracle import evaluate_oracle


def _load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def run(scenario_dir: Path) -> bool | None:
    oracle_path = scenario_dir / "oracle.yaml"
    if not oracle_path.is_file():
        print(f"[{scenario_dir.name}] SKIP (no oracle.yaml)")
        return None
    oracle = _load(oracle_path)
    if oracle.get("status") == "TODO":
        print(f"[{scenario_dir.name}] SKIP (oracle TODO)")
        return None
    result = evaluate_oracle(
        {**oracle, "scenario_id": scenario_dir.name},
        Policy.from_dict(_load(scenario_dir / "policy.yaml")),
        _load(scenario_dir / "scenario.yaml"),
    )
    print(f"[{scenario_dir.name}] positive_control: {'PASS' if result.positive.passed else 'FAIL'} "
          f"(recall={result.scorer_recall:.2f})")
    print(f"[{scenario_dir.name}] negative_control: {'PASS' if result.negative.passed else 'FAIL'} "
          f"(fp={result.negative.false_positives})")
    print(f"scorer_recall: {result.scorer_recall:.2f} / scorer_fpr: {result.scorer_fpr:.2f}")
    return result.passed


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate scorer accuracy with oracle.yaml")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scenario")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--scenarios-dir", type=Path, default=ROOT / "scenarios")
    args = parser.parse_args()
    directories = (
        sorted(path for path in args.scenarios_dir.iterdir() if path.is_dir())
        if args.all else [args.scenarios_dir / args.scenario]
    )
    results = [run(path) for path in directories]
    return 1 if any(result is False for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
