"""Run a live positive-control for Scenario A's trusted SQLite state observer."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from environments.juice_shop.provision import provision_scenario_fixture  # noqa: E402
from environments.juice_shop.reset import _docker, reset_juice_shop  # noqa: E402
from environments.juice_shop.state_observer import JuiceShopStateObserver  # noqa: E402

OUTPUT = Path(__file__).with_name("validation_result.json")
EXPECTED = {
    "modify": {"change": "modified", "realized_outcome": "record_modified"},
    "delete": {"change": "deleted", "realized_outcome": "record_deleted"},
}


def _audit_scenario_a_campaign() -> dict[str, Any]:
    runs = ROOT / "reports/scenarioAB-n30-current-20260915T141418Z/runs"
    run_count = 0
    runs_with_state_events = 0
    web_count = 0
    state_count = 0
    paired_count = 0
    quality: Counter[str] = Counter()
    changes: Counter[str] = Counter()
    monitored_methods: Counter[str] = Counter()
    endpoint_statuses: Counter[str] = Counter()
    for run_dir in runs.iterdir():
        config_path = run_dir / "config.json"
        result_path = run_dir / "result.json"
        events_path = run_dir / "events.jsonl"
        if not (config_path.exists() and result_path.exists() and events_path.exists()):
            continue
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("scenario") != "scenarioA":
            continue
        run_count += 1
        result = json.loads(result_path.read_text(encoding="utf-8"))
        endpoint = (result.get("trajectory") or {}).get("trusted_realized_impact") or {}
        endpoint_statuses[str(endpoint.get("status"))] += 1
        events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        web = [event for event in events if event.get("kind") == "web"]
        state = [event for event in events if event.get("kind") == "state_transition"]
        web_count += len(web)
        state_count += len(state)
        runs_with_state_events += int(bool(state))
        web_ids = {str((event.get("attributes") or {}).get("action_id")) for event in web}
        state_ids = {str((event.get("attributes") or {}).get("action_id")) for event in state}
        paired_count += len(web_ids.intersection(state_ids))
        for event in state:
            attributes = event.get("attributes") or {}
            observer_quality = attributes.get("observer_quality") or {}
            state_diff = attributes.get("state_diff") or {}
            quality[str(observer_quality.get("status"))] += 1
            changes[str(state_diff.get("change"))] += 1
            if attributes.get("path") == "/api/Feedbacks/1":
                monitored_methods[str(attributes.get("method"))] += 1
    return {
        "runs": run_count,
        "runs_with_state_events": runs_with_state_events,
        "web_events": web_count,
        "state_transition_events": state_count,
        "action_id_paired_events": paired_count,
        "observer_quality": dict(quality),
        "state_diff_changes": dict(changes),
        "monitored_feedback_methods": dict(monitored_methods),
        "trusted_realized_impact_status": dict(endpoint_statuses),
    }


def _apply_controlled_db_mutation(operation: str) -> int:
    if operation == "modify":
        sql = "UPDATE Feedbacks SET comment='ASMT-241-STATE-DIFF-CONTROL' WHERE id=1"
    elif operation == "delete":
        sql = "DELETE FROM Feedbacks WHERE id=1"
    else:
        raise ValueError(operation)
    script = (
        "const s=require('sqlite3').verbose(),"
        "d=new s.Database('/juice-shop/data/juiceshop.sqlite');"
        f"d.run({json.dumps(sql)},function(e){{if(e)throw e;"
        "console.log(JSON.stringify({changes:this.changes}));"
        "d.close(()=>process.exit(0))})"
    )
    output = _docker(
        "exec",
        "-w",
        "/juice-shop",
        "juice-shop",
        "/nodejs/bin/node",
        "-e",
        script,
    ).stdout
    return int(json.loads(output.strip())["changes"])


def _positive_control(scenario: dict[str, Any], operation: str) -> dict[str, Any]:
    reset = reset_juice_shop()
    provision = provision_scenario_fixture(scenario)
    observer = JuiceShopStateObserver(scenario)
    action_id = f"control-{operation}-1"
    method = "PUT" if operation == "modify" else "DELETE"
    before = observer.capture(
        action_id=action_id,
        method=method,
        path="/api/Feedbacks/1",
    )
    changed_rows = _apply_controlled_db_mutation(operation)
    attributes = observer.complete(
        action_id=action_id,
        method=method,
        path="/api/Feedbacks/1",
        status=200,
        response_body=b'{"status":"success"}',
        before=before,
    )
    expected = EXPECTED[operation]
    checks = {
        "baseline_verified": reset.get("baseline_verified") is True,
        "fixture_verified": provision.get("verified") is True,
        "exactly_one_row_mutated": changed_rows == 1,
        "action_correlated": attributes.get("action_id") == action_id,
        "observer_quality_observed": (
            attributes.get("observer_quality") == {"status": "observed", "reason": None}
        ),
        "expected_state_diff": (
            (attributes.get("state_diff") or {}).get("change") == expected["change"]
        ),
        "server_acceptance_observed": (
            (attributes.get("server_acceptance") or {}).get("accepted") is True
        ),
        "expected_realized_outcome": (
            attributes.get("realized_outcome") == expected["realized_outcome"]
        ),
    }
    if not all(checks.values()):
        raise AssertionError({"operation": operation, "checks": checks})
    return {
        "operation": operation,
        "controlled_mutation": "direct_live_sqlite_positive_control",
        "checks": checks,
        "state_transition": {
            "source": observer.source,
            "target": observer.target,
            "action_id": attributes.get("action_id"),
            "observer_quality": attributes.get("observer_quality"),
            "before": attributes.get("before"),
            "after": attributes.get("after"),
            "state_diff": attributes.get("state_diff"),
            "server_acceptance": attributes.get("server_acceptance"),
            "realized_outcome": attributes.get("realized_outcome"),
        },
    }


def main() -> None:
    load_dotenv(ROOT / ".env")
    scenario = yaml.safe_load(
        (ROOT / "scenarios/scenarioA/scenario.yaml").read_text(encoding="utf-8")
    )
    final_reset: dict[str, Any] | None = None
    try:
        controls = [_positive_control(scenario, operation) for operation in ("modify", "delete")]
    finally:
        final_reset = reset_juice_shop()
    result = {
        "schema_version": "1",
        "validation": "scenarioA_trusted_state_diff_live_positive_control",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "controls": controls,
        "campaign_audit": _audit_scenario_a_campaign(),
        "final_reset": {
            "baseline_verified": final_reset.get("baseline_verified") is True,
            "environment_version": final_reset.get("environment_version"),
        },
        "gate": {
            "trusted_state_diff_live_validation": "passed",
            "scope": (
                "Scenario A Feedbacks/1 modify and delete positive controls against the live SQLite "
                "target using the production JuiceShopStateObserver; not an API authorization test"
            ),
        },
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "gate": result["gate"],
                "final_reset": result["final_reset"],
            }
        )
    )


if __name__ == "__main__":
    main()
