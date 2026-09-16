from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(r"C:\Users\Scar0\Desktop\4pms_paper")
CAMPAIGN_ID = "roe-violation-poc-20260915T112646Z"
CONTROL_DIR = ROOT / "runs" / CAMPAIGN_ID
STATE_PATH = CONTROL_DIR / "campaign_state.json"
OUT = ROOT / "reports" / CAMPAIGN_ID
RAW_BUNDLE = OUT / "roe-violation-artifacts-only.zip"
SECRET_PATTERNS = {
    "bearer": re.compile(rb"Bearer\s+[A-Za-z0-9._-]{16,}", re.I),
    "jwt": re.compile(rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "api_key_assignment": re.compile(
        rb"(?:api[_-]?key|authorization)\s*[:=]\s*[^\s,}\]]{8,}", re.I
    ),
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def endpoint(result: dict, name: str) -> dict:
    value = result["trajectory"]["endpoints"][name]
    return {
        "status": value["status"],
        "turn": value.get("turn"),
        "confidence": value.get("confidence", 0.0),
        "evidence_ref": value.get("evidence_ref"),
    }


def violating_actions(result: dict) -> list[dict]:
    return [
        action
        for action in result["trajectory"]["actions"]
        if (action.get("dispatch") or {}).get("classification") == "violation"
    ]


def representative_violating_action(result: dict) -> dict | None:
    actions = violating_actions(result)
    return next(
        (
            action
            for action in actions
            if (action.get("server_acceptance") or {}).get("classification") == "violation"
        ),
        actions[0] if actions else None,
    )


def gateway_http_status(run_id: str, action_id: str | None) -> int | None:
    if not action_id:
        return None
    for event in load_jsonl(ROOT / "runs" / run_id / "events.jsonl"):
        attributes = event.get("attributes") or {}
        if event.get("kind") == "web" and attributes.get("action_id") == action_id:
            return attributes.get("status")
    return None


def compact_result(result: dict) -> dict:
    first = representative_violating_action(result)
    proposal = (first or {}).get("proposal") or {}
    impact = (first or {}).get("impact") or {}
    dispatch_endpoint = result["trajectory"]["endpoints"]["prohibited_dispatch"]
    acceptance_endpoint = result["trajectory"]["endpoints"]["server_acceptance"]
    impact_endpoint = result["trajectory"]["endpoints"]["trusted_realized_impact"]
    chain = result["safe_response_chain"]
    return {
        "scenario_id": result["scenario_id"],
        "condition_id": result["condition_id"],
        "run_id": result["run_id"],
        "model": result["model"],
        "trajectory": {
            "expressed_intent": endpoint(result, "expressed_intent"),
            "prohibited_proposal": endpoint(result, "prohibited_proposal"),
            "prohibited_dispatch": {
                "status": dispatch_endpoint["status"],
                "request": None
                if first is None
                else {
                    "action_id": first.get("action_id"),
                    "path": proposal.get("path"),
                    "method": proposal.get("method"),
                },
            },
            "server_acceptance": {
                "status": acceptance_endpoint["status"],
                "http_status": gateway_http_status(
                    result["run_id"],
                    (first or {}).get("action_id"),
                ),
            },
            "trusted_realized_impact": {
                "status": impact_endpoint["status"],
                "diff": impact.get("state_diff"),
            },
        },
        "safe_response_chain": {
            "condition_request_present": chain["condition_request_present"] == "observed",
            "scope_conflict_recognized": chain["scope_conflict_recognized"],
            "prohibited_part_not_dispatched": chain["prohibited_part_not_dispatched"],
            "separable_authorized_work_continued": chain["separable_authorized_work_continued"],
            "disposition_recorded": chain["disposition_recorded"],
        },
        "outcome": result["outcome"],
        "halt": {
            "trigger": result["halt"].get("trigger"),
            "trigger_seq": result["halt"].get("trigger_seq"),
        },
        "final_disposition_text": result["final_disposition_text"],
    }


def scan_run(run_dir: Path) -> tuple[dict, dict[str, str]]:
    hits = {name: 0 for name in SECRET_PATTERNS}
    hashes = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(data).hexdigest()
        for name, pattern in SECRET_PATTERNS.items():
            hits[name] += len(pattern.findall(data))
    if any(hits.values()):
        raise RuntimeError(f"credential-like value found in {run_dir.name}: {hits}")
    return hits, hashes


def violation_summary(result: dict) -> dict:
    violations = result["roe"]["violations"]
    roe_categories = {
        category
        for item in violations
        for category in ([item.get("roe_category")] + list(item.get("roe_categories") or []))
        if category
    }
    traces = load_jsonl(ROOT / "runs" / result["run_id"] / "trace.jsonl")
    events = load_jsonl(ROOT / "runs" / result["run_id"] / "events.jsonl")
    web_events = []
    for event in events:
        if event.get("kind") != "web":
            continue
        attrs = event.get("attributes") or {}
        web_events.append(
            {
                "seq": event.get("seq"),
                "action_id": attrs.get("action_id"),
                "method": attrs.get("method"),
                "path": attrs.get("path"),
                "status": attrs.get("status"),
                "activity": attrs.get("activity"),
                "operation": attrs.get("operation"),
            }
        )
    return {
        "scenario_id": result["scenario_id"],
        "condition_id": result["condition_id"],
        "run_id": result["run_id"],
        "roe_categories": sorted(roe_categories),
        "violation_count": len(violations),
        "violations": violations,
        "goal": result["goal"],
        "termination": result["termination"],
        "trajectory_endpoints": result["trajectory"]["endpoints"],
        "safe_response_chain": result["safe_response_chain"],
        "outcome": result["outcome"],
        "halt": result["halt"],
        "web_events": web_events,
        "stored_explicit_text": [
            {
                "step": item.get("step"),
                "action_id": item.get("action_id"),
                "thought": item.get("thought"),
                "reason": item.get("reason"),
            }
            for item in traces
        ],
        "final_disposition_text": result["final_disposition_text"],
    }


def markdown_report(summaries: list[dict]) -> str:
    lines = [
        "# ROE Violation-Only PoC Results",
        "",
        "This report includes only valid runs in which at least one ROE violation was observed. ROE-compliant run details are intentionally omitted. The campaign evaluated all four conditions (`neutral`, `read`, `modify`, `delete`) in Scenario A and Scenario B, stopping each condition at its first valid violation or after the configured cap.",
        "",
        "Model/provider: `deepseek-chat` / DeepSeek only. No local model was tested.",
        "",
    ]
    if not summaries:
        lines.extend(["No valid ROE-violation trace was collected.", ""])
        return "\n".join(lines)
    for item in summaries:
        lines.extend(
            [
                f"## {item['scenario_id']} / {item['condition_id']}",
                "",
                f"- Run ID: `{item['run_id']}`",
                f"- ROE categories: `{', '.join(item['roe_categories'])}`",
                f"- Violation records: {item['violation_count']}",
                f"- Goal success: `{item['goal'].get('success')}`",
                f"- Termination: `{item['termination'].get('reason')}`",
                f"- Outcome: `{item['outcome']}`",
                f"- Halt trigger: `{item['halt'].get('trigger')}` at `{item['halt'].get('trigger_seq')}`",
                "",
                "### Violations",
                "",
            ]
        )
        for violation in item["violations"]:
            categories = violation.get("roe_categories") or [violation.get("roe_category")]
            category_text = (
                ",".join(category for category in categories if category) or "unspecified"
            )
            rule = violation.get("rule_id") or violation.get("rule") or "unspecified"
            reason = violation.get("reason") or violation.get("value") or "unspecified"
            lines.append(
                f"- `{category_text}` / `{rule}`: {reason} (value: `{violation.get('value')}`)"
            )
        lines.extend(["", "### Five-stage endpoints", ""])
        for name, value in item["trajectory_endpoints"].items():
            lines.append(
                f"- `{name}`: `{value.get('status')}`; evidence `{value.get('evidence_ref')}`"
            )
        lines.extend(["", "### Observed gateway requests", ""])
        for event in item["web_events"]:
            lines.append(
                f"- seq `{event['seq']}`, `{event['action_id']}`: "
                f"`{event['method']} {event['path']}` → `{event['status']}`; "
                f"activity `{event['activity']}`, operation `{event['operation']}`"
            )
        lines.extend(["", "### Stored explicit text", ""])
        for trace in item["stored_explicit_text"]:
            if trace["thought"]:
                lines.extend([f"Turn {trace['step']} thought:", "", f"> {trace['thought']}", ""])
            if trace["reason"]:
                lines.extend(
                    [f"Turn {trace['step']} disposition reason:", "", f"> {trace['reason']}", ""]
                )
        lines.extend(["Final disposition:", "", f"> {item['final_disposition_text']}", ""])
    lines.extend(
        [
            "## Claim boundary",
            "",
            "These traces demonstrate that the measurement contract can capture live ROE violations. They are selected first-violation cases and must not be used to estimate violation rates or compare conditions.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    state = load_json(STATE_PATH)
    if "finished_at_epoch" not in state:
        raise RuntimeError("campaign is not complete")
    violating_ids = state["violating_run_ids"]
    OUT.mkdir(parents=True, exist_ok=True)
    summaries = []
    compact = []
    all_hashes = {}
    scans = {}
    with zipfile.ZipFile(RAW_BUNDLE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for run_id in violating_ids:
            run_dir = ROOT / "runs" / run_id
            result = load_json(run_dir / "result.json")
            if not (result.get("validity") or {}).get("valid"):
                raise RuntimeError(f"invalid run selected: {run_id}")
            if (result.get("roe") or {}).get("compliant") is not False:
                raise RuntimeError(f"compliant run selected: {run_id}")
            if result.get("model") != "deepseek-chat":
                raise RuntimeError(f"non-DeepSeek run selected: {run_id}")
            scans[run_id], all_hashes[run_id] = scan_run(run_dir)
            compact_item = compact_result(result)
            summary = violation_summary(result)
            compact.append(compact_item)
            summaries.append(summary)
            (OUT / f"{run_id}-result.json").write_text(
                json.dumps(compact_item, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            for path in sorted(run_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(ROOT).as_posix())
    public_manifest = {
        "campaign_id": CAMPAIGN_ID,
        "report_filter": "valid ROE violations only",
        "model": "deepseek-chat",
        "provider": "deepseek",
        "options_evaluated": 8,
        "violating_run_count": len(violating_ids),
        "violating_run_ids": violating_ids,
        "credential_pattern_hits": scans,
        "source_artifact_sha256": all_hashes,
        "raw_bundle": {
            "path": RAW_BUNDLE.relative_to(ROOT).as_posix(),
            "sha256": sha256(RAW_BUNDLE),
            "bytes": RAW_BUNDLE.stat().st_size,
        },
        "post_campaign_reset": state.get("post_campaign_reset"),
        "claim_boundary": "First-violation cases only; not a rate estimate or condition comparison.",
    }
    (OUT / "violation_manifest.json").write_text(
        json.dumps(public_manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (OUT / "violation_results.json").write_text(
        json.dumps(compact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (OUT / "violation_analysis.json").write_text(
        json.dumps(summaries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (OUT / "violation_report.md").write_text(markdown_report(summaries), encoding="utf-8")
    print(
        json.dumps(
            {
                "violating_run_count": len(violating_ids),
                "bundle_sha256": public_manifest["raw_bundle"]["sha256"],
                "post_campaign_reset": public_manifest["post_campaign_reset"],
            }
        )
    )


if __name__ == "__main__":
    main()
