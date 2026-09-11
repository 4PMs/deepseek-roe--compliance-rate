"""Shared target lifecycle serialization."""

from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from tempera.runner import main, run_pipeline


class TargetLockTest(unittest.TestCase):
    def test_every_run_holds_target_lock(self):
        entered = []

        @contextmanager
        def lock():
            entered.append(True)
            yield

        args = object()
        with patch("tempera.runner._target_lock", lock), patch(
            "tempera.runner._run_pipeline", Mock(return_value="done")
        ) as pipeline:
            self.assertEqual("done", run_pipeline(args))

        self.assertEqual([True], entered)
        pipeline.assert_called_once_with(args)


class EnvironmentLoadingTest(unittest.TestCase):
    KEY = "TEMPERA_TEST_DOTENV"

    def setUp(self):
        self.previous = os.environ.pop(self.KEY, None)

    def tearDown(self):
        os.environ.pop(self.KEY, None)
        if self.previous is not None:
            os.environ[self.KEY] = self.previous

    def _run(self, path: Path):
        with patch("tempera.runner.ENV_PATH", path), patch(
            "tempera.runner.parse_args", return_value=SimpleNamespace(command=None)
        ):
            main()

    def test_loads_dotenv_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(f"{self.KEY}=from-file\n", encoding="utf-8")
            self._run(path)
        self.assertEqual("from-file", os.environ[self.KEY])

    def test_shell_value_wins(self):
        os.environ[self.KEY] = "from-shell"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(f"{self.KEY}=from-file\n", encoding="utf-8")
            self._run(path)
        self.assertEqual("from-shell", os.environ[self.KEY])

    def test_missing_dotenv_is_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            self._run(Path(directory) / ".env")
        self.assertNotIn(self.KEY, os.environ)


if __name__ == "__main__":
    unittest.main()
