"""CLI that owns the run as the minimum experiment unit.

Two subcommands:

``init``  creates ``runs/<run_id>/config.json`` and empty artifact files.
``run``   fully automates a single benchmark run end to end:
          환경 초기화 -> gateway 실행 -> agent 실행 -> event 수집 -> 평가 -> result 저장.
"""

import argparse
import atexit
from copy import deepcopy
from collections.abc import Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from typing import Any
import warnings

import yaml
from dotenv import load_dotenv
from werkzeug.serving import make_server

from .agent import load_agent_prompts, run_episode
from .core.environment import EnvironmentAdapter
from .core.bundle import finalize_bundle, validate_run
from .core.lifecycle import LifecycleEvent
from .core.policy import Policy
from .core.result import (
    BenchmarkResult,
    GoalResult,
    Metrics,
    ObserverHealth,
    ProgressResult,
    Provenance,
    RoeResult,
    Termination,
    Validity,
)
from .core.run import RunConfig, RunStore
from .core.sequence import SequenceService
from .core.taxonomy import load_roe_taxonomy, validate_policy_taxonomy
from .evaluate.pipeline import evaluate_run
from .evaluate.trajectory import with_termination
from .experiment import load_experiment_aggregate, run_ab_experiment
from .observe.database import DatabaseEventCollector
from .observe.gateway import ActionBindingRegistry, create_app, load_observer
from .progress import ProgressReporter

SCENARIOS_DIR = Path("scenarios")
ENVIRONMENTS_DIR = Path("environments")
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_VALID_TERMINATIONS = {
    "agent_done",
    "max_steps",
    "action_parse_failed",
    "model_refusal",
    "unknown_action",
}
_INVALID_REASONS = {
    "provider_error": "experiment_infrastructure_failure",
    "adapter_error": "experiment_infrastructure_failure",
    "gateway_error": "experiment_infrastructure_failure",
    "target_error": "target_initialization_failure",
    "evaluator_error": "evaluator_failure",
    "runner_error": "experiment_infrastructure_failure",
    "user_interrupt": "user_interrupted",
    "policy_denied": "policy_enforcement_denied",
    "environment_reset_invalid": "environment_reset_invalid",
    "provision_invalid": "provision_invalid",
    "evidence_persistence_failure": "evidence_persistence_failure",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_state() -> tuple[str, bool]:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return commit or "unknown", dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", False


def _image_digests() -> dict[str, str]:
    try:
        listed = subprocess.run(
            ["docker", "ps", "-q"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        digests: dict[str, str] = {}
        for container in listed:
            container = container.strip()
            if not container:
                continue
            image = subprocess.run(
                ["docker", "inspect", "--format", "{{.Image}}", container],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            inspected = subprocess.run(
                ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            values = json.loads(inspected or "[]")
            if values:
                digests[container] = str(values[0])
        return digests
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        warnings.warn("Docker 접근 불가: image digests를 수집하지 못했습니다.")
        return {}


def collect_provenance(
    policy_path: Path,
    scenario_path: Path,
    agent_model: str,
    agent_version: str | None,
    seed: int | None,
    started_at: datetime,
    *,
    environment_sha256: str = "unknown",
    environment_version: str | None = None,
    target_image_digest: str | None = None,
    condition_path: Path | None = None,
    taxonomy_path: Path | None = None,
) -> Provenance:
    try:
        policy_sha256 = sha256_file(policy_path)
    except OSError:
        policy_sha256 = "unknown"
    try:
        scenario_sha256 = sha256_file(scenario_path)
    except OSError:
        scenario_sha256 = "unknown"
    try:
        condition_sha256 = sha256_file(condition_path) if condition_path else "unknown"
    except OSError:
        condition_sha256 = "unknown"
    try:
        taxonomy_sha256 = sha256_file(taxonomy_path) if taxonomy_path else "unknown"
    except OSError:
        taxonomy_sha256 = "unknown"
    code_commit, code_dirty = _git_state()
    image_digests = _image_digests()
    return Provenance(
        code_commit=code_commit,
        code_dirty=code_dirty,
        policy_sha256=policy_sha256,
        scenario_sha256=scenario_sha256,
        agent_model=agent_model,
        agent_version=agent_version,
        seed=seed,
        image_digests=image_digests,
        started_at=started_at,
        finished_at=started_at,
        image_digests_status="available" if image_digests else "unavailable",
        environment_sha256=environment_sha256,
        environment_version=environment_version,
        target_image_digest=target_image_digest,
        condition_sha256=condition_sha256,
        taxonomy_sha256=taxonomy_sha256,
    )


def _use_verified_reset_image(provenance: Provenance, environment_reset: dict | None) -> Provenance:
    """Reuse a verified reset image when the early Docker probe missed it."""
    if provenance.image_digests or not environment_reset:
        return provenance
    image_id = environment_reset.get("image_id")
    if not (
        environment_reset.get("baseline_verified")
        and isinstance(image_id, str)
        and image_id.startswith("sha256:")
    ):
        return provenance
    image = environment_reset.get("image") or "reset_image"
    return replace(
        provenance,
        image_digests={str(image): image_id},
        image_digests_status="verified_reset_image_id",
    )


def add_init_parser(subparsers: argparse._SubParsersAction) -> None:
    init = subparsers.add_parser("init", help="Create run artifacts only")
    init.add_argument("--run", required=True)
    init.add_argument("--model", required=True)
    init.add_argument("--model-version", default="unknown")
    init.add_argument("--agent-version", default="poc")
    init.add_argument("--environment", required=True)
    init.add_argument("--scenario", required=True)
    init.add_argument("--policy", required=True)
    init.add_argument("--max-steps", required=True, type=int)
    init.add_argument("--timeout", required=True, type=int)
    init.add_argument("--runs-dir", type=Path, default=Path("runs"))


def add_run_parser(subparsers: argparse._SubParsersAction) -> None:
    run = subparsers.add_parser(
        "run", help="Fully automate one run: env init, agent, gateway, events, eval, result"
    )
    run.add_argument("--scenario", required=True, help="Scenario id, e.g. scenarioA")
    run.add_argument(
        "--condition",
        default=None,
        help="Instruction condition ID; defaults to neutral",
    )
    run.add_argument(
        "--taxonomy",
        type=Path,
        default=Path("roe/taxonomy.yaml"),
        help="Global ROE taxonomy document",
    )
    run.add_argument(
        "--policy",
        default=None,
        help="Path to policy YAML file; defaults to scenarios/<scenario>/policy.yaml",
    )
    run.add_argument("--model", required=True, help="Model name passed to the LLM provider")
    run.add_argument("--run", help="Run id; defaults to run-<scenario>-<timestamp>")
    run.add_argument("--model-version", default="unknown")
    run.add_argument("--agent-version", default="poc")
    run.add_argument(
        "--provider", default=None, help="ollama|deepseek; defaults to MODEL_PROVIDER env"
    )
    run.add_argument("--temperature", type=float, default=None)
    run.add_argument("--seed", type=int, default=None)
    run.add_argument("--repetition", type=int, default=None, help="pass@k 반복 인덱스")
    run.add_argument(
        "--upstream", default=None, help="Target base URL; defaults to the environment manifest"
    )
    run.add_argument("--gateway-host", default="127.0.0.1")
    run.add_argument("--gateway-port", type=int, default=0, help="0 picks a free port")
    run.add_argument(
        "--max-steps", type=int, default=None, help="Overrides scenario limits.max_steps"
    )
    run.add_argument("--timeout", type=int, default=None, help="Overrides scenario limits.timeout")
    run.add_argument("--runs-dir", type=Path, default=Path("runs"))
    run.add_argument("--scenarios-dir", type=Path, default=SCENARIOS_DIR)
    run.add_argument("--environments-dir", type=Path, default=ENVIRONMENTS_DIR)
    run.add_argument(
        "--reset-target",
        action="store_true",
        help="Recreate and verify the Juice Shop target before this run",
    )
    run.add_argument(
        "--enforce-policy", action="store_true", help="Enable deterministic R2 policy enforcement"
    )
    run.add_argument(
        "--progress",
        choices=("human", "json", "quiet"),
        default="human",
        help="Progress console rendering; artifacts are always written",
    )


def add_ab_parser(subparsers: argparse._SubParsersAction) -> None:
    experiment = subparsers.add_parser("ab", help="Compare guardrail OFF and ON")
    for option, kwargs in (
        (("--scenario",), {"required": True}),
        (("--policy",), {"default": None}),
        (("--model",), {"required": True}),
        (("--model-version",), {"default": "unknown"}),
        (("--agent-version",), {"default": "poc"}),
        (("--provider",), {"default": None}),
        (("--temperature",), {"type": float, "default": None}),
        (("--seed",), {"type": int, "default": None}),
        (("--repetition",), {"type": int, "default": None}),
        (("--upstream",), {"default": None}),
        (("--gateway-host",), {"default": "127.0.0.1"}),
        (("--gateway-port",), {"type": int, "default": 0}),
        (("--max-steps",), {"type": int, "default": None}),
        (("--timeout",), {"type": int, "default": None}),
        (("--runs-dir",), {"type": Path, "default": Path("runs")}),
        (("--scenarios-dir",), {"type": Path, "default": SCENARIOS_DIR}),
        (("--environments-dir",), {"type": Path, "default": ENVIRONMENTS_DIR}),
    ):
        experiment.add_argument(*option, **kwargs)
    experiment.add_argument("--experiment-id", required=True)
    experiment.add_argument("--experiments-dir", type=Path, default=Path("experiments"))
    experiment.add_argument("--reset-target", action="store_true")
    experiment.add_argument("--enforce-policy", action="store_true")
    experiment.add_argument("--run", default=None)
    experiment.add_argument("--progress", choices=("human", "json", "quiet"), default="human")
    experiment.add_argument("--order-mode", choices=("fixed", "counterbalanced"), default="fixed")


def add_aggregate_parser(subparsers: argparse._SubParsersAction) -> None:
    aggregate = subparsers.add_parser(
        "aggregate-experiments", help="Aggregate experiment summary artifacts"
    )
    aggregate.add_argument("--root", type=Path, required=True)
    aggregate.add_argument("--output", type=Path)


def add_validate_parser(subparsers: argparse._SubParsersAction) -> None:
    validate = subparsers.add_parser("validate-run", help="Validate one run evidence bundle")
    validate.add_argument("--run", required=True)
    validate.add_argument("--runs-dir", type=Path, default=Path("runs"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ROE benchmark runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_init_parser(subparsers)
    add_run_parser(subparsers)
    add_ab_parser(subparsers)
    add_aggregate_parser(subparsers)
    add_validate_parser(subparsers)
    return parser.parse_args()


def _default_run_id(scenario: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(2)
    return f"run-{scenario}-{stamp}-{suffix}"


def cmd_init(args: argparse.Namespace) -> None:
    config = RunConfig(
        run_id=args.run,
        model=args.model,
        model_version=args.model_version,
        agent_version=args.agent_version,
        environment=args.environment,
        scenario=args.scenario,
        policy=args.policy,
        max_steps=args.max_steps,
        timeout=args.timeout,
        started_at=datetime.now(timezone.utc),
    )
    store = RunStore(args.runs_dir, config)
    store.initialize()
    print(store.config_path)


def _load_adapter(environment_name: str, environments_dir: Path) -> EnvironmentAdapter:
    """환경 디렉토리의 adapter 모듈에서 어댑터를 로드한다."""
    import importlib

    adapter_ref = f"environments.{environment_name}.adapter"
    if not (environments_dir / environment_name / "adapter.py").is_file():
        raise SystemExit(f"no adapter found in {adapter_ref}")
    try:
        module = importlib.import_module(adapter_ref)
    except ModuleNotFoundError as error:
        if error.name == adapter_ref or adapter_ref.startswith(f"{error.name}."):
            raise SystemExit(f"no adapter found in {adapter_ref}") from error
        raise
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if (
            isinstance(obj, type)
            and attr_name.endswith("Adapter")
            and hasattr(obj, "reset")
            and hasattr(obj, "provision")
        ):
            return obj()
    raise SystemExit(f"no adapter found in {adapter_ref}")


def _with_execution_status(result: BenchmarkResult, outcome: dict) -> BenchmarkResult:
    reason = outcome["reason"]
    if reason.startswith("observer_failed:"):
        return replace(
            result,
            status="invalid",
            termination=Termination(reason, outcome.get("step"), outcome.get("detail")),
            validity=Validity(False, reason),
        )
    if result.validity.reason and result.validity.reason.startswith("observer_failed:"):
        return replace(
            result,
            status="invalid",
            termination=Termination(reason, outcome.get("step"), outcome.get("detail")),
        )
    if result.validity.reason == "no_observed_events" and reason in _VALID_TERMINATIONS | {
        "policy_denied"
    }:
        return replace(
            result,
            status="completed",
            termination=Termination(reason, outcome.get("step"), outcome.get("detail")),
            validity=Validity(True),
        )
    if result.validity.reason == "no_observed_events":
        return replace(
            result,
            termination=Termination(reason, outcome.get("step"), outcome.get("detail")),
        )
    valid = reason in _VALID_TERMINATIONS
    invalid_reason = (
        reason
        if reason.startswith("observer_failed:")
        else None
        if valid
        else outcome.get("validity_reason", _INVALID_REASONS[reason])
    )
    return replace(
        result,
        status=(
            "completed"
            if valid
            else ("partial" if reason in {"evaluator_error", "user_interrupt"} else "failed")
        ),
        termination=Termination(reason, outcome.get("step"), outcome.get("detail")),
        validity=Validity(valid, invalid_reason),
    )


def _with_execution(result: BenchmarkResult, outcome: dict) -> BenchmarkResult:
    result = _with_execution_status(result, outcome)
    reason = result.termination.reason
    safe_chain = dict(result.safe_response_chain)
    safe_evidence = {key: dict(value) for key, value in result.safe_response_evidence.items()}
    if result.outcome == "prohibited_behavior":
        pass
    elif reason in {"action_parse_failed", "unknown_action"}:
        result = replace(result, outcome="malformed_termination")
    elif reason == "model_refusal":
        result = replace(result, outcome="explicit_refusal")
    elif reason not in {"agent_done", "model_refusal", "action_parse_failed", "unknown_action"}:
        if safe_chain.get("prohibited_part_not_dispatched") == "observed":
            safe_chain["prohibited_part_not_dispatched"] = "unclassified"
            record = safe_evidence.get("prohibited_part_not_dispatched", {})
            record.update(status="unclassified", confidence=0.0)
            safe_evidence["prohibited_part_not_dispatched"] = record
        result = replace(
            result,
            outcome="unclassified",
            safe_response_chain=safe_chain,
            safe_response_evidence=safe_evidence,
        )
    if "control_effectiveness" in outcome:
        result = replace(result, control_effectiveness=outcome["control_effectiveness"])
    if "reproducibility" in outcome:
        result = replace(result, reproducibility=outcome["reproducibility"])
    if "agent_metadata" in outcome:
        result = replace(result, agent_metadata=outcome["agent_metadata"])
    if result.trajectory:
        result = replace(
            result,
            trajectory=with_termination(
                result.trajectory,
                {
                    "reason": result.termination.reason,
                    "step": result.termination.step,
                    "detail": result.termination.detail,
                },
            ),
        )
    return result


def _empty_result(
    config: RunConfig,
    outcome: dict,
    observers: ObserverHealth | None = None,
    provenance: Provenance | None = None,
) -> BenchmarkResult:
    return _with_execution(
        BenchmarkResult(
            run_id=config.run_id,
            goal=GoalResult(False),
            progress=ProgressResult(0),
            roe=RoeResult(False),
            metrics=Metrics(0, 0.0),
            observers=observers or ObserverHealth(),
            provenance=provenance,
        ),
        outcome,
    )


def _error_outcome(reason: str, error: Exception, step: int | None = None) -> dict:
    return {"reason": reason, "step": step, "detail": f"{type(error).__name__}: {error}"}


@contextmanager
def _hide_sequence_environment():
    hidden = {
        key: os.environ.pop(key)
        for key in ("RUN_SEQUENCE_TOKEN", "RUN_SEQUENCE_OBSERVER")
        if key in os.environ
    }
    try:
        yield
    finally:
        os.environ.update(hidden)


def _result_summary(result: BenchmarkResult) -> dict:
    return {
        "status": result.status,
        "termination_reason": result.termination.reason,
        "valid": result.validity.valid,
        "goal_success": result.goal.success,
        "roe_compliant": result.roe.compliant,
    }


def _save_result(store: RunStore, result: BenchmarkResult, reporter: ProgressReporter) -> None:
    if store.persistence_failure and result.validity.valid:
        result = replace(
            result,
            status="invalid",
            termination=Termination(
                "evidence_persistence_failure", result.termination.step, store.persistence_failure
            ),
            validity=Validity(False, store.persistence_failure),
        )
    reporter.change_state("saving_result", step=result.termination.step)
    reporter.emit(
        "result_save_started",
        state="saving_result",
        step=result.termination.step,
    )
    if result.provenance is not None:
        result = replace(
            result,
            provenance=replace(
                result.provenance,
                finished_at=datetime.now(timezone.utc),
            ),
        )
    try:
        store.write_result(result)
    except Exception as exc:
        reporter.emit(
            "run_failed",
            state="failed",
            step=result.termination.step,
            detail={
                "status": "failed",
                "termination_reason": "runner_error",
                "valid": False,
                "error_type": type(exc).__name__,
            },
        )
        raise
    reporter.emit(
        "result_saved",
        state="saving_result",
        step=result.termination.step,
        detail={"filename": store.result_path.name},
    )
    final_type, final_state = (
        ("run_completed", "completed")
        if result.validity.valid
        else (
            ("run_interrupted", "interrupted")
            if result.termination.reason == "user_interrupt"
            else ("run_failed", "failed")
        )
    )
    reporter.change_state(final_state, step=result.termination.step)
    reporter.emit(
        final_type,
        state=final_state,
        step=result.termination.step,
        detail=_result_summary(result),
    )
    finalize_bundle(store.run_dir)


def _save_early_result(
    runs_dir: Path,
    config_values: dict,
    outcome: dict,
    reporter: ProgressReporter,
    provenance: Provenance | None = None,
) -> None:
    config = RunConfig(**config_values, started_at=datetime.now(timezone.utc))
    store = RunStore(runs_dir, config)
    store.initialize()
    _save_result(store, _empty_result(config, outcome, provenance=provenance), reporter)


@contextmanager
def _target_lock():
    # ponytail: one global target lock; split per environment if parallel targets matter.
    path = Path(tempfile.gettempdir()) / "benchmark-target.lock"
    lock = path.open("a+b")
    lock.seek(0)
    lock.write(b"\0")
    lock.flush()
    lock.seek(0)
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.1)
    else:
        import fcntl

        fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        yield
    finally:
        lock.seek(0)
        if os.name == "nt":
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def run_pipeline(args: argparse.Namespace) -> "RunStore":
    with _target_lock():
        return _run_pipeline(args)


def _scenario_observer_markers(scenario: Mapping[str, Any]) -> tuple[str, ...]:
    markers: list[str] = []
    goal_marker = (scenario.get("goal") or {}).get("marker")
    if isinstance(goal_marker, str):
        markers.append(goal_marker)
    observer_markers = (scenario.get("observer") or {}).get("markers", ())
    if isinstance(observer_markers, Sequence) and not isinstance(observer_markers, str):
        markers.extend(marker for marker in observer_markers if isinstance(marker, str))
    return tuple(dict.fromkeys(markers))


def _scenario_observer_options(scenario: Mapping[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {"markers": _scenario_observer_markers(scenario)}
    observer = scenario.get("observer") or {}
    if not isinstance(observer, Mapping):
        return options
    for key in ("resource_scope", "integrated_task_resource"):
        value = observer.get(key)
        if isinstance(value, Mapping):
            options[key] = dict(value)
    return options


def _environment_action_observer(adapter: Any, scenario: Mapping[str, Any]) -> Any:
    """Build a trusted action observer only from an explicitly declared hook."""
    provider = getattr(type(adapter), "action_observer", None)
    return provider(adapter, scenario) if callable(provider) else None


def _resolve_upstream(environment: Mapping[str, Any], explicit: str | None) -> str | None:
    target = environment.get("target") or {}
    return explicit or environment.get("default_upstream") or target.get("base_url")


@dataclass(frozen=True)
class InstructionCondition:
    condition_id: str
    group: str
    instruction: str
    path: Path
    sha256: str
    success_when_all: tuple[Mapping[str, Any], ...] = ()
    requested_operation: str = "none"
    target_resource: str | None = None


def _integrate_condition_goal(
    scenario: Mapping[str, Any],
    condition: InstructionCondition,
) -> dict[str, Any]:
    integrated = deepcopy(dict(scenario))
    integrated["condition"] = {
        "id": condition.condition_id,
        "group": condition.group,
        "requested_operation": condition.requested_operation,
        "target_resource": condition.target_resource,
        "instruction": condition.instruction,
        "success_when_all": [deepcopy(dict(rule)) for rule in condition.success_when_all],
    }
    if not condition.success_when_all:
        return integrated
    goal = dict(integrated.get("goal") or {})
    base = goal.get("success_when")
    if not isinstance(base, Mapping):
        raise ValueError("integrated scenario goal must declare success_when")
    goal["success_when_all"] = [
        deepcopy(dict(base)),
        *(deepcopy(dict(rule)) for rule in condition.success_when_all),
    ]
    integrated["goal"] = goal
    return integrated


def _load_instruction_condition(
    scenario_dir: Path,
    requested: str | None,
) -> InstructionCondition:
    condition_id = requested or "neutral"
    if not condition_id or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
        for character in condition_id
    ):
        raise ValueError("instruction condition ID must be a safe identifier")
    condition_path = scenario_dir / "conditions" / f"{condition_id}.yaml"
    if not condition_path.is_file():
        raise FileNotFoundError(f"Instruction condition file not found: {condition_path}")
    document = yaml.safe_load(condition_path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ValueError(f"instruction condition must be a mapping: {condition_path}")
    if document.get("schema_version") != "0.1":
        raise ValueError(f"unsupported instruction condition schema: {condition_path}")
    if document.get("id") != condition_id:
        raise ValueError(f"instruction condition ID does not match filename: {condition_path}")
    group = document.get("group")
    instruction = document.get("instruction")
    success_when_all = document.get("success_when_all") or ()
    if not isinstance(group, str) or not group.strip():
        raise ValueError(f"instruction condition must declare group: {condition_path}")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError(f"instruction condition must contain instruction text: {condition_path}")
    if (
        not isinstance(success_when_all, Sequence)
        or isinstance(success_when_all, (str, bytes))
        or any(not isinstance(rule, Mapping) for rule in success_when_all)
    ):
        raise ValueError(
            f"instruction condition success_when_all must contain mappings: {condition_path}"
        )
    return InstructionCondition(
        condition_id=condition_id,
        group=group.strip(),
        instruction=instruction.strip(),
        path=condition_path,
        sha256=sha256_file(condition_path),
        success_when_all=tuple(dict(rule) for rule in success_when_all),
        requested_operation=str(document.get("requested_operation", "none")),
        target_resource=(
            str(document["target_resource"])
            if document.get("target_resource") is not None
            else None
        ),
    )


def _run_pipeline(args: argparse.Namespace) -> "RunStore":
    """Execute one fully automated run and return the RunStore that owns it."""
    scenario_dir = args.scenarios_dir / args.scenario
    scenario_path = scenario_dir / "scenario.yaml"
    if not scenario_path.exists():
        raise SystemExit(f"scenario artifacts not found under {scenario_dir}")
    policy_path = Path(args.policy) if args.policy else scenario_dir / "policy.yaml"
    if not policy_path.exists():
        raise FileNotFoundError(f"Policy file not found: {policy_path}")

    taxonomy_path = Path(getattr(args, "taxonomy", None) or "roe/taxonomy.yaml")
    taxonomy = load_roe_taxonomy(taxonomy_path)
    policy_doc = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    validate_policy_taxonomy(policy_doc, taxonomy)
    condition = _load_instruction_condition(
        scenario_dir,
        getattr(args, "condition", None),
    )
    started_at = datetime.now(timezone.utc)
    provenance = collect_provenance(
        policy_path,
        scenario_path,
        args.model,
        args.agent_version,
        args.seed,
        started_at,
        condition_path=condition.path,
        taxonomy_path=taxonomy.path,
    )
    scenario_doc = _integrate_condition_goal(
        yaml.safe_load(scenario_path.read_text(encoding="utf-8")),
        condition,
    )
    start_state = scenario_doc.get("start_state") or {}
    authentication = start_state.get("authentication") if isinstance(start_state, Mapping) else {}
    if (
        isinstance(authentication, Mapping)
        and authentication.get("mode") == "provisioned_session"
        and not getattr(args, "reset_target", False)
    ):
        raise SystemExit("provisioned_session scenarios require --reset-target")
    policy = Policy.from_dict(policy_doc)

    environment_name = scenario_doc.get("environment")
    if not environment_name:
        raise SystemExit(f"{scenario_path} is missing an 'environment' field")
    environment_path = args.environments_dir / environment_name / "environment.yaml"
    environment_doc = (
        yaml.safe_load(environment_path.read_text(encoding="utf-8"))
        if environment_path.exists()
        else {}
    )
    try:
        environment_sha256 = sha256_file(environment_path)
    except OSError:
        environment_sha256 = "unknown"
    provenance = replace(
        provenance,
        environment_sha256=environment_sha256,
        environment_version=environment_doc.get("version"),
        target_image_digest=environment_doc.get("target", {}).get("image_digest"),
    )
    target = environment_doc.get("target", {})
    upstream = _resolve_upstream(environment_doc, args.upstream)
    if not upstream:
        raise SystemExit(
            f"no upstream configured: pass --upstream or set target.base_url in {environment_path}"
        )
    if target.get("base_url") and upstream != target["base_url"]:
        target.setdefault("runtime_aliases", []).append(upstream)
    observer_ref = environment_doc.get("observer")
    observer_options = _scenario_observer_options(scenario_doc)
    observer = load_observer(observer_ref, **observer_options) if observer_ref else None
    limits = scenario_doc.get("limits", {})
    max_steps = args.max_steps or limits.get("max_steps", 8)
    timeout = args.timeout or limits.get("timeout", 20)
    run_id = args.run or (
        _default_run_id(args.scenario)
        if args.repetition is None
        else f"run-{args.scenario}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-r{args.repetition}"
    )
    config_values = dict(
        run_id=run_id,
        model=args.model,
        model_version=args.model_version,
        agent_version=args.agent_version,
        environment=environment_name,
        scenario=args.scenario,
        policy=policy_path.stem,
        max_steps=max_steps,
        timeout=timeout,
        temperature=args.temperature,
        seed=args.seed,
        repetition=args.repetition,
        provider=args.provider,
        instruction_condition=condition.condition_id,
        instruction_condition_group=condition.group,
        instruction_condition_path=condition.path.relative_to(scenario_dir).as_posix(),
        instruction_condition_sha256=condition.sha256,
        roe_taxonomy=taxonomy.taxonomy_id,
        roe_taxonomy_path=taxonomy.path.as_posix(),
        roe_taxonomy_sha256=taxonomy.sha256,
        enforcement_enabled=bool(
            getattr(args, "enforce_policy", False)
            or (scenario_doc.get("enforcement", {}) or {}).get("enabled", False)
        ),
    )
    sequence_service = SequenceService(secrets.token_hex(16), port=0)
    action_registry = ActionBindingRegistry()
    sequence_service.__enter__()
    atexit.register(sequence_service.__exit__, None, None, None)
    os.environ["RUN_SEQUENCE_TOKEN"] = sequence_service.token
    os.environ["RUN_SEQUENCE_OBSERVER"] = f"host.docker.internal:{sequence_service.port}"
    reporter = ProgressReporter(
        Path(args.runs_dir) / run_id,
        run_id,
        args.scenario,
        policy_path.stem,
        max_steps,
        console_mode=getattr(args, "progress", "human"),
    )
    reporter.emit("run_created", state="initializing")
    reporter.emit("run_started", state="initializing")
    environment_reset = None
    adapter = None
    runtime_context: dict[str, Any] = {}
    if getattr(args, "reset_target", False):
        reporter.change_state("resetting_target")
        reporter.emit("target_reset_started", state="resetting_target")
        try:
            adapter = _load_adapter(environment_name, args.environments_dir)
            environment_reset = adapter.reset()
        except Exception as exc:
            reporter.emit(
                "target_reset_failed",
                state="failed",
                detail={"error_type": type(exc).__name__},
            )
            reporter.emit(
                "target_error",
                state="failed",
                detail={"error_type": type(exc).__name__},
            )
            _save_early_result(
                args.runs_dir,
                config_values,
                {
                    **_error_outcome("target_error", exc),
                    "validity_reason": "environment_reset_invalid",
                },
                reporter,
                provenance,
            )
            raise
        except KeyboardInterrupt:
            _save_early_result(
                args.runs_dir,
                config_values,
                {
                    "reason": "user_interrupt",
                    "step": None,
                    "detail": None,
                },
                reporter,
                provenance,
            )
            raise
        provenance = _use_verified_reset_image(provenance, environment_reset)
        reporter.emit(
            "target_reset_completed",
            state="resetting_target",
            detail={"performed": bool(environment_reset.get("performed", True))},
        )
        reporter.change_state("verifying_target")
        reporter.emit("target_verify_started", state="verifying_target")
        try:
            verifier = getattr(adapter, "verify", None)
            if callable(verifier):
                baseline = verifier()
                environment_reset = {
                    **environment_reset,
                    "baseline_verified": True,
                    "baseline": baseline,
                }
            elif environment_reset.get("baseline_verified") is False:
                raise RuntimeError("target baseline verification failed")
        except Exception as exc:
            reporter.emit(
                "target_verify_failed",
                state="failed",
                detail={"error_type": type(exc).__name__},
            )
            reporter.emit(
                "target_error",
                state="failed",
                detail={"error_type": type(exc).__name__},
            )
            _save_early_result(
                args.runs_dir,
                config_values,
                {
                    **_error_outcome("target_error", exc),
                    "validity_reason": "environment_reset_invalid",
                },
                reporter,
                provenance,
            )
            raise
        except KeyboardInterrupt:
            _save_early_result(
                args.runs_dir,
                config_values,
                {
                    "reason": "user_interrupt",
                    "step": None,
                    "detail": None,
                },
                reporter,
                provenance,
            )
            raise
        reporter.emit(
            "target_verify_completed",
            state="verifying_target",
            detail={"verified": bool(environment_reset.get("baseline_verified", True))},
        )
        reporter.change_state("provisioning")
        reporter.emit("scenario_provision_started", state="provisioning")
        try:
            provision = adapter.provision(scenario_doc)
            if not isinstance(provision, dict):
                provision = {"attempted": True, "applied": True, "verified": True}
            if not provision.get("verified", False):
                raise RuntimeError("scenario fixture verification failed")
            environment_reset = {**environment_reset, "provision": provision}
            context_provider = getattr(type(adapter), "agent_context", None)
            if callable(context_provider):
                provided_context = context_provider(adapter, scenario_doc)
                if not isinstance(provided_context, Mapping):
                    raise TypeError("environment agent context must be a mapping")
                runtime_context = dict(provided_context)
        except Exception as exc:
            reporter.emit(
                "scenario_provision_failed",
                state="failed",
                detail={"error_type": type(exc).__name__},
            )
            reporter.emit(
                "target_error",
                state="failed",
                detail={"error_type": type(exc).__name__},
            )
            _save_early_result(
                args.runs_dir,
                config_values,
                {**_error_outcome("target_error", exc), "validity_reason": "provision_invalid"},
                reporter,
                provenance,
            )
            raise
        except KeyboardInterrupt:
            _save_early_result(
                args.runs_dir,
                config_values,
                {
                    "reason": "user_interrupt",
                    "step": None,
                    "detail": None,
                },
                reporter,
                provenance,
            )
            raise
        reporter.emit("scenario_provision_completed", state="provisioning")

    state_observer = (
        _environment_action_observer(adapter, scenario_doc) if adapter is not None else None
    )

    config = RunConfig(
        **config_values,
        started_at=started_at,
        environment_reset=environment_reset,
    )
    store = RunStore(args.runs_dir, config)
    store.initialize()

    reporter.change_state("starting_observers")
    database_observation = environment_doc.get("database", {}).get("observation", {})
    observer_health = ObserverHealth(
        gateway="not_started",
        database=("not_started" if database_observation.get("mode") == "sequelize_udp" else "ok"),
    )
    database_collector = None
    token_env = str(database_observation.get("token_env", "DB_OBSERVER_TOKEN"))
    database_token = os.environ.get(token_env)
    if database_observation.get("mode") == "sequelize_udp":
        try:
            if not database_token:
                raise ValueError("database observer token is missing")
            database_collector = DatabaseEventCollector(
                run_id,
                store.append_event,
                token=database_token,
                host=str(database_observation.get("bind_host", "127.0.0.1")),
                port=int(database_observation.get("port", 8765)),
            )
            database_collector.start()
            if not database_collector.wait_until_ready():
                raise TimeoutError("database observer heartbeat was not received")
        except Exception as exc:
            if database_collector is not None:
                database_collector.close()
                database_collector = None
            observer_health = ObserverHealth(
                gateway="not_started",
                database="failed",
                detail={"database": type(exc).__name__},
            )
            reporter.emit(
                "database_observer_failed",
                state="failed",
                detail={"error_type": type(exc).__name__},
            )
        else:
            observer_health = replace(observer_health, database="ok")
            reporter.emit(
                "database_observer_started",
                state="starting_observers",
                detail={
                    "host": database_collector.address[0],
                    "port": database_collector.address[1],
                },
            )
    else:
        database_collector = None

    observer_status = {
        "database_observer": "enabled" if database_collector is not None else "disabled",
        "r5_evidence": "available" if database_collector is not None else "unavailable",
        "reason": None if database_collector is not None else "database_observer_not_active",
    }
    observer_health = replace(
        observer_health,
        detail={**observer_health.detail, **observer_status},
    )
    provenance = replace(provenance, observer_status=observer_status)

    requested_port = args.gateway_port or 0
    reporter.change_state("starting_gateway")
    try:
        app = create_app(
            upstream,
            run_id,
            actor="agent",
            event_sink=store.append_event,
            observer=observer,
            timeout=timeout,
            tls=environment_doc.get("tls"),
            request_scope=(database_collector.request_scope if database_collector else None),
            sequence_allocator=sequence_service.allocator,
            lifecycle_sink=store.append_lifecycle,
            action_registry=action_registry,
            enforce_policy=config.enforcement_enabled,
            state_observer=state_observer,
        )
        server = make_server(args.gateway_host, requested_port, app)
    except Exception as exc:
        if database_collector is not None:
            database_collector.close()
        reporter.emit(
            "gateway_failed",
            state="failed",
            detail={"error_type": type(exc).__name__},
        )
        reporter.emit(
            "gateway_error",
            state="failed",
            detail={"error_type": type(exc).__name__},
        )
        result = _empty_result(
            config, _error_outcome("gateway_error", exc), observer_health, provenance
        )
        _save_result(store, result, reporter)
        raise
    actual_port = server.server_address[1]
    observer_health = replace(observer_health, gateway="ok")
    gateway_url = f"http://{args.gateway_host}:{actual_port}"
    reporter.emit(
        "gateway_started",
        state="starting_gateway",
        detail={"host": args.gateway_host, "port": actual_port},
    )

    import threading

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    interrupted = False
    with database_collector if database_collector is not None else nullcontext():
        server_thread.start()
        try:
            prompts = load_agent_prompts(
                scenario_path,
                gateway=gateway_url,
                policy_path=policy_path,
                condition_instruction=condition.instruction,
                runtime_context=runtime_context,
            )

            def report(record: dict) -> None:
                store.append_trace({"run_id": run_id, **record})

            def lifecycle(
                stage: str,
                action_id: str,
                step: int,
                raw_action: dict,
                details: dict | None,
                normalized_action: dict,
            ) -> None:
                details = details or {}
                store.append_lifecycle(
                    LifecycleEvent.now(
                        run_id=run_id,
                        seq=step - 1,
                        action_id=action_id,
                        actor="agent",
                        source="runner",
                        stage=stage,
                        raw_action=raw_action if stage == "proposed" else None,
                        reference=None if stage == "proposed" else {"action_step": step},
                        normalized_action=normalized_action,
                        decision=details.get("decision"),
                        reason=details.get("reason"),
                    )
                )

            def progress(event_type: str, step: int, detail: dict) -> None:
                reporter.emit(event_type, state="running_agent", step=step, detail=detail)

            outcome = {"reason": "runner_error", "step": None, "detail": None}
            try:
                reporter.change_state("running_agent")
                reporter.emit("agent_started", state="running_agent")
                with _hide_sequence_environment():
                    outcome = run_episode(
                        prompts.system,
                        gateway_url,
                        max_steps,
                        provider=args.provider,
                        model=args.model,
                        temperature=args.temperature,
                        on_step=report,
                        on_progress=progress,
                        on_lifecycle=lifecycle,
                        policy=policy,
                        enforce_policy=config.enforcement_enabled,
                        seed=config.seed,
                        run_id=run_id,
                        action_registry=action_registry,
                        scenario=args.scenario,
                        goal=scenario_doc.get("goal", {}),
                        default_headers=runtime_context.get("headers", {}),
                        task_prompt=prompts.task,
                    )
            except KeyboardInterrupt:
                interrupted = True
                outcome = {"reason": "user_interrupt", "step": None, "detail": None}
            except Exception as exc:
                outcome = _error_outcome("runner_error", exc)
        finally:
            server.shutdown()
            server_thread.join(timeout=5)

    if interrupted:
        result = _empty_result(config, outcome, provenance=provenance)
        _save_result(store, result, reporter)
        raise KeyboardInterrupt

    reporter.change_state("evaluating", step=outcome.get("step"))
    reporter.emit(
        "evaluation_started",
        state="evaluating",
        step=outcome.get("step"),
    )
    try:
        store.sort_events()
        result = _with_execution(
            evaluate_run(
                store.events_path,
                scenario_doc,
                policy,
                config,
                environment=environment_doc,
                observers=observer_health,
                lifecycle_path=store.lifecycle_path,
                proposal_activity_resolver=(
                    getattr(observer, "resolve_proposal_activity", None)
                    if observer is not None
                    else None
                ),
            ),
            outcome,
        )
        result = replace(result, provenance=provenance)
        reporter.emit(
            "evaluation_completed",
            state="evaluating",
            step=outcome.get("step"),
            detail={
                "current_stage": result.progress.current_stage,
                "goal_success": result.goal.success,
                "roe_compliant": result.roe.compliant,
            },
        )
    except KeyboardInterrupt:
        result = _empty_result(
            config,
            {
                "reason": "user_interrupt",
                "step": outcome.get("step"),
                "detail": None,
            },
            provenance=provenance,
        )
        _save_result(store, result, reporter)
        raise
    except Exception as exc:
        reporter.emit(
            "evaluation_failed",
            state="failed",
            step=outcome.get("step"),
            detail={"error_type": type(exc).__name__},
        )
        result = _empty_result(
            config, _error_outcome("evaluator_error", exc), provenance=provenance
        )
        _save_result(store, result, reporter)
        raise
    _save_result(store, result, reporter)
    sequence_service.__exit__(None, None, None)
    if outcome.get("detail"):
        error_path = store.run_dir / "episode_error.txt"
        error_path.write_text(outcome["detail"], encoding="utf-8")
    return store


def cmd_run(args: argparse.Namespace) -> None:
    run_pipeline(args)


def cmd_ab(args: argparse.Namespace) -> None:
    print(json.dumps(run_ab_experiment(args, run_pipeline), indent=2, ensure_ascii=False))


def cmd_aggregate(args: argparse.Namespace) -> None:
    summary, errors = load_experiment_aggregate(args.root)
    output = args.output or args.root / "aggregate-summary.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    from .core.run import _atomic_json

    _atomic_json(output, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


def cmd_validate(args: argparse.Namespace) -> None:
    report = validate_run(args.runs_dir / args.run)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["valid"]:
        raise SystemExit(1)


def main() -> None:
    load_dotenv(ENV_PATH, override=False)
    args = parse_args()
    if args.command == "init":
        cmd_init(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "ab":
        cmd_ab(args)
    elif args.command == "aggregate-experiments":
        cmd_aggregate(args)
    elif args.command == "validate-run":
        cmd_validate(args)


if __name__ == "__main__":
    main()
