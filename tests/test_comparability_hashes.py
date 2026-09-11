from tempera.experiment import build_pair_summary


def result(run_id, provenance, reproducibility=None):
    return {
        "run_id": run_id,
        "goal": {"success": False}, "progress": {"current_stage": 0},
        "metrics": {"steps": 1}, "roe": {"summary": {"violations": 0}},
        "validity": {"valid": True}, "termination": {"reason": "agent_done"},
        "provenance": provenance,
        "reproducibility": reproducibility or {"seed_requested": 7, "seed_applied": True},
    }


BASE = {
    "policy_sha256": "policy-a", "scenario_sha256": "scenario-a",
    "environment_sha256": "environment-a", "target_image_digest": "image-a",
    "agent_model": "model-a", "agent_version": "agent-a",
}
CONFIG = {"policy": "same", "scenario": "same", "environment": "env",
          "model": "model-a", "provider": "ollama", "temperature": 0,
          "max_steps": 3, "timeout": 10, "seed": 7, "agent_version": "agent-a"}


def pair(left=BASE, right=BASE):
    return build_pair_summary("pair", result("off", left), result("on", right),
                              off_config=CONFIG, on_config=CONFIG,
                              target_reset_verified=True)


def test_different_policy_content_hash_invalidates_pair():
    right = {**BASE, "policy_sha256": "policy-b"}
    summary = pair(right=right)
    assert summary["comparability"]["same_policy_hash"] is False
    assert summary["comparability"]["valid"] is False


def test_different_scenario_content_hash_invalidates_pair():
    right = {**BASE, "scenario_sha256": "scenario-b"}
    summary = pair(right=right)
    assert summary["comparability"]["same_scenario_hash"] is False
    assert summary["comparability"]["valid"] is False


def test_matching_hashes_and_runtime_metadata_are_comparable():
    summary = pair()
    assert summary["comparability"]["valid"] is True
    assert summary["comparability"]["reproducibility_warning"] is None


def test_seed_not_applied_invalidates_strict_pair():
    on = result("on", BASE, {"seed_requested": 7, "seed_applied": False})
    summary = build_pair_summary("pair", result("off", BASE), on,
                                off_config=CONFIG, on_config=CONFIG,
                                target_reset_verified=True)
    assert summary["comparability"]["reproducibility_warning"] == "seed_not_applied"
    assert summary["comparability"]["valid"] is False
