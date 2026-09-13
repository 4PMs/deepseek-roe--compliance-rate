"""Run configuration and filesystem persistence."""

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
from threading import Lock
from typing import Any, Mapping

from .event import Event
from .lifecycle import LifecycleEvent
from .result import BenchmarkResult


class ArtifactPersistenceError(IOError):
    """A required run artifact could not be durably persisted."""


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory = path.parent.open("rb")
            try:
                os.fsync(directory.fileno())
            finally:
                directory.close()
        except OSError:
            pass
    finally:
        if temporary.exists():
            temporary.unlink()


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    model: str
    model_version: str
    agent_version: str
    environment: str
    scenario: str
    policy: str
    max_steps: int
    timeout: int
    started_at: datetime
    environment_reset: dict[str, Any] | None = None
    temperature: float | None = None
    seed: int | None = None
    repetition: int | None = None
    provider: str | None = None
    enforcement_enabled: bool = False
    instruction_condition: str | None = None
    instruction_condition_group: str | None = None
    instruction_condition_path: str | None = None
    instruction_condition_sha256: str | None = None
    roe_taxonomy: str | None = None
    roe_taxonomy_path: str | None = None
    roe_taxonomy_sha256: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", self.run_id):
            raise ValueError("run_id must be 1-100 safe filename characters")
        if self.started_at.tzinfo is None:
            raise ValueError("started_at must include a timezone")
        if self.max_steps <= 0 or self.timeout <= 0:
            raise ValueError("max_steps and timeout must be positive")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat()
        if self.environment_reset is None:
            data.pop("environment_reset")
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunConfig":
        values = dict(data)
        started_at = values["started_at"]
        if isinstance(started_at, str):
            values["started_at"] = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        return cls(**values)


class RunStore:
    """Owns the stable runs/<run_id>/ artifact layout."""

    def __init__(self, runs_dir: Path, config: RunConfig):
        self.config = config
        self.run_dir = Path(runs_dir) / config.run_id
        self.config_path = self.run_dir / "config.json"
        self.events_path = self.run_dir / "events.jsonl"
        self.trace_path = self.run_dir / "trace.jsonl"
        self.progress_path = self.run_dir / "progress.jsonl"
        self.lifecycle_path = self.run_dir / "lifecycle.jsonl"
        self.status_path = self.run_dir / "status.json"
        self.result_path = self.run_dir / "result.json"
        self._write_lock = Lock()
        self.persistence_failure: str | None = None

    def initialize(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.config_path.exists():
            existing = RunConfig.from_dict(
                json.loads(self.config_path.read_text(encoding="utf-8"))
            )
            if existing != self.config:
                raise FileExistsError(
                    f"run {self.config.run_id!r} already has a different config"
                )
        else:
            if self.events_path.exists() or self.result_path.exists():
                raise FileExistsError(
                    f"run {self.config.run_id!r} has artifacts without config.json"
                )
            _atomic_json(self.config_path, self.config.to_dict())
        self.events_path.touch(exist_ok=True)
        self.trace_path.touch(exist_ok=True)
        self.lifecycle_path.touch(exist_ok=True)

    def append_event(self, event: Event) -> None:
        if event.run_id != self.config.run_id:
            raise ValueError("event run_id does not match RunConfig")
        line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
        try:
            with self._write_lock, self.events_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            self.persistence_failure = f"evidence_persistence_failure:events:{exc}"
            raise ArtifactPersistenceError(self.persistence_failure) from exc

    def append_trace(self, record: Mapping[str, Any]) -> None:
        """Append one agent-level reasoning step (thought/action/observation).

        This is distinct from ``events.jsonl``: events are Observer-normalized
        facts about what happened, while the trace records what the agent
        thought and decided at each step. Neither judges goal/ROE outcomes.
        """
        line = json.dumps(dict(record), ensure_ascii=False, default=str) + "\n"
        try:
            with self._write_lock, self.trace_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            # Trace is optional; it must not invalidate an otherwise complete run.
            import warnings
            warnings.warn(f"optional trace persistence failed: {exc}")

    def append_lifecycle(self, event: LifecycleEvent) -> None:
        if event.run_id != self.config.run_id:
            raise ValueError("lifecycle run_id does not match RunConfig")
        line = json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n"
        try:
            with self._write_lock, self.lifecycle_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            self.persistence_failure = f"evidence_persistence_failure:lifecycle:{exc}"
            raise ArtifactPersistenceError(self.persistence_failure) from exc

    def sort_events(self) -> None:
        """Order new runs by seq; retain timestamp ordering for legacy runs."""
        with self._write_lock:
            try:
                events = [Event.from_dict(json.loads(line)) for line in
                          self.events_path.read_text(encoding="utf-8").splitlines()
                          if line.strip()]
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                self.persistence_failure = f"evidence_persistence_failure:events:{exc}"
                raise ArtifactPersistenceError(self.persistence_failure) from exc
            if events and all(event.seq >= 0 for event in events):
                events.sort(key=lambda event: event.seq)
            elif any(event.seq >= 0 for event in events):
                raise ValueError("mixed legacy and sequenced events")
            else:
                events.sort(key=lambda event: event.timestamp)
            temporary = self.events_path.with_name(f".events.{os.getpid()}.tmp")
            try:
                with temporary.open("w", encoding="utf-8") as stream:
                    stream.write("".join(json.dumps(event.to_dict(), ensure_ascii=False) + "\n" for event in events))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.events_path)
            except OSError as exc:
                self.persistence_failure = f"evidence_persistence_failure:events:{exc}"
                raise ArtifactPersistenceError(self.persistence_failure) from exc
            finally:
                if temporary.exists():
                    temporary.unlink()

    def write_result(self, result: BenchmarkResult) -> None:
        if result.run_id != self.config.run_id:
            raise ValueError("result run_id does not match RunConfig")
        try:
            _atomic_json(self.result_path, result.to_dict())
        except OSError as exc:
            self.persistence_failure = f"evidence_persistence_failure:result:{exc}"
            raise ArtifactPersistenceError(self.persistence_failure) from exc
