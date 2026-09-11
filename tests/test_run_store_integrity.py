from datetime import datetime, timezone
import json

import pytest

from tempera.core.result import BenchmarkResult, GoalResult, Metrics, ProgressResult, RoeResult
from tempera.core.run import ArtifactPersistenceError, RunConfig, RunStore
from tempera.core.lifecycle import LifecycleEvent


def store(tmp_path):
    config = RunConfig("run-integrity", "m", "1", "a", "env", "s", "p", 1, 1,
                       datetime.now(timezone.utc))
    result = RunStore(tmp_path, config)
    result.initialize()
    return result


def test_result_replace_keeps_previous_valid_file_on_failure(tmp_path, monkeypatch):
    run = store(tmp_path)
    old = json.dumps({"old": True})
    run.result_path.write_text(old, encoding="utf-8")
    monkeypatch.setattr("tempera.core.run.os.replace", lambda *_: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        run.write_result(BenchmarkResult(run.config.run_id, GoalResult(False),
                                         ProgressResult(0), RoeResult(False), Metrics(0, 0)))
    assert run.result_path.read_text(encoding="utf-8") == old


def test_partial_event_line_is_fatal(tmp_path):
    run = store(tmp_path)
    run.events_path.write_text('{"partial":', encoding="utf-8")
    with pytest.raises(ArtifactPersistenceError):
        run.sort_events()
    assert run.persistence_failure.startswith("evidence_persistence_failure:events:")


def test_lifecycle_append_failure_is_fatal(tmp_path):
    run = store(tmp_path)
    run.lifecycle_path.unlink()
    run.lifecycle_path.mkdir()
    event = LifecycleEvent.now(run_id=run.config.run_id, seq=0, action_id="a",
                               actor="agent", source="test", stage="proposed",
                               raw_action={"tool": "done"})
    with pytest.raises(ArtifactPersistenceError):
        run.append_lifecycle(event)


def test_trace_write_failure_is_optional(tmp_path):
    run = store(tmp_path)
    run.trace_path.unlink()
    run.trace_path.mkdir()
    run.append_trace({"step": 1})
