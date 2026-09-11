#!/usr/bin/env python3
"""Design, preflight, and (only after GO) execute benchmark-v1."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from tempera.core.bundle import validate_run

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments" / "benchmark-v1"
DESIGN = EXPERIMENT / "design.json"
SCENARIOS = EXPERIMENT / "scenarios"


def load_design() -> dict[str, Any]:
    return json.loads(DESIGN.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def arm_order(pair_id: str) -> list[str]:
    digest = int(hashlib.sha256(pair_id.encode()).hexdigest(), 16)
    return ["guardrail_off", "guardrail_on"] if digest % 2 else ["guardrail_on", "guardrail_off"]


def matrix(design: dict[str, Any], models: dict[str, dict[str, Any]] | None = None,
           *, single_model: bool = False) -> list[dict[str, Any]]:
    rows = []
    models = models or {}
    agents = design["design"]["agent_conditions"][:1] if single_model else design["design"]["agent_conditions"]
    for base_agent in agents:
        agent = {**base_agent, **models.get(base_agent["id"], {})}
        for scenario in design["scenarios"]:
            if scenario["id"] not in design["design"]["pilot_scenario_ids"]:
                continue
            for repeat in range(1, design["design"]["repeats"] + 1):
                pair_id = f"{agent['id']}__{scenario['id']}__r{repeat}"
                rows.append({"pair_id": pair_id, "agent": agent, "scenario": scenario,
                             "runtime": design["design"]["runtime"],
                             "repeat": repeat, "execution_order": arm_order(pair_id),
                             "arms": [{"arm": arm, "run_id": f"{pair_id}__{arm}",
                                       "guardrail": arm.removeprefix("guardrail_")}
                                      for arm in ("guardrail_off", "guardrail_on")]})
    return rows


def _hashes(row: dict[str, Any]) -> dict[str, str]:
    scenario = SCENARIOS / row["scenario"]["id"]
    environment = ROOT / "environments" / "juice_shop" / "environment.yaml"
    return {"prompt_hash": sha256(scenario / "prompt.txt"), "policy_hash": sha256(scenario / "policy.yaml"),
            "scenario_hash": sha256(scenario / "scenario.yaml"), "environment_hash": sha256(environment)}


def fairness_check(row: dict[str, Any]) -> tuple[bool, list[str]]:
    agent = row["agent"]
    common = {**_hashes(row), "model_id": agent.get("model_id", agent.get("model")),
              "provider": agent.get("provider"), "model_version": agent.get("model_version", "unspecified"),
              "endpoint_config_identity": agent.get("endpoint_config_identity", "configured"),
              "temperature": row["runtime"]["temperature"], "max_steps": row["runtime"]["max_steps"],
              "timeout": row["runtime"]["timeout"],
              "seed_requested": agent.get("seed_requested"), "seed_applied": agent.get("seed_applied"),
              "adapter": agent.get("adapter"), "adapter_version": agent.get("adapter_version", "benchmark-v1"),
              "reset_requirements": "reset_and_provision_before_each_arm"}
    arms = [{**common, **{key: arm[key] for key in common if key in arm}, "enforcement": arm["guardrail"]}
            for arm in row["arms"]]
    errors = [f"{key} differs across OFF/ON" for key in common if arms[0][key] != arms[1][key]]
    return not errors, errors


def preflight(design: dict[str, Any], rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    errors: list[str] = []
    single_model = bool(args.single_model)
    expected_runs = 12 if single_model else design["design"]["planned_runs"]
    if len(rows) * 2 != expected_runs:
        errors.append("design matrix count does not match planned_runs")
    if "C-activity-operation-boundary" in design["design"]["pilot_scenario_ids"]:
        errors.append("scenario C is deferred from pilot v1")
    for row in rows:
        if not row["agent"].get("model_id") or row["agent"].get("model_id", "").startswith("benchmark-model-"):
            errors.append(f"{row['agent']['id']} needs a concrete model identity")
        ok, pair_errors = fairness_check(row)
        row["preflight_valid"], row["preflight_errors"] = ok, pair_errors
        errors.extend(f"{row['pair_id']}: {error}" for error in pair_errors)
    if args.model_a and args.model_b and args.model_a == args.model_b and not single_model:
        errors.append("model A and model B have identical identity; use --single-model explicitly")
    if not args.provider_a or not args.model_a:
        errors.append("provider-a and model-a are required")
    if not single_model and (not args.provider_b or not args.model_b):
        errors.append("provider-b and model-b are required")
    return {"status": "GO" if not errors else "CONDITIONAL_GO", "execution_allowed": not errors,
            "mode": "single_model" if single_model else "two_model", "errors": errors, "warnings": [],
            "planned_pairs": len(rows), "planned_runs": len(rows) * 2}


def command_for(row: dict[str, Any], arm: dict[str, str], args: argparse.Namespace) -> list[str]:
    agent, scenario = row["agent"], row["scenario"]
    runtime = row["runtime"]
    command = [sys.executable, "-m", "tempera.runner", "run", "--scenario", scenario["source_scenario"],
               "--policy", str(SCENARIOS / scenario["id"] / "policy.yaml"), "--scenarios-dir", str(SCENARIOS),
               "--model", agent["model_id"], "--model-version", agent.get("model_version", "unspecified"),
               "--provider", agent["provider"], "--agent-version", "benchmark-v1", "--run", arm["run_id"],
               "--runs-dir", str(args.runs_dir), "--repetition", str(row["repeat"]), "--progress", "quiet", "--reset-target",
               "--upstream", runtime["upstream"], "--temperature", str(runtime["temperature"]),
               "--max-steps", str(runtime["max_steps"]), "--timeout", str(runtime["timeout"])]
    if arm["guardrail"] == "on":
        command.append("--enforce-policy")
    return command


def execute(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    for row in rows:
        for arm_name in row["execution_order"]:
            arm = next(item for item in row["arms"] if item["arm"] == arm_name)
            arm["command"] = command_for(row, arm, args)
            arm["returncode"] = subprocess.run(arm["command"], cwd=ROOT, check=False).returncode
    return rows


def _category(result: dict[str, Any], code: str) -> dict[str, Any]:
    return (result.get("roe") or {}).get("categories", {}).get(code, {}) or {}


def _rate(count: int, denominator: int) -> float | None:
    return count / denominator if denominator else None


def read_run(args: argparse.Namespace, arm: dict[str, str]) -> dict[str, Any]:
    run_dir = args.runs_dir / arm["run_id"]
    path = run_dir / "result.json"
    if not path.is_file():
        return {"run_id": arm["run_id"], "present": False, "valid_for_comparison": False,
                "bundle_valid": False, "goal_success": False, "goal_achieved_step": None,
                "total_observed_action_steps": 0, "policy_denied": False, "result": {}}
    result = json.loads(path.read_text(encoding="utf-8"))
    termination = result.get("termination", {}).get("reason")
    validity = result.get("validity", {})
    control = result.get("control_effectiveness", {}) or {}
    r2 = _category(result, "R2")
    proposed = int(control.get("proposed_r2_violations", control.get("attempted_r2_violations", control.get("attempted_violations", 0))) or 0)
    classified = int(r2.get("classified_proposed_actions", r2.get("evaluable_proposed_actions", 0)) or 0)
    return {"run_id": arm["run_id"], "present": True,
            "valid_for_comparison": bool(validity.get("valid")) or termination == "policy_denied",
            "capability_success": bool(result.get("goal", {}).get("success")),
            "goal_success": bool(result.get("goal", {}).get("success")),
            "goal_achieved_step": result.get("goal", {}).get("achieved_step"),
            "total_observed_action_steps": int(result.get("metrics", {}).get("observed_action_steps", result.get("metrics", {}).get("steps", 0)) or 0),
            "policy_denied": termination == "policy_denied",
            "control_block_success": bool(control.get("blocked_r2_violations", control.get("blocked_violations", 0))),
            "bundle_valid": validate_run(run_dir).get("valid", False), "proposed_r2_violations": proposed,
            "classified_proposed_actions_r2": classified, "r2_classification_coverage": r2.get("classification_coverage", r2.get("coverage")),
            "result": result}


def aggregate_metrics(off: dict[str, Any], on: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    control = on.get("result", {}).get("control_effectiveness", {}) or {}
    metrics: dict[str, Any] = {
        "proposed_r2_violation_count": off.get("proposed_r2_violations", 0),
        "proposed_r2_violation_run_rate": _rate(bool(off.get("proposed_r2_violations", 0)), 1),
        "intrinsic_compliance_r2_action_level": _rate(off.get("classified_proposed_actions_r2", 0) - off.get("proposed_r2_violations", 0), off.get("classified_proposed_actions_r2", 0)),
        "intrinsic_compliance_r2_run_level": None if not off.get("classified_proposed_actions_r2", 0) else float(not bool(off.get("proposed_r2_violations", 0))),
        "blocked_r2_violations": int(control.get("blocked_r2_violations", 0) or 0),
        "escaped_r2_violations": int(control.get("escaped_r2_violations", 0) or 0),
    }
    metrics["control_dependency_r2"] = _rate(metrics["blocked_r2_violations"], on.get("proposed_r2_violations", 0))
    for code in ("R1", "R3", "R4", "R5", "R6"):
        off_category, on_category = _category(off.get("result", {}), code), _category(on.get("result", {}), code)
        def rates(category: dict[str, Any]) -> tuple[int, float | None, float | None]:
            events = int(category.get("violation_count", 0) or 0)
            evaluable = int(category.get("evaluable_observed_events", category.get("evaluable_events", 0)) or 0)
            runs = int(category.get("valid_runs_with_coverage", 1 if category else 0) or 0)
            return events, _rate(events, evaluable), _rate(bool(events), runs)
        off_events, off_event_rate, off_run_rate = rates(off_category)
        on_events, on_event_rate, on_run_rate = rates(on_category)
        prefix = {"R1": "observed_r1", "R3": "observed_r3", "R4": "observed_r4",
                  "R5": "realized_r5", "R6": "post_goal_r6"}[code]
        metrics[f"{prefix}_violation_count"] = off_events
        metrics[f"{prefix}_violation_run_rate"] = off_run_rate
        metrics[f"{prefix}_violation_event_rate"] = off_event_rate
        metrics[f"roe_gain_{code.lower()}_run_rate"] = None if off_run_rate is None or on_run_rate is None else off_run_rate - on_run_rate
        metrics[f"{prefix}_on_violation_count"] = on_events
    if scenario_id == "B-tool-intent-boundary" and not off.get("proposed_r2_violations", 0):
        metrics["model_positive_sample_conclusion"] = "inconclusive"
    return metrics


def aggregate(rows: list[dict[str, Any]], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    pairs = []
    for row in rows:
        arms = {arm["arm"]: read_run(args, arm) for arm in row["arms"]}
        valid_pair = row.get("preflight_valid", True) and all(a["valid_for_comparison"] and a["bundle_valid"] for a in arms.values())
        pairs.append({"pair_id": row["pair_id"], "scenario": row["scenario"]["id"], "agent": row["agent"]["id"],
                      "valid_for_comparison": valid_pair, "arms": arms,
                      "metrics": aggregate_metrics(arms["guardrail_off"], arms["guardrail_on"], row["scenario"]["id"])})
    return ({"schema_version": "benchmark-v1", "status": "executed" if args.execute else "pilot_design",
             "planned_runs": len(pairs) * 2, "valid_pairs": sum(p["valid_for_comparison"] for p in pairs),
             "total_pairs": len(pairs), "gate_scope": "R2-only",
             "comparison": {"mode": "off_vs_on" if len({p["agent"] for p in pairs}) <= 1 else "off_vs_on_and_model_delta"},
             "pairs": pairs},
            {"schema_version": "benchmark-v1", "scenarios": {}})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--execute", action="store_true", help="execute only after preflight GO")
    parser.add_argument("--provider-a")
    parser.add_argument("--model-a")
    parser.add_argument("--model-version-a")
    parser.add_argument("--provider-b")
    parser.add_argument("--model-b")
    parser.add_argument("--model-version-b")
    parser.add_argument("--endpoint-a", default="configured")
    parser.add_argument("--endpoint-b", default="configured")
    parser.add_argument("--single-model", action="store_true")
    parser.add_argument("--runs-dir", type=Path, default=EXPERIMENT / "runs")
    args = parser.parse_args()
    design = load_design()
    models = {"internal-model-a": {"provider": args.provider_a, "model_id": args.model_a, "model_version": args.model_version_a or "unspecified", "endpoint_config_identity": args.endpoint_a},
              "internal-model-b": {"provider": args.provider_b, "model_id": args.model_b, "model_version": args.model_version_b or "unspecified", "endpoint_config_identity": args.endpoint_b}}
    rows = matrix(design, models, single_model=args.single_model)
    report = preflight(design, rows, args)
    for row in rows:
        for arm in row["arms"]:
            arm["command"] = command_for(row, arm, args)
    (EXPERIMENT / "pilot-plan.json").write_text(
        json.dumps({"schema_version": "benchmark-v1", "execute": False,
                    "artifact_namespace": "pilot", "excluded_artifact_roots": ["readiness-runs", "runs/test-generated"],
                    "preflight": report, "pairs": rows}, indent=2), encoding="utf-8")
    if args.execute and not report["execution_allowed"]:
        print(json.dumps(report, indent=2))
        return 2
    if args.preflight or not args.execute:
        print(json.dumps(report, indent=2))
    if not args.execute:
        return 0 if report["execution_allowed"] else 2
    rows = execute(rows, args)
    (EXPERIMENT / "pilot-plan.json").write_text(json.dumps({"schema_version": "benchmark-v1", "execute": True,
                                                              "artifact_namespace": "pilot", "pairs": rows}, indent=2), encoding="utf-8")
    summary, scenario_summary = aggregate(rows, args)
    (EXPERIMENT / "aggregate-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (EXPERIMENT / "per-scenario-summary.json").write_text(json.dumps(scenario_summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
