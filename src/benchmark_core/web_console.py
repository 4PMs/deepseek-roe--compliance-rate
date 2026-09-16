from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import yaml
from flask import Flask, jsonify, render_template, request


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PROVIDER_OPTIONS = ({"id": "deepseek", "label": "DeepSeek API"},)


def _read_yaml(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"YAML document must be a mapping: {path}")
    return document


def discover_options(project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    scenarios_dir = project_root / "scenarios"
    scenarios: list[dict[str, Any]] = []
    for scenario_dir in sorted(path for path in scenarios_dir.iterdir() if path.is_dir()):
        scenario_path = scenario_dir / "scenario.yaml"
        if not scenario_path.is_file():
            continue
        scenario = _read_yaml(scenario_path)
        study = scenario.get("study") if isinstance(scenario.get("study"), dict) else {}
        experiment = (
            scenario.get("experiment") if isinstance(scenario.get("experiment"), dict) else {}
        )
        conditions: list[dict[str, Any]] = []
        conditions_dir = scenario_dir / "conditions"
        if conditions_dir.is_dir():
            for condition_path in sorted(conditions_dir.glob("*.yaml")):
                condition = _read_yaml(condition_path)
                conditions.append(
                    {
                        "id": str(condition.get("id", condition_path.stem)),
                        "group": str(condition.get("group", "unknown")),
                        "delivery_phase": str(condition.get("delivery_phase", "unspecified")),
                        "requested_operation": str(
                            condition.get("requested_operation", "unspecified")
                        ),
                        "target_resource": condition.get("target_resource"),
                        "instruction": str(condition.get("instruction", "")),
                    }
                )
        scenarios.append(
            {
                "id": scenario_dir.name,
                "status": str(scenario.get("status", "unspecified")),
                "role": str(study.get("role", "unspecified")),
                "eligibility": str(study.get("eligibility", "unspecified")),
                "rationale": str(study.get("rationale", "")),
                "experiment_enabled": bool(experiment.get("enabled", True)),
                "remaining_requirements": list(scenario.get("remaining_requirements", [])),
                "blockers": list(study.get("blockers", [])),
                "defaults": {
                    "max_steps": (scenario.get("limits") or {}).get("max_steps"),
                    "timeout": (scenario.get("limits") or {}).get("timeout"),
                },
                "conditions": conditions,
            }
        )
    return {
        "providers": list(_PROVIDER_OPTIONS),
        "scenarios": scenarios,
        "defaults": {
            "provider": "deepseek",
            "model": "deepseek-flash",
            "reset_target": True,
            "enforce_policy": False,
        },
    }


def _safe_identifier(value: Any, label: str, *, required: bool = True) -> str | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"{label} is required")
        return None
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"{label} must use only letters, numbers, '.', '_' or '-'")
    return value


def _optional_number(
    payload: Mapping[str, Any],
    name: str,
    cast: type[int] | type[float],
    *,
    minimum: float,
) -> int | float | None:
    value = payload.get(name)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        parsed = cast(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if parsed < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}")
    return parsed


def _scenario_metadata(project_root: Path, scenario_id: str) -> dict[str, Any]:
    options = discover_options(project_root)
    for scenario in options["scenarios"]:
        if scenario["id"] == scenario_id:
            return scenario
    raise ValueError(f"unknown scenario: {scenario_id}")


def validate_launch_eligibility(project_root: Path, payload: Mapping[str, Any]) -> None:
    scenario_id = _safe_identifier(payload.get("scenario"), "scenario")
    scenario = _scenario_metadata(project_root, scenario_id)
    eligibility = scenario["eligibility"]
    enabled = scenario["experiment_enabled"]
    acknowledged = payload.get("acknowledge_incomplete") is True
    if (eligibility != "eligible" or not enabled) and not acknowledged:
        detail = eligibility if enabled else f"{eligibility}; experiment disabled"
        raise PermissionError(
            f"Scenario {scenario_id} is {detail}. Check the incomplete-design acknowledgement "
            "only for an intentional diagnostic run."
        )


def build_runner_command(
    project_root: Path,
    payload: Mapping[str, Any],
) -> tuple[list[str], str]:
    scenario_id = _safe_identifier(payload.get("scenario"), "scenario")
    scenario = _scenario_metadata(project_root, scenario_id)
    condition_id = _safe_identifier(payload.get("condition", "neutral"), "condition")
    condition_ids = {condition["id"] for condition in scenario["conditions"]}
    if condition_id not in condition_ids:
        raise ValueError(f"unknown condition for {scenario_id}: {condition_id}")

    provider = _safe_identifier(payload.get("provider", "deepseek"), "provider")
    if provider != "deepseek":
        raise ValueError("provider must be deepseek for this study")
    model = _safe_identifier(payload.get("model", "deepseek-flash"), "model")
    run_id = _safe_identifier(payload.get("run_id"), "run ID", required=False)
    if run_id is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"console-{scenario_id}-{stamp}-{secrets.token_hex(2)}"

    command = [
        sys.executable,
        "-m",
        "benchmark_core.runner",
        "run",
        "--scenario",
        scenario_id,
        "--condition",
        condition_id,
        "--provider",
        provider,
        "--model",
        model,
        "--run",
        run_id,
        "--progress",
        "human",
    ]
    optional_text = {
        "model_version": "--model-version",
        "agent_version": "--agent-version",
    }
    for field, flag in optional_text.items():
        value = payload.get(field)
        if value not in (None, ""):
            command.extend((flag, _safe_identifier(value, field)))

    upstream = payload.get("upstream")
    if upstream not in (None, ""):
        if not isinstance(upstream, str):
            raise ValueError("upstream must be a URL")
        parsed = urlparse(upstream)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("upstream must be an http or https URL")
        command.extend(("--upstream", upstream))

    number_options = (
        ("temperature", float, 0.0, "--temperature"),
        ("seed", int, 0.0, "--seed"),
        ("repetition", int, 0.0, "--repetition"),
        ("max_steps", int, 1.0, "--max-steps"),
        ("timeout", int, 1.0, "--timeout"),
    )
    for field, cast, minimum, flag in number_options:
        value = _optional_number(payload, field, cast, minimum=minimum)
        if value is not None:
            command.extend((flag, str(value)))

    if payload.get("reset_target") is True:
        command.append("--reset-target")
    if payload.get("enforce_policy") is True:
        command.append("--enforce-policy")
    return command, run_id


def _display_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


class ConsoleJobManager:
    def __init__(self, project_root: Path = PROJECT_ROOT, *, max_log_lines: int = 3000):
        self.project_root = project_root
        self.max_log_lines = max_log_lines
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def launch(self, command: list[str], run_id: str) -> dict[str, Any]:
        job_id = f"job-{secrets.token_hex(6)}"
        job = {
            "job_id": job_id,
            "run_id": run_id,
            "status": "starting",
            "return_code": None,
            "command": _display_command(command),
            "log_lines": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        }
        with self._lock:
            run_exists = (self.project_root / "runs" / run_id).exists()
            run_known = any(item["run_id"] == run_id for item in self._jobs.values())
            if run_exists or run_known:
                raise FileExistsError(f"run {run_id!r} already exists; use a new run ID")
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run_process,
            args=(job_id, command),
            daemon=True,
            name=f"console-{job_id}",
        )
        thread.start()
        return self.get(job_id)

    def _run_process(self, job_id: str, command: list[str]) -> None:
        env = os.environ.copy()
        env.setdefault("DB_OBSERVER_TOKEN", secrets.token_hex(32))
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                command,
                cwd=self.project_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
            self._update(job_id, status="running", pid=process.pid)
            assert process.stdout is not None
            for line in process.stdout:
                self._append_log(job_id, line.rstrip("\r\n"))
            return_code = process.wait()
            self._update(
                job_id,
                status="completed" if return_code == 0 else "failed",
                return_code=return_code,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            self._append_log(job_id, f"Console launch failed: {type(exc).__name__}: {exc}")
            self._update(
                job_id,
                status="failed",
                return_code=None,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )

    def _append_log(self, job_id: str, line: str) -> None:
        with self._lock:
            lines = self._jobs[job_id]["log_lines"]
            lines.append(line)
            if len(lines) > self.max_log_lines:
                del lines[: len(lines) - self.max_log_lines]

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            self._jobs[job_id].update(changes)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = dict(self._jobs[job_id])
            log_lines = list(job.pop("log_lines"))
        result_path = self.project_root / "runs" / job["run_id"] / "result.json"
        result = None
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                result = None
        job["log"] = "\n".join(log_lines)
        job["result"] = result
        job["artifact_path"] = str(result_path.parent) if result_path.parent.exists() else None
        return job


def create_app(
    project_root: Path = PROJECT_ROOT,
    *,
    manager: ConsoleJobManager | None = None,
) -> Flask:
    app = Flask(__name__)
    job_manager = manager or ConsoleJobManager(project_root)

    @app.get("/")
    def index():
        return render_template("console.html")

    @app.get("/api/options")
    def options():
        return jsonify(discover_options(project_root))

    @app.post("/api/preview")
    def preview():
        try:
            payload = request.get_json(silent=False) or {}
            command, run_id = build_runner_command(project_root, payload)
            return jsonify({"run_id": run_id, "command": _display_command(command)})
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/runs")
    def launch_run():
        try:
            payload = request.get_json(silent=False) or {}
            validate_launch_eligibility(project_root, payload)
            command, run_id = build_runner_command(project_root, payload)
            return jsonify(job_manager.launch(command, run_id)), 202
        except PermissionError as exc:
            return jsonify({"error": str(exc)}), 409
        except FileExistsError as exc:
            return jsonify({"error": str(exc)}), 409
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str):
        try:
            return jsonify(job_manager.get(job_id))
        except KeyError:
            return jsonify({"error": "job not found"}), 404

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok"})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Local ROE benchmark browser console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8760, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    create_app(PROJECT_ROOT).run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
