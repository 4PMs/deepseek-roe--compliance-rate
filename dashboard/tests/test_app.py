import unittest
from pathlib import Path

from dashboard.app import build_snapshot


class DashboardPresentationTests(unittest.TestCase):
    def test_empty_snapshot_preserves_zero_denominator(self) -> None:
        snapshot = build_snapshot([])

        self.assertEqual(snapshot["summary"]["valid_runs"], 0)
        self.assertIsNone(snapshot["summary"]["violation_rate"])
        self.assertEqual(snapshot["runs"], [])

    def test_primary_ui_names_ttp_and_roe_before_attack_matrix(self) -> None:
        html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text(encoding="utf-8")

        timeline_position = html.index("TTP / RoE Timeline")
        matrix_position = html.index("ATT&amp;CK Matrix")
        self.assertLess(timeline_position, matrix_position)
        self.assertIn("AI Agent Control Evaluation", html)


if __name__ == "__main__":
    unittest.main()
