import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


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
        "DB_OBSERVER", "DB_OBSERVER_TOKEN",
        "RUN_SEQUENCE_OBSERVER", "RUN_SEQUENCE_TOKEN",
    ):
        process_env.pop(name, None)
    process_env.update(env)
    return subprocess.run(
        [NODE, "-e", script, str(OBSERVER)],
        capture_output=True, text=True, env=process_env, check=True,
    )


def test_all_observer_variables_enable_observer():
    result = _run_observer({
        "DB_OBSERVER": "127.0.0.1:8765",
        "DB_OBSERVER_TOKEN": "db-token",
        "RUN_SEQUENCE_OBSERVER": "127.0.0.1:8766",
        "RUN_SEQUENCE_TOKEN": "sequence-token",
    })
    assert result.stdout == '{"loaded":true}'
    assert "BENCHMARK DB observer disabled" not in result.stderr


def test_missing_sequence_variables_disables_observer_with_one_warning():
    result = _run_observer({
        "DB_OBSERVER": "127.0.0.1:8765",
        "DB_OBSERVER_TOKEN": "db-token",
    })
    assert result.stdout == '{"loaded":false}'
    assert result.stderr.count("BENCHMARK DB observer disabled") == 1
    assert "R5 state-diff evidence will be unavailable" in result.stderr
    assert "configuration incomplete" in result.stderr


def test_missing_database_variables_also_disables_observer_without_delay_path():
    result = _run_observer({
        "RUN_SEQUENCE_OBSERVER": "127.0.0.1:8766",
        "RUN_SEQUENCE_TOKEN": "sequence-token",
    })
    assert result.stdout == '{"loaded":false}'
    assert result.stderr.count("BENCHMARK DB observer disabled") == 1


def test_query_logging_never_spawns_a_synchronous_child_process():
    if NODE is None:
        pytest.skip("node is not installed")
    script = """
const Module = require('node:module')
const load = Module._load
let childProcessLoaded = false
let connectionCreated = false
let heartbeatSent = false
class Sequelize {
  log () { return 'logged' }
}
Module._load = function (request, parent, isMain) {
  if (request === 'sequelize') return { Sequelize }
  if (request === 'node:dgram') {
    return {
      createSocket: () => ({
        on () {},
        unref () {},
        send (payload) { heartbeatSent ||= JSON.parse(payload).type === 'heartbeat' }
      })
    }
  }
  if (request === 'node:net') {
    return {
      createConnection: () => {
        connectionCreated = true
        return { on () { return this }, end () {}, destroy () {} }
      }
    }
  }
  if (request === 'node:child_process') {
    childProcessLoaded = true
    return { execFileSync: () => { throw new Error('sync child process used') } }
  }
  return load.call(this, request, parent, isMain)
}
require(process.argv[1])
const result = Sequelize.prototype.log('SELECT * FROM Users')
process.stdout.write(JSON.stringify({ childProcessLoaded, connectionCreated, heartbeatSent, result }))
"""
    process_env = os.environ.copy()
    process_env.update({
        "DB_OBSERVER": "127.0.0.1:8765",
        "DB_OBSERVER_TOKEN": "db-token",
        "RUN_SEQUENCE_OBSERVER": "127.0.0.1:8766",
        "RUN_SEQUENCE_TOKEN": "sequence-token",
    })
    result = subprocess.run(
        [NODE, "-e", script, str(OBSERVER)],
        capture_output=True, text=True, env=process_env, check=True,
    )

    assert result.stdout == (
        '{"childProcessLoaded":false,"connectionCreated":true,'
        '"heartbeatSent":true,"result":"logged"}'
    )


def test_database_collector_binds_an_interface_reachable_from_docker():
    environment = yaml.safe_load(
        (OBSERVER.parent / "environment.yaml").read_text(encoding="utf-8")
    )

    assert environment["database"]["observation"]["bind_host"] == "0.0.0.0"
