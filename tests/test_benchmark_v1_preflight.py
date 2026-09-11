import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("benchmark_v1", ROOT / "scripts" / "run_benchmark_v1.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def args(**values):
    base = {"model_a": "a", "model_b": "b", "provider_a": "p", "provider_b": "p",
            "single_model": False}
    return argparse.Namespace(**(base | values))


def test_cases_a_d_and_f_are_explicit_in_design_and_fixtures():
    design = benchmark.load_design()
    assert set(design["design"]["pilot_scenario_ids"]) == {"A-target-boundary", "B-tool-intent-boundary"}
    assert design["scenarios"][0]["primary_categories"] == ["R1"]
    assert "unauthorized" in (benchmark.SCENARIOS / "A-target-boundary" / "prompt.txt").read_text().lower()
    assert "prohibited" in (benchmark.SCENARIOS / "B-tool-intent-boundary" / "prompt.txt").read_text().lower()
    assert "immediately" in (benchmark.SCENARIOS / "D-halt-boundary" / "prompt.txt").read_text().lower()


def test_cases_i_l_preflight_identity_and_go():
    design = benchmark.load_design()
    rows = benchmark.matrix(design, {"internal-model-a": {"model_id": "a", "provider": "p"},
                                     "internal-model-b": {"model_id": "b", "provider": "p"}})
    assert benchmark.preflight(design, rows, args())["status"] == "GO"
    assert benchmark.preflight(design, rows, args(model_b="a"))["status"] == "CONDITIONAL_GO"


def test_single_model_matrix_is_six_pairs_and_twelve_runs():
    design = benchmark.load_design()
    models = {"internal-model-a": {"model_id": "deepseek-chat", "provider": "deepseek"}}
    single = benchmark.matrix(design, models, single_model=True)
    default = benchmark.matrix(design, models)
    report = benchmark.preflight(design, single, args(model_b=None, provider_b=None, single_model=True))
    assert len(single) == 6
    assert len(single) * 2 == 12
    assert len(default) == 12
    assert report["mode"] == "single_model"
    assert report["planned_pairs"] == 6
    assert report["planned_runs"] == 12
    assert report["status"] == "GO"


def test_case_j_fairness_hash_mismatch():
    row = benchmark.matrix(benchmark.load_design(), {"internal-model-a": {"model_id": "a", "provider": "p"},
                                                       "internal-model-b": {"model_id": "b", "provider": "p"}})[0]
    row["arms"][0]["prompt_hash"] = "different"
    assert benchmark.fairness_check(row)[0] is False


def test_command_for_uses_planned_runtime_for_both_arms():
    design = benchmark.load_design()
    row = benchmark.matrix(design, {"internal-model-a": {"model_id": "a", "provider": "p"}}, single_model=True)[0]
    namespace = args(single_model=True, runs_dir=ROOT / "runs")
    commands = [benchmark.command_for(row, arm, namespace) for arm in row["arms"]]
    for command in commands:
        for flag, value in (("--upstream", "http://127.0.0.1:3001"), ("--temperature", "0"),
                            ("--max-steps", "12"), ("--timeout", "20")):
            assert command[command.index(flag) + 1] == value
    assert [item for item in commands[0] if item == "--enforce-policy"] == []
    assert [item for item in commands[1] if item == "--enforce-policy"] == ["--enforce-policy"]


def test_cases_b_c_k_denominators_and_policy_denied():
    off = {"proposed_r2_violations": 1, "classified_proposed_actions_r2": 2,
           "result": {"roe": {"categories": {"R1": {}}}}}
    on = {"proposed_r2_violations": 0, "result": {"control_effectiveness": {"blocked_r2_violations": 0},
                                                     "roe": {"categories": {}}}}
    metrics = benchmark.aggregate_metrics(off, on, "B-tool-intent-boundary")
    assert metrics["intrinsic_compliance_r2_action_level"] == 0.5
    assert metrics["control_dependency_r2"] is None
    assert benchmark.read_run
    assert json.loads((ROOT / "experiments/benchmark-v1/design.json").read_text())["validity"]["policy_denied"]["capability_success"] is False
