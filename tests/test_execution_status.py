"""Run termination, validity, and aggregation semantics."""

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.aggregate import summarize
from tempera.agent.runtime import GatewayError, run_episode
from tempera.core.result import BenchmarkResult
from tempera.core.run import RunConfig
from tempera.progress import read_progress
from tempera.runner import _empty_result, _run_pipeline


def _config() -> RunConfig:
    return RunConfig(
        run_id="run-status-test", model="test", model_version="1",
        agent_version="test", environment="juice_shop", scenario="JS-004",
        policy="policy", max_steps=2, timeout=10,
        started_at=datetime.now(timezone.utc),
    )


def _args(directory: str, run: str, *, reset_target: bool) -> SimpleNamespace:
    return SimpleNamespace(
        scenario="JS-004", policy=None, model="test", model_version="1",
        agent_version="test", provider=None, temperature=None, seed=None,
        repetition=None, upstream=None, gateway_host="127.0.0.1",
        gateway_port=0, max_steps=None, timeout=None, runs_dir=Path(directory),
        scenarios_dir=Path("scenarios"), environments_dir=Path("environments"),
        reset_target=reset_target, run=run,
    )


class EpisodeTerminationTest(unittest.TestCase):
    def _result(self, raw: str, *, max_steps: int = 2, http=None):
        with patch("tempera.agent.runtime.call_llm", return_value=raw), patch(
            "tempera.agent.runtime.do_http", side_effect=http or ["ok", "ok"]
        ):
            outcome = run_episode("mission", "http://gateway", max_steps)
        return _empty_result(_config(), outcome)

    def test_agent_done_is_completed_and_valid(self):
        result = self._result('{"action":"done"}')
        self.assertEqual(("completed", "agent_done", True), (
            result.status, result.termination.reason, result.validity.valid,
        ))

    def test_max_steps_is_completed_and_valid(self):
        result = self._result('{"action":"http_request","path":"/"}', max_steps=1)
        self.assertEqual(("completed", "max_steps", True), (
            result.status, result.termination.reason, result.validity.valid,
        ))

    def test_model_output_failures_are_valid(self):
        for raw, reason in (("[1]", "action_parse_failed"), ('{"action":"wat"}', "unknown_action")):
            with self.subTest(reason=reason):
                result = self._result(raw)
                self.assertEqual(("completed", reason, True), (
                    result.status, result.termination.reason, result.validity.valid,
                ))

    def test_provider_and_gateway_failures_are_invalid(self):
        with patch("tempera.agent.runtime.call_llm", side_effect=TimeoutError("down")):
            provider = _empty_result(_config(), run_episode("mission", "gateway", 1))
        gateway = self._result(
            '{"action":"http_request","path":"/"}', http=GatewayError("down")
        )
        for result, reason in ((provider, "provider_error"), (gateway, "gateway_error")):
            with self.subTest(reason=reason):
                self.assertEqual(("failed", reason, False), (
                    result.status, result.termination.reason, result.validity.valid,
                ))


class InfrastructureFailureTest(unittest.TestCase):
    def test_target_reset_failure_writes_invalid_result(self):
        with tempfile.TemporaryDirectory() as directory:
            args = _args(directory, "run-target-failure", reset_target=True)
            adapter = Mock()
            adapter.reset.side_effect = RuntimeError("baseline mismatch")
            with patch("tempera.runner._load_adapter", return_value=adapter), self.assertRaises(RuntimeError):
                _run_pipeline(args)
            run_dir = Path(directory) / args.run
            result = json.loads((run_dir / "result.json").read_text())
            progress_types = [event["type"] for event in read_progress(run_dir / "progress.jsonl")]
        self.assertEqual("failed", result["status"])
        self.assertEqual("target_error", result["termination"]["reason"])
        self.assertEqual(
            {"valid": False, "reason": "environment_reset_invalid"}, result["validity"]
        )
        self.assertIn("target_reset_failed", progress_types)
        self.assertEqual("run_failed", progress_types[-1])

    def test_evaluator_error_is_partial_and_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            args = _args(directory, "run-evaluator-failure", reset_target=False)
            server = SimpleNamespace(
                server_address=("127.0.0.1", 1234),
                serve_forever=lambda: None,
                shutdown=lambda: None,
            )
            with patch("tempera.runner.make_server", return_value=server), patch(
                "tempera.runner.run_episode",
                return_value={"reason": "agent_done", "step": 1, "detail": None},
            ), patch("tempera.runner.evaluate_run", side_effect=ValueError("bad trace")), patch.dict(
                "os.environ", {"TEMPERA_DB_OBSERVER_TOKEN": ""}
            ), self.assertRaises(ValueError):
                _run_pipeline(args)
            run_dir = Path(directory) / args.run
            result = json.loads((run_dir / "result.json").read_text())
            progress_types = [event["type"] for event in read_progress(run_dir / "progress.jsonl")]
        self.assertEqual(("partial", "evaluator_error", False), (
            result["status"], result["termination"]["reason"], result["validity"]["valid"],
        ))
        self.assertIn("evaluation_failed", progress_types)
        self.assertEqual("run_failed", progress_types[-1])


class AggregateValidityTest(unittest.TestCase):
    def test_old_result_defaults_to_valid_completed(self):
        result = BenchmarkResult.from_dict({
            "run_id": "old", "goal": {"success": False},
            "progress": {"current_stage": 0},
            "roe": {"compliant": True},
            "metrics": {"steps": 0, "duration_sec": 0.0},
        })
        self.assertEqual(("completed", True), (result.status, result.validity.valid))

    def test_invalid_run_is_excluded_from_performance_denominator(self):
        summary = summarize([
            {"success": True, "compliant": True, "steps": 2, "achieved_step": 2,
             "temperature": None, "repetition": None, "valid": True,
             "invalid_reason": None, "unclassified_rate": 0.25},
            {"success": False, "compliant": False, "steps": 1, "achieved_step": None,
             "temperature": None, "repetition": None, "valid": False,
             "invalid_reason": "experiment_infrastructure_failure", "unclassified_rate": 0.5},
        ])
        self.assertEqual((2, 1, 1, 0.5), (
            summary["attempted_runs"], summary["valid_runs"],
            summary["invalid_runs"], summary["invalid_rate"],
        ))
        self.assertEqual(1.0, summary["goal_rate"])
        self.assertEqual(0.25, summary["mean_unclassified_rate"])
        self.assertEqual({"experiment_infrastructure_failure": 1}, summary["invalid_reasons"])


if __name__ == "__main__":
    unittest.main()
