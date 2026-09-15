from pathlib import Path
import json
import shutil
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from benchmark_core.web_console import (
    ConsoleJobManager,
    build_runner_command,
    create_app,
    discover_options,
)


ROOT = Path(__file__).resolve().parents[1]


def _valid_payload(**overrides):
    payload = {
        "scenario": "scenarioA",
        "condition": "neutral",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "reset_target": True,
        "enforce_policy": False,
        "acknowledge_incomplete": True,
    }
    payload.update(overrides)
    return payload


def test_discover_options_reads_scenario_and_condition_metadata():
    options = discover_options(ROOT)

    assert options["providers"] == [
        {"id": "deepseek", "label": "DeepSeek API"},
    ]
    scenarios = {item["id"]: item for item in options["scenarios"]}
    scenario_a = scenarios["scenarioA"]
    assert scenario_a["status"] == "design_ready"
    assert scenario_a["role"] == "primary"
    assert scenario_a["eligibility"] == "pilot_ready"
    assert {item["id"] for item in scenario_a["conditions"]} == {
        "neutral", "read", "modify", "delete",
    }
    modify = next(item for item in scenario_a["conditions"] if item["id"] == "modify")
    assert modify["group"] == "treatment"
    assert modify["delivery_phase"] == "initial_task"
    assert modify["requested_operation"] == "modify"
    assert modify["target_resource"] == "scenario_a_credential_feedback"
    assert modify["instruction"]

    scenario_b = scenarios["scenarioB"]
    assert scenario_b["role"] == "replication"
    assert scenario_b["eligibility"] == "pilot_ready"
    assert scenario_b["experiment_enabled"] is True
    assert {item["id"] for item in scenario_b["conditions"]} == {
        "neutral", "read", "modify", "delete",
    }


def test_build_runner_command_maps_controls_without_shell_interpolation():
    command, run_id = build_runner_command(
        ROOT,
        _valid_payload(
            run_id="manual-check-01",
            model_version="V3.1",
            seed=17,
            repetition=2,
            temperature=0.2,
            max_steps=8,
            timeout=25,
            upstream="http://127.0.0.1:3000",
            enforce_policy=True,
        ),
    )

    assert command[:4] == [sys.executable, "-m", "benchmark_core.runner", "run"]
    assert run_id == "manual-check-01"
    assert command[command.index("--scenario") + 1] == "scenarioA"
    assert command[command.index("--condition") + 1] == "neutral"
    assert command[command.index("--provider") + 1] == "deepseek"
    assert command[command.index("--model") + 1] == "deepseek-chat"
    assert command[command.index("--progress") + 1] == "human"
    assert "--reset-target" in command
    assert "--enforce-policy" in command
    assert command[command.index("--temperature") + 1] == "0.2"
    assert command[command.index("--seed") + 1] == "17"
    assert command[command.index("--max-steps") + 1] == "8"
    assert command[command.index("--timeout") + 1] == "25"
    assert command[command.index("--run") + 1] == "manual-check-01"


def test_build_runner_command_rejects_unknown_condition_and_unsafe_run_id():
    with pytest.raises(ValueError, match="condition"):
        build_runner_command(ROOT, _valid_payload(condition="unknown"))
    with pytest.raises(ValueError, match="run ID"):
        build_runner_command(ROOT, _valid_payload(run_id="../escape"))
    with pytest.raises(ValueError, match="provider"):
        build_runner_command(ROOT, _valid_payload(provider="ollama"))


def test_api_requires_acknowledgement_for_incomplete_scenario():
    app = create_app(ROOT, manager=Mock(spec=ConsoleJobManager))
    client = app.test_client()

    response = client.post(
        "/api/runs",
        json=_valid_payload(acknowledge_incomplete=False),
    )

    assert response.status_code == 409
    assert "pilot_ready" in response.get_json()["error"]


def test_api_launches_valid_run_and_exposes_job_status():
    manager = Mock(spec=ConsoleJobManager)
    manager.launch.return_value = {
        "job_id": "job-123",
        "run_id": "manual-check-01",
        "status": "running",
        "command": "python -m benchmark_core.runner run ...",
    }
    manager.get.return_value = {
        "job_id": "job-123",
        "run_id": "manual-check-01",
        "status": "completed",
        "return_code": 0,
        "log": "done\n",
        "result": {"status": "completed"},
    }
    app = create_app(ROOT, manager=manager)
    client = app.test_client()

    launched = client.post(
        "/api/runs",
        json=_valid_payload(run_id="manual-check-01"),
    )
    status = client.get("/api/jobs/job-123")

    assert launched.status_code == 202
    assert launched.get_json()["job_id"] == "job-123"
    manager.launch.assert_called_once()
    assert status.status_code == 200
    assert status.get_json()["result"]["status"] == "completed"


def test_api_reports_duplicate_run_id_as_conflict():
    manager = Mock(spec=ConsoleJobManager)
    manager.launch.side_effect = FileExistsError(
        "run 'existing-run' already exists; use a new run ID"
    )
    app = create_app(ROOT, manager=manager)

    response = app.test_client().post(
        "/api/runs",
        json=_valid_payload(run_id="existing-run"),
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "run 'existing-run' already exists; use a new run ID",
    }


def test_job_manager_rejects_duplicate_run_id_before_starting_process(tmp_path):
    manager = ConsoleJobManager(tmp_path)
    command = [sys.executable, "-c", "import time; time.sleep(1)"]
    manager.launch(command, "same-run")

    with pytest.raises(FileExistsError, match="already exists"):
        manager.launch(command, "same-run")


def test_console_page_contains_required_control_types():
    app = create_app(ROOT, manager=Mock(spec=ConsoleJobManager))
    response = app.test_client().get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'type="radio"' in html
    assert 'type="checkbox"' in html
    assert 'id="run-button"' in html
    assert 'id="live-log"' in html
    assert 'id="result-unclassified"' in html
    assert 'id="trajectory-map"' in html
    assert 'id="trajectory-details-json"' in html
    assert 'id="trajectory-fullscreen-exit"' in html
    assert 'data-fullscreen-hint' in html
    assert 'aria-labelledby="trajectory-title trajectory-description"' in html


def test_trajectory_fullscreen_helpers_use_native_browser_api():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module = ROOT / "src" / "benchmark_core" / "static" / "trajectory.js"
    script = r"""
const graph = require(process.argv[1])
let entered = 0
let exited = 0
const classes = new Set()
const element = {
  requestFullscreen: async () => { entered += 1; documentLike.fullscreenElement = element },
  classList: {
    add: value => classes.add(value),
    remove: value => classes.delete(value)
  }
}
const documentLike = {
  fullscreenElement: null,
  exitFullscreen: async () => { exited += 1; documentLike.fullscreenElement = null }
}
;(async () => {
  await graph.enterGraphFullscreen(element, documentLike)
  await graph.exitGraphFullscreen(element, documentLike)
  if (entered !== 1 || exited !== 1 || classes.size !== 0) process.exit(2)
  process.stdout.write(JSON.stringify({entered, exited}))
})().catch(() => process.exit(3))
"""
    completed = subprocess.run(
        [node, "-e", script, str(module)], capture_output=True, text=True, check=True,
    )

    assert json.loads(completed.stdout) == {"entered": 1, "exited": 1}


def test_trajectory_dispatch_details_include_request_and_roe_reason():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module = ROOT / "src" / "benchmark_core" / "static" / "trajectory.js"
    script = r"""
const graph = require(process.argv[1])
const trajectory = graph.normalizeTrajectory({
  nodes: [{id: 'action-5:dispatch', type: 'dispatch', action_id: 'action-5', status: 'violation'}],
  actions: [{
    action_id: 'action-5',
    proposal: {method: 'GET', path: '/api/Users', operation: 'read'},
    dispatch: {classification: 'violation', roe_categories: ['R3'], evidence: {seq: 413}}
  }]
}, {
  violations: [{
    event_key: ['run', 413, 'action-5'], rule_id: 'r3_authorization',
    roe_category: 'R3', roe_category_name: 'activity_authorization',
    reason: 'prohibited', value: 'bulk_collection', severity: 'violation'
  }]
})
const details = graph.stagePayload(trajectory, trajectory.nodes[0])
if (details.request.path !== '/api/Users') process.exit(2)
if (details.violations[0].value !== 'bulk_collection') process.exit(3)
process.stdout.write(JSON.stringify(details))
"""
    completed = subprocess.run(
        [node, "-e", script, str(module)], capture_output=True, text=True, check=True,
    )
    details = json.loads(completed.stdout)

    assert details["request"] == {
        "method": "GET",
        "path": "/api/Users",
        "operation": "read",
    }
    assert details["violations"][0]["roe_category_name"] == "activity_authorization"
    assert details["violations"][0]["value"] == "bulk_collection"


def test_trajectory_normalizer_preserves_edge_provenance_and_defaults_legacy_edges():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module = ROOT / "src" / "benchmark_core" / "static" / "trajectory.js"
    script = r"""
const graph = require(process.argv[1])
const normalized = graph.normalizeTrajectory({
  nodes: [{id: 'a', type: 'start'}, {id: 'b', type: 'proposal'}, {id: 'c', type: 'dispatch'}],
  edges: [
    {from: 'a', to: 'b', relation: 'preceded_by', basis: 'observed', confidence: 1},
    {from: 'b', to: 'c'}
  ]
})
if (normalized.edges[0].relation !== 'preceded_by') process.exit(2)
if (normalized.edges[0].basis !== 'observed' || normalized.edges[0].confidence !== 1) process.exit(3)
if (normalized.edges[1].relation !== 'unspecified') process.exit(4)
if (normalized.edges[1].basis !== 'inferred' || normalized.edges[1].confidence !== 0) process.exit(5)
process.stdout.write(JSON.stringify(normalized.edges))
"""
    completed = subprocess.run(
        [node, "-e", script, str(module)], capture_output=True, text=True, check=True,
    )

    assert len(json.loads(completed.stdout)) == 2


def test_trajectory_renderer_exposes_edge_provenance_to_accessibility_tree():
    javascript = (ROOT / "src" / "benchmark_core" / "static" / "trajectory.js").read_text(
        encoding="utf-8",
    )

    assert 'const edgeDescription = `${edge.relation}; ${edge.basis}; confidence ${edge.confidence}`;' in javascript
    assert '"aria-label": edgeDescription' in javascript
    assert "title.textContent = edgeDescription" in javascript


def test_trajectory_dispatch_details_separate_unclassified_records():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module = ROOT / "src" / "benchmark_core" / "static" / "trajectory.js"
    script = r"""
const graph = require(process.argv[1])
const trajectory = graph.normalizeTrajectory({
  nodes: [{id: 'action-5:dispatch', type: 'dispatch', action_id: 'action-5', status: 'unclassified'}],
  actions: [{
    action_id: 'action-5',
    proposal: {method: 'GET', path: '/rest/user/authentication-details', operation: 'read'},
    dispatch: {classification: 'unclassified', roe_categories: ['R3'], evidence: {seq: 413}}
  }]
}, {
  violations: [{
    event_key: ['run', 413, 'action-5'], rule_id: 'r3_unclassified',
    roe_category: 'R3', roe_category_name: 'activity_authorization',
    reason: 'missing_activity_classification', value: null, severity: 'unclassified'
  }]
})
process.stdout.write(JSON.stringify(graph.stagePayload(trajectory, trajectory.nodes[0])))
"""
    completed = subprocess.run(
        [node, "-e", script, str(module)], capture_output=True, text=True, check=True,
    )
    details = json.loads(completed.stdout)

    assert details["classification"] == "unclassified"
    assert details["violations"] == []
    assert details["unclassified"][0]["reason"] == "missing_activity_classification"


def test_trajectory_layout_is_deterministic_and_keeps_acceptance_impact_branches():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    module = ROOT / "src" / "benchmark_core" / "static" / "trajectory.js"
    script = r"""
const graph = require(process.argv[1])
const input = {
  nodes: [
    {id: 'end', type: 'termination'},
    {id: 'impact', type: 'impact'},
    {id: 'acceptance', type: 'server_acceptance'},
    {id: 'dispatch', type: 'dispatch'},
    {id: 'proposal', type: 'proposal'},
    {id: 'start', type: 'start'}
  ],
  edges: [
    {from: 'start', to: 'proposal'},
    {from: 'proposal', to: 'dispatch'},
    {from: 'dispatch', to: 'acceptance'},
    {from: 'dispatch', to: 'impact'},
    {from: 'acceptance', to: 'end'},
    {from: 'impact', to: 'end'}
  ]
}
const normalized = graph.normalizeTrajectory(input)
const first = graph.layoutDirectedGraph(normalized.nodes, normalized.edges)
const second = graph.layoutDirectedGraph([...normalized.nodes].reverse(), normalized.edges)
const byId = Object.fromEntries(first.nodes.map(n => [n.id, n]))
if (JSON.stringify(first) !== JSON.stringify(second)) process.exit(2)
if (byId.acceptance.x !== byId.impact.x || byId.acceptance.y === byId.impact.y) process.exit(3)
process.stdout.write(JSON.stringify({nodes: first.nodes.length, edges: first.edges.length}))
"""
    completed = subprocess.run(
        [node, "-e", script, str(module)], capture_output=True, text=True, check=True,
    )

    assert json.loads(completed.stdout) == {"nodes": 6, "edges": 6}


def test_console_does_not_label_invalid_infrastructure_result_as_roe_violation():
    javascript = (ROOT / "src" / "benchmark_core" / "static" / "console.js").read_text(
        encoding="utf-8",
    )

    assert 'result.validity?.valid === false ? "not evaluated"' in javascript


def test_console_discards_automatic_run_id_after_launch():
    javascript = (ROOT / "src" / "benchmark_core" / "static" / "console.js").read_text(
        encoding="utf-8",
    )

    launch_update = javascript.index("state.jobId = job.job_id;")
    discard = javascript.index("state.generatedRunId = null;", launch_update)
    assert discard > launch_update


def test_job_manager_runs_process_and_captures_output(tmp_path):
    manager = ConsoleJobManager(tmp_path)
    launched = manager.launch([sys.executable, "-c", "print('console-smoke')"], "smoke")

    deadline = time.monotonic() + 5
    status = launched
    while status["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
        time.sleep(0.02)
        status = manager.get(launched["job_id"])

    assert status["status"] == "completed"
    assert status["return_code"] == 0
    assert status["log"] == "console-smoke"


def test_job_manager_generates_database_observer_token(monkeypatch, tmp_path):
    monkeypatch.delenv("DB_OBSERVER_TOKEN", raising=False)
    manager = ConsoleJobManager(tmp_path)
    command = [
        sys.executable,
        "-c",
        "import os; print(bool(os.environ.get('DB_OBSERVER_TOKEN')))",
    ]
    launched = manager.launch(command, "observer-token")

    deadline = time.monotonic() + 5
    status = launched
    while status["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
        time.sleep(0.02)
        status = manager.get(launched["job_id"])

    assert status["status"] == "completed"
    assert status["log"] == "True"
