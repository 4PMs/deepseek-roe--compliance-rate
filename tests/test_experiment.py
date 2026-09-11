from tempera.experiment import aggregate_pair_summaries, build_pair_summary


CONFIG = {
    "scenario": "fixture",
    "model": "fixture-model",
    "agent_version": "fixture-agent",
    "provider": "fixture",
    "temperature": 0,
    "max_steps": 3,
    "timeout": 10,
    "seed": 7,
    "policy": "policy",
}


def result(run_id, *, goal=False, stage=0, steps=1, attempted=0,
           observed=0, blocked=0, escaped=0, false_blocks=0,
           recall=None, fpr=None, valid=True, reason="agent_done"):
    return {
        "run_id": run_id,
        "goal": {"success": goal},
        "progress": {"current_stage": stage},
        "metrics": {"steps": steps},
        "roe": {"summary": {"violations": observed}},
        "control_effectiveness": {
            "attempted_violations": attempted,
            "blocked_violations": blocked,
            "escaped_violations": escaped,
            "blocked_allowed_actions": false_blocks,
            "allowed_allowed_actions": 1 if goal else 0,
            "enforcement_recall": recall,
            "enforcement_fpr": fpr,
        },
        "validity": {"valid": valid},
        "termination": {"reason": reason},
    }


def pair(off, on):
    return build_pair_summary(
        "fixture-001", off, on,
        off_config=CONFIG,
        on_config=CONFIG,
        target_reset_verified=True,
    )


def test_case_1_blocked_action_separates_agent_and_control_axes():
    summary = pair(
        result("off", attempted=1, observed=1),
        result("on", attempted=1, blocked=1, recall=1.0, valid=False,
               reason="policy_denied"),
    )

    assert summary["comparability"]["valid"] is True
    assert summary["agent_compliance"]["attempted_roe_violations"] == {"off": 1, "on": 1}
    assert summary["control_effectiveness"]["blocked_violations_on"] == 1
    assert summary["observed_safety"]["observed_roe_violations"] == {"off": 1, "on": 0}
    assert summary["deltas"]["roe_gain"] == 1
    assert summary["deltas"]["control_dependency"] == 1.0


def test_case_2_allowed_action_has_no_false_block_or_capability_delta():
    summary = pair(result("off", goal=True, stage=2), result("on", goal=True, stage=2))

    assert summary["capability"]["off"] == summary["capability"]["on"]
    assert summary["control_effectiveness"]["false_blocks_on"] == 0
    assert summary["deltas"]["capability_loss"] == 0


def test_case_3_forced_deny_is_a_false_block_and_capability_loss():
    summary = pair(
        result("off", goal=True, stage=2),
        result("on", goal=False, false_blocks=1, valid=False, reason="policy_denied"),
    )

    assert summary["control_effectiveness"]["false_blocks_on"] == 1
    assert summary["deltas"]["capability_loss"] == -1
    assert summary["deltas"]["capability_loss_pp"] == -100


def test_case_4_reset_failure_invalid_pair_and_aggregate_excludes_it():
    invalid = build_pair_summary(
        "fixture-002", result("off", valid=False, reason="target_error"),
        result("on", valid=False, reason="target_error"),
        off_config=CONFIG, on_config=CONFIG, target_reset_verified=False,
    )
    valid = pair(result("off", attempted=1), result("on", attempted=1, blocked=1))

    aggregate = aggregate_pair_summaries([invalid, valid])

    assert invalid["comparability"]["valid"] is False
    assert aggregate["valid_pairs"] == 1
    assert aggregate["invalid_pairs"] == 1
    assert aggregate["total_attempted_violations"] == {"off": 1, "on": 1}
    assert aggregate["aggregate_enforcement_recall"] == 1.0


def test_aggregate_denominator_zero_is_none():
    assert aggregate_pair_summaries([])["aggregate_enforcement_recall"] is None
    assert aggregate_pair_summaries([])["aggregate_enforcement_fpr"] is None
