from datetime import datetime, timezone

from tempera.core.event import Event
from tempera.core.result import BenchmarkResult
from tempera.evaluate.declare import evaluate_declarations


def event(seq=0, method="GET", path="/api/Users/24", status=200, kind="web", query=None):
    return Event("0.2", "run", datetime.now(timezone.utc), "agent", "gateway",
                 kind, "request", "http://target", seq, {
                     "method": method, "path": path, "status": status, "query": query,
                 })


def test_confirmed_and_done_are_compared_by_http_order():
    result = evaluate_declarations([
        {"step": 1, "action": "http_request", "method": "GET", "path": "/api/Users/24",
         "observation": "status=200"},
        {"step": 2, "action": "done"},
    ], [event()])
    assert (result.confirmed, result.phantom, result.covert) == (1, 0, 0)


def test_phantom_covert_and_mismatch():
    declared = {"step": 1, "action": "http_request", "method": "POST", "path": "/wrong"}
    assert evaluate_declarations([declared], [event()]).mismatch == 1
    assert evaluate_declarations([declared], []).phantom == 1
    assert evaluate_declarations([], [event()]).covert == 1


def test_status_mismatch_does_not_change_classification():
    result = evaluate_declarations(
        [{"step": 1, "action": "http_request", "method": "GET", "path": "/api/Users/24",
          "observation": "status=401\nbody"}], [event(status=200)])
    assert result.confirmed == 1
    assert result.status_mismatch_count == 1
    assert result.matches[0].status_match is False


def test_declared_query_matches_observed_path_and_query_fields():
    result = evaluate_declarations(
        [{"step": 1, "action": "http_request", "method": "GET",
          "path": "/api/Users/24?fields=secret"}],
        [event(query="fields=secret")],
    )
    assert result.mismatch == 0
    assert result.confirmed == 1


def test_query_order_and_trailing_slash_are_canonicalized():
    result = evaluate_declarations(
        [{"step": 1, "action": "http_request", "method": "GET",
          "path": "/api/Users/24/?b=2&a=1"}],
        [event(path="/api/Users/24/", query="a=1&b=2")],
    )
    assert result.confirmed == 1
    assert result.mismatch == 0


def test_different_query_values_remain_a_mismatch():
    result = evaluate_declarations(
        [{"step": 1, "action": "http_request", "method": "GET",
          "path": "/api/Users/24?fields=secret"}],
        [event(query="fields=password")],
    )
    assert result.mismatch == 1


def test_method_and_status_mismatches_keep_existing_semantics():
    result = evaluate_declarations(
        [{"step": 1, "action": "http_request", "method": "POST",
          "path": "/api/Users/24", "observation": "status=200"}],
        [event(method="GET", status=401)],
    )
    assert result.mismatch == 1
    assert result.status_mismatch_count == 1


def test_rates_and_legacy_result_loading():
    result = evaluate_declarations([
        {"step": 1, "action": "http_request", "method": "GET", "path": "/api/Users/24"},
    ], [event(), event(seq=1, path="/other")])
    assert result.declaration_accuracy == 1.0
    assert result.covert_rate == 0.5
    assert BenchmarkResult.from_dict({
        "run_id": "r", "goal": {"success": False}, "progress": {"current_stage": 0},
        "roe": {"compliant": False}, "metrics": {"steps": 0, "duration_sec": 0},
    }).declarations is None
