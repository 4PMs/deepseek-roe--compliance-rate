import unittest

from dashboard.backend.domain.models import DashboardRun, GoalAssessment, RoeAssessment
from dashboard.backend.services.analytics import summarize_runs


class AnalyticsTests(unittest.TestCase):
    def _run(self, run_id: str, goal: bool | None, compliant: bool | None, validity: str) -> DashboardRun:
        return DashboardRun(
            run_id=run_id,
            model="model",
            model_version="1",
            agent_version="poc",
            environment="juice_shop",
            scenario="JS-001",
            policy="policy",
            started_at=None,
            pressure_family="unknown",
            pressure_level=None,
            goal=GoalAssessment(success=goal, achieved_step=None, evidence=[]),
            roe=RoeAssessment(compliant=compliant, violations=[]),
            progress_stage=None,
            progress_name=None,
            metrics={},
            termination_reason="unknown",
            validity=validity,
            timeline=[],
            trace=[],
            warnings=[],
        )

    def test_summary_uses_only_valid_runs_as_behavior_denominator(self) -> None:
        runs = [
            self._run("safe", True, True, "valid"),
            self._run("unsafe", True, False, "valid"),
            self._run("infra", None, None, "invalid"),
            self._run("unknown", None, None, "unknown"),
        ]

        summary = summarize_runs(runs)

        self.assertEqual(summary.total_runs, 4)
        self.assertEqual(summary.valid_runs, 2)
        self.assertEqual(summary.goal_success_count, 2)
        self.assertEqual(summary.violation_run_count, 1)
        self.assertEqual(summary.safe_success_count, 1)
        self.assertEqual(summary.goal_rate, 1.0)
        self.assertEqual(summary.violation_rate, 0.5)
        self.assertEqual(summary.safe_success_rate, 0.5)

    def test_empty_valid_denominator_produces_unknown_rates(self) -> None:
        summary = summarize_runs([self._run("unknown", None, None, "unknown")])

        self.assertIsNone(summary.goal_rate)
        self.assertIsNone(summary.violation_rate)
        self.assertIsNone(summary.safe_success_rate)


if __name__ == "__main__":
    unittest.main()
