import os
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
OBSERVER = Path(__file__).parents[1] / "environments" / "juice_shop" / "sequelize_observer.cjs"


def _run_observer(env: dict[str, str]):
    if NODE is None:
        pytest.skip("node is not installed")
    script = """
const Module = require('node:module')
const load = Module._load
let loaded = false
Module._load = function (request, parent, isMain) {
  if (request === 'sequelize') {
    loaded = true
    return { Sequelize: class Sequelize {} }
  }
  return load.call(this, request, parent, isMain)
}
require(process.argv[1])
process.stdout.write(JSON.stringify({ loaded }))
"""
    process_env = os.environ.copy()
    for name in (
        "TEMPERA_DB_OBSERVER", "TEMPERA_DB_OBSERVER_TOKEN",
        "TEMPERA_SEQUENCE_OBSERVER", "TEMPERA_SEQUENCE_TOKEN",
    ):
        process_env.pop(name, None)
    process_env.update(env)
    return subprocess.run(
        [NODE, "-e", script, str(OBSERVER)],
        capture_output=True, text=True, env=process_env, check=True,
    )


def test_all_observer_variables_enable_observer():
    result = _run_observer({
        "TEMPERA_DB_OBSERVER": "127.0.0.1:8765",
        "TEMPERA_DB_OBSERVER_TOKEN": "db-token",
        "TEMPERA_SEQUENCE_OBSERVER": "127.0.0.1:8766",
        "TEMPERA_SEQUENCE_TOKEN": "sequence-token",
    })
    assert result.stdout == '{"loaded":true}'
    assert "TEMPERA DB observer disabled" not in result.stderr


def test_missing_sequence_variables_disables_observer_with_one_warning():
    result = _run_observer({
        "TEMPERA_DB_OBSERVER": "127.0.0.1:8765",
        "TEMPERA_DB_OBSERVER_TOKEN": "db-token",
    })
    assert result.stdout == '{"loaded":false}'
    assert result.stderr.count("TEMPERA DB observer disabled") == 1
    assert "R5 state-diff evidence will be unavailable" in result.stderr
    assert "configuration incomplete" in result.stderr


def test_missing_database_variables_also_disables_observer_without_delay_path():
    result = _run_observer({
        "TEMPERA_SEQUENCE_OBSERVER": "127.0.0.1:8766",
        "TEMPERA_SEQUENCE_TOKEN": "sequence-token",
    })
    assert result.stdout == '{"loaded":false}'
    assert result.stderr.count("TEMPERA DB observer disabled") == 1
