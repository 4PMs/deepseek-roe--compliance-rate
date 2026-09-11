import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tempera.core.lifecycle import validate_lifecycle
from tempera.evaluate.pipeline import load_lifecycle


def test_done_no_target_is_a_valid_allow_terminal_sequence():
    validate_lifecycle([
        {"action_id": "action-1", "stage": "proposed", "raw_action": {"action": "done"}},
        {"action_id": "action-1", "stage": "policy_decision", "decision": "allow"},
    ])


@pytest.mark.parametrize("records", [
    [{"action_id": "action-1", "stage": "proposed"},
     {"action_id": "action-1", "stage": "proposed"}],
    [{"action_id": "action-1", "stage": "proposed"},
     {"action_id": "action-1", "stage": "policy_decision"},
     {"action_id": "action-1", "stage": "observed"}],
    [{"action_id": "action-1", "stage": "proposed", "run_id": "other"}],
    [{"stage": "observed"}],
])
def test_invalid_lifecycle_is_reported_by_production_loader(records):
    with TemporaryDirectory() as directory:
        path = Path(directory) / "lifecycle.jsonl"
        path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        valid, reason = load_lifecycle(path, run_id="run-1")
    assert valid is False
    assert reason.startswith("lifecycle_invalid:")
