# Repository and JS-001 Data Audit

## Scope and evidence date

Audit performed on 2026-09-11 against the local repository and its sibling source checkout. No `.env`, API key, or credential file was read.

## Repository identity

- Repository root: `C:/Users/Scar0/Desktop/4pms_paper`
- Branch: `main` tracking `origin/main`
- HEAD: `bc4e703646520653751044947d43e3ad3fb26ea8` (`feat: base project copied`)
- HEAD is an initial commit with no parent, so the new repository alone does not retain source history.
- The sibling source checkout `C:/Users/Scar0/Desktop/4pms_benchmark` is at `13afed95b5006aed3eaeada19a404e8edb75272c`.
- Tree comparison found all 265 files tracked by source commit `13afed9` in the new commit with identical Git blob IDs. The new commit has 37 additional tracked files (the dashboard subtree and KIPS template) and no missing source-tracked files. This identifies `13afed9` as the local source-commit candidate for the copied core, while `bc4e703` remains the only authoritative history in the new repository.
- Initial working-tree change before this audit: untracked `IDEA.md`.

No `AGENTS.md`, `CLAUDE.md`, or `.cursorrules` was present. The active project contract therefore comes from the user request plus repository README/configuration.

## Project structure relevant to the paper

- Core implementation: `src/tempera/`
- JS-001 artifacts: `scenarios/JS-001/{scenario.yaml,policy.yaml,policy-capability.yaml,oracle.yaml}`
- Environment declaration: `environments/juice_shop/environment.yaml`
- Existing aggregation: `scripts/aggregate.py`
- Historical aggregate narrative: `docs/실험 결과.md`
- Experiment-design artifacts: `experiments/benchmark-v1/`
- KIPS Word template: `.hermes/desktop-attachments/KIPS 학술발표대회 논문양식_v1_word.doc`
- Paper-specific offline analysis: `analysis/`

The KIPS template is a legacy Composite Document File V2 Word document. File metadata reports 2 pages. It was only identified and inspected for metadata in this audit; no paper draft was generated yet.

## Raw Run inventory and blocker

The current repository has no `runs/` directory. Outside synthetic dashboard fixtures and one benchmark-v1 readiness artifact, repository-wide inventory found:

- `config.json`: 4
- `result.json`: 3
- `events.jsonl`: 4
- `lifecycle.jsonl`: 1

The three complete config/result/events groups are explicitly synthetic dashboard demo runs. The readiness run has no `result.json`. They are not the historical JS-001 experimental dataset and must not enter paper statistics.

The sibling `4pms_benchmark` checkout also has no `runs/` directory and no historical Run artifacts. Therefore:

- 0/93 historical JS-001 raw Runs are available.
- Baseline 44 and impact-seeking 49 cannot be recounted.
- Goal 38/38, ROE violation 3/29, Safe Success 36/9, violation-event totals, and 179 attempted / 176 valid cannot be verified from raw evidence.
- Existing values in `docs/실험 결과.md` remain unverified historical aggregate claims, not final paper results.

## Implemented eligibility audit

`analysis/audit_js001.py` reads `config.json`, `result.json`, `events.jsonl`, and optional `lifecycle.jsonl` directly. It does not use the Dashboard Adapter or `BenchmarkResult.from_dict`, both of which can treat missing legacy validity as valid.

Primary model-performance inclusion requires:

1. exact JS-001 scenario and exact preregistered `config.policy` arm mapping;
2. explicit `result.validity.valid == true`;
3. readable, non-empty config/result/events artifacts;
4. matching config/result/event Run IDs;
5. boolean `goal.success` and `roe.compliant`;
6. no duplicate Run ID or duplicate core artifact bundle.

Missing validity becomes `legacy_unknown` and is excluded pending artifact-level adjudication. Explicit invalid Runs remain in attempted/excluded counts. `policy_denied` is excluded from intrinsic model-performance denominators and is separately marked guardrail-evaluable only when enforcement was enabled. Missing lifecycle is reported as an audit flag but is not by itself treated as a fatal legacy exclusion; malformed lifecycle and basic action-stage correlation problems are reported separately.

## Current harness validity finding

`src/tempera/agent/runtime.py` emits `adapter_error`, but `src/tempera/runner.py` did not define it in `_INVALID_REASONS`. `_with_execution_status` consequently raised `KeyError('adapter_error')`; in a full pipeline this could mask the adapter failure and cause downstream evaluator-error handling. The mapping is now explicit: `adapter_error` is a failed, infrastructure-invalid Run with reason `experiment_infrastructure_failure`. A regression test covers this path.

## Document-aggregate arithmetic check (not raw-data verification)

`analysis/statistics.py` independently recalculates the values encoded in the audit specification. These calculations confirm arithmetic only; they do not establish that the 44/49 denominators are eligible Runs.

| Outcome | Baseline Wilson 95% CI | Impact-seeking Wilson 95% CI | Risk difference (95% CI) | Risk ratio (95% CI) | Fisher two-sided p |
|---|---:|---:|---:|---:|---:|
| Goal Success | 38/44, 73.29%–93.60% | 38/49, 64.12%–86.98% | -8.81%p (-24.07, 7.30) | 0.898 (0.742, 1.087) | 0.2974 |
| ROE Violation | 3/44, 2.35%–18.23% | 29/49, 45.25%–71.78% | +52.37%p (34.36, 65.74) | 8.680 (2.841, 26.519) | 5.606e-8 |
| Safe Success | 36/44, 68.04%–90.49% | 9/49, 9.98%–31.36% | -63.45%p (-75.52, -44.51) | 0.224 (0.122, 0.412) | 5.678e-10 |

The prompt's tentative baseline Goal Success interval (approximately 73.1%–92.2%) is not reproduced. The Wilson interval from 38/44 is 73.29%–93.60%. Machine-readable values, full precision, and a source warning are stored in `analysis/outputs/documented_aggregate_statistics.json`.

## Current conclusion

The historical effect remains suitable only as exploratory motivation until the 93 raw Run directories are recovered and audited. No paper-ready empirical count has been verified in this repository. The immediate data blocker is the missing raw `runs/<run_id>/` artifact set, ideally including config, events, lifecycle, trace, result, status/progress, and bundle metadata for every attempted run.
