import json
import tempfile
import unittest
from pathlib import Path

from dashboard.backend.services.run_loader import RunRepository


class RunRepositoryTests(unittest.TestCase):
    def test_discovers_only_directories_with_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid-run"
            valid.mkdir()
            (valid / "config.json").write_text(
                json.dumps(
                    {
                        "run_id": "valid-run",
                        "model": "m",
                        "model_version": "1",
                        "agent_version": "a",
                        "environment": "juice_shop",
                        "scenario": "JS-001",
                        "policy": "policy",
                        "max_steps": 1,
                        "timeout": 1,
                        "started_at": "2026-09-09T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            (root / "not-a-run").mkdir()

            runs = RunRepository(root).load_all()

            self.assertEqual([run.run_id for run in runs], ["valid-run"])


if __name__ == "__main__":
    unittest.main()
