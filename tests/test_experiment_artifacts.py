import json

from benchmark_core.experiment import _order_for, load_experiment_aggregate


def summary(experiment_id, order):
    return {
        "schema_version": "1.0",
        "experiment_id": experiment_id,
        "off_run": f"{experiment_id}-off",
        "on_run": f"{experiment_id}-on",
        "execution_order": order,
        "comparability": {"valid": True},
        "deltas": {"capability_loss": 0, "roe_gain": 1},
        "agent_compliance": {"attempted_r2_violations": {"off": 2, "on": 2}},
        "control_effectiveness": {
            "blocked_r2_violations_on": 2,
            "escaped_r2_violations_on": 0,
            "false_blocks_on": 0,
            "allowed_allowed_actions_on": 1,
        },
    }


def test_counterbalanced_order_alternates_by_pair_number():
    assert _order_for("pair-001", "counterbalanced") == ["guardrail_off", "guardrail_on"]
    assert _order_for("pair-002", "counterbalanced") == ["guardrail_on", "guardrail_off"]


def test_loader_aggregates_real_summaries_and_excludes_invalid(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "summary.json").write_text(
        json.dumps(summary("one", ["guardrail_off", "guardrail_on"])), encoding="utf-8"
    )
    invalid = summary("two", ["guardrail_on", "guardrail_off"])
    invalid["comparability"]["valid"] = False
    (tmp_path / "two" / "summary.json").write_text(json.dumps(invalid), encoding="utf-8")
    aggregate, errors = load_experiment_aggregate(tmp_path)
    assert errors == []
    assert aggregate["total_pairs"] == 2
    assert aggregate["valid_pairs"] == 1
    assert aggregate["invalid_pairs"] == 1
    assert aggregate["off_on_count"] == 1


def test_loader_reports_malformed_and_duplicate_artifacts(tmp_path):
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
    (tmp_path / "one" / "summary.json").write_text(
        json.dumps(summary("same", ["guardrail_off", "guardrail_on"])), encoding="utf-8"
    )
    (tmp_path / "two" / "summary.json").write_text(
        json.dumps(summary("same", ["guardrail_on", "guardrail_off"])), encoding="utf-8"
    )
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "summary.json").write_text("{", encoding="utf-8")
    aggregate, errors = load_experiment_aggregate(tmp_path)
    assert aggregate["valid_pairs"] == 1
    assert len(errors) == 2
    assert any("duplicate experiment_id" in error for error in errors)
