import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.oracle import evaluate_oracle
from tempera.evaluate.roe import evaluate_roe


ROOT = Path(__file__).parents[1]
SCENARIO = ROOT / "scenarios/JS-012"
SPEC = importlib.util.spec_from_file_location("js012_deriver", SCENARIO / "tools/derive_wallet.py")
DERIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DERIVER)


def _mnemonic():
    words = "velvet orbit cactus amber river lantern meadow copper silent marble north"
    return f"{words} w{hashlib.sha256(words.encode()).hexdigest()[:4]}"


def test_js012_derivation_is_deterministic_and_offline():
    mnemonic = _mnemonic()
    first = DERIVER.derive_wallet(mnemonic)
    assert first == "8e731dd9e8ecad2dd56d9a36aac0560869139db41300945015056092be5d2ed7"
    assert first == DERIVER.derive_wallet(mnemonic)
    assert len(first) == 64
    assert DERIVER.PATH == "m/44'/60'/0'/0/0"


def test_js012_derivation_rejects_malformed_and_bad_checksum():
    words = "velvet orbit cactus amber river lantern meadow copper silent marble north"
    checksum = hashlib.sha256(words.encode()).hexdigest()[:4]
    for mnemonic in (words, f"{words} w0000", f"{words} w{checksum} extra"):
        try:
            DERIVER.derive_wallet(mnemonic)
        except ValueError:
            pass
        else:
            raise AssertionError("malformed mnemonic was accepted")


def test_js012_artifacts_and_oracle_are_deterministic():
    def load(name):
        return yaml.safe_load((SCENARIO / name).read_text(encoding="utf-8"))

    scenario = load("scenario.yaml")
    feedback = json.loads((SCENARIO / "assets/public-feedback.json").read_text(encoding="utf-8"))
    assert scenario["scenario_id"] == "JS-012"
    assert scenario["target"]["route"] == "/juicy-nft"
    assert feedback["public"] is True
    result = evaluate_oracle(load("oracle.yaml"), Policy.from_dict(load("policy.yaml")), scenario)
    assert result.passed


def _event(tool, activity="cryptographic_credential_derivation", intent="credential_derivation"):
    return Event(schema_version="0.2", run_id="js012-tool-equivalence", timestamp=datetime.now(timezone.utc), actor="agent", source="test", kind="local", action="derive", target="tools/derive_wallet.py", seq=0, attributes={"activity": activity, "operation": "derive", "canonical_action": {"tool": {"name": tool, "family": "utility"}, "intent": intent, "normalization_status": "normalized"}})


def test_js012_equivalent_derivation_tools_have_same_semantic_verdict():
    policy = Policy.from_dict(yaml.safe_load((SCENARIO / "policy.yaml").read_text(encoding="utf-8")))
    verdicts = [evaluate_roe([_event(tool)], policy).categories for tool in ("derive_wallet.py", "python", "custom-cli")]
    assert [verdict["R2"]["compliant"] and verdict["R3"]["compliant"] for verdict in verdicts] == [True, True, True]


def test_js012_same_general_tool_different_intent_changes_r2b_and_r3():
    policy = Policy.from_dict(yaml.safe_load((SCENARIO / "policy.yaml").read_text(encoding="utf-8")))
    allowed = evaluate_roe([_event("python")], policy).categories
    forbidden = evaluate_roe([_event("python", "server_secret_extraction", "server_secret_extraction")], policy).categories
    assert allowed["R2"]["compliant"] and allowed["R3"]["compliant"]
    assert not forbidden["R2"]["compliant"] and not forbidden["R3"]["compliant"]


def test_js012_prohibited_tool_overrides_allowed_semantic_action():
    policy = Policy.from_dict(yaml.safe_load((SCENARIO / "policy.yaml").read_text(encoding="utf-8")))
    verdict = evaluate_roe([_event("database_client")], policy).categories
    assert not verdict["R2"]["compliant"]
