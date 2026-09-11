from datetime import datetime, timezone
import threading
import socket
import time
import warnings

import pytest

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.core.sequence import SequenceAllocator, SequenceService
from tempera.core.result import GoalResult
from tempera.evaluate.matching import evidence
from tempera.evaluate.roe import evaluate_roe
from tempera.observe.database import DatabaseObserver
from tempera.observe.gateway import WebObserver
from tempera.observe.normalizer import RawObservation


def event(kind: str, timestamp: str, seq: int, **attributes: object) -> Event:
    return Event("0.2", "run", datetime.fromisoformat(timestamp), "agent", kind,
                 kind, "request", "target", seq, dict(attributes))


def test_clock_skew_uses_seq_not_timestamp():
    goal = event("web", "2026-01-01T12:00:00.100+00:00", 10, method="GET")
    database = event("sequelize", "2026-01-01T11:59:59.900+00:00", 11,
                     operation="UPDATE", table="Users", behavior="data_modification")
    result = evaluate_roe([goal], Policy(), goal=GoalResult(True, [evidence(0, goal)]),
                          database_events=[database], goal_achieved_seq=10)
    assert any(v["reason"] == "post_goal_database_modification" for v in result.violations)


def test_new_event_and_legacy_roundtrip():
    current = event("web", "2026-01-01T00:00:00+00:00", 7)
    assert Event.from_dict(current.to_dict()).seq == 7
    with warnings.catch_warnings(record=True) as caught:
        legacy = Event.from_dict({**current.to_dict(), "schema_version": "0.1"} | {"seq": None})
    assert legacy.seq == -1 and caught


def test_negative_new_event_rejected_and_allocator_is_monotonic():
    try:
        event("web", "2026-01-01T00:00:00+00:00", -1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative seq accepted")
    allocator = SequenceAllocator()
    assert [allocator.next(), allocator.next()] == [0, 1]


def test_sequence_service_binds_for_container_reachability():
    service = SequenceService("token")
    try:
        assert service.address[0] == "0.0.0.0"
    finally:
        service.__exit__(None, None, None)


def test_request_seq_precedes_triggered_database_seq():
    raw = RawObservation(datetime.now(timezone.utc), "agent", "gateway", "web",
                         "request", "http://target/api/Users", {"method": "GET"})
    web = WebObserver().normalize("run", raw, seq=41)
    db_raw = RawObservation(datetime.now(timezone.utc), "target", "sequelize", "database",
                            "query", "sqlite", {"sql": "SELECT * FROM Users"})
    database = DatabaseObserver().normalize("run", db_raw, seq=42)
    assert web.seq < database.seq


# ---------------------------------------------------------------------------
# Regression tests: SequenceService lifecycle deadlock (fix: check is_alive)
# ---------------------------------------------------------------------------

def test_sequence_service_start_stop_bounded():
    """Service must start and stop within a bounded time; no hang allowed."""
    from tempera.core.sequence import request_sequence

    with SequenceService("tok") as svc:
        seq = request_sequence("127.0.0.1", svc.port, "tok")
        assert seq == 0

    # After context exit, background thread must be dead
    assert not svc._thread.is_alive(), "SequenceService thread leaked after __exit__"


def test_sequence_service_exit_without_enter_does_not_hang():
    """__exit__ called without __enter__ (thread never started) must return promptly.

    Regression: previously called server.shutdown() unconditionally, which blocks
    forever because shutdown() waits for serve_forever() to set __is_shut_down —
    an event that is never set when the thread was never started.
    """
    svc = SequenceService("tok2")
    result = []

    def do_exit():
        svc.__exit__(None, None, None)
        result.append("done")

    t = threading.Thread(target=do_exit, daemon=True)
    t.start()
    t.join(timeout=3)
    assert not t.is_alive(), "__exit__ without __enter__ blocked for >3s (deadlock)"
    assert result == ["done"]


def test_sequence_service_repeated_lifecycle():
    """start → request → stop cycle must complete cleanly 5 times; no thread leaks."""
    from tempera.core.sequence import request_sequence

    for i in range(5):
        with SequenceService(f"tok-{i}") as svc:
            seq = request_sequence("127.0.0.1", svc.port, f"tok-{i}")
            assert seq == 0
        assert not svc._thread.is_alive(), f"thread leaked on iteration {i}"


def test_request_sequence_wrong_token_no_seq():
    """Invalid token causes server to return nothing; client must raise, not hang."""
    from tempera.core.sequence import request_sequence

    with SequenceService("correct-token") as svc:
        with pytest.raises(Exception):
            # Server sends nothing for a bad token; readline() returns ""
            # which causes json.loads("") → ValueError (JSONDecodeError)
            request_sequence("127.0.0.1", svc.port, "wrong-token", timeout=2)


def test_request_sequence_unreachable_raises_promptly():
    """Connecting to a port with no listener must raise within timeout, not hang."""
    # Bind briefly to discover a free port, then close so nothing listens
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    from tempera.core.sequence import request_sequence

    start = time.monotonic()
    try:
        request_sequence("127.0.0.1", free_port, "tok", timeout=2)
    except OSError:
        pass  # expected: connection refused or timed out
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"request_sequence blocked {elapsed:.1f}s (expected raise within 5s)"
