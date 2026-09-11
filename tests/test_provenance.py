from datetime import datetime, timezone
import hashlib
from pathlib import Path
from unittest.mock import patch

from tempera.core.result import BenchmarkResult, Provenance
from tempera.runner import _image_digests, _use_verified_reset_image, collect_provenance, sha256_file


def test_old_result_without_provenance_loads():
    result = BenchmarkResult.from_dict({
        "run_id": "old", "goal": {"success": False},
        "progress": {"current_stage": 0}, "roe": {"compliant": True},
        "metrics": {"steps": 0, "duration_sec": 0.0},
    })
    assert result.provenance is None


def test_same_policy_bytes_have_same_sha256(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    policy.write_bytes(b"allowed:\n  targets: [example]\n")

    assert sha256_file(policy) == sha256_file(policy)
    assert sha256_file(policy) == hashlib.sha256(policy.read_bytes()).hexdigest()


def test_git_unavailable_returns_unknown_without_raising(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    scenario = tmp_path / "scenario.yaml"
    policy.write_bytes(b"policy")
    scenario.write_bytes(b"scenario")

    with patch("tempera.runner.subprocess.run", side_effect=FileNotFoundError):
        provenance = collect_provenance(
            policy, scenario, "test-model", "test-version", 7,
            datetime.now(timezone.utc),
        )

    assert provenance.code_commit == "unknown"
    assert provenance.code_dirty is False
    assert provenance.policy_sha256 == sha256_file(policy)
    assert provenance.scenario_sha256 == sha256_file(scenario)


def test_verified_reset_image_fills_early_docker_probe_gap():
    provenance = Provenance(
        "commit", False, "policy", "scenario", "model", "version", None,
        {}, datetime.now(timezone.utc), datetime.now(timezone.utc),
    )
    result = _use_verified_reset_image(provenance, {
        "image": "tempera-juice-shop:latest",
        "image_id": "sha256:" + "a" * 64,
        "baseline_verified": True,
    })
    assert result.image_digests == {"tempera-juice-shop:latest": "sha256:" + "a" * 64}
    assert result.image_digests_status == "verified_reset_image_id"


def test_docker_available_records_repo_digest():
    from subprocess import CompletedProcess

    with patch("tempera.runner.subprocess.run", side_effect=[
        CompletedProcess([], 0, stdout="container-1\n", stderr=""),
        CompletedProcess([], 0, stdout='["juice@sha256:' + "b" * 64 + '"]', stderr=""),
    ]):
        assert _image_digests() == {"container-1": "juice@sha256:" + "b" * 64}


def test_unverified_reset_image_is_not_invented():
    provenance = Provenance(
        "commit", False, "policy", "scenario", "model", "version", None,
        {}, datetime.now(timezone.utc), datetime.now(timezone.utc),
    )
    result = _use_verified_reset_image(provenance, {"image_id": "sha256:" + "a" * 64})
    assert result.image_digests == {}
    assert result.image_digests_status == "unavailable"
