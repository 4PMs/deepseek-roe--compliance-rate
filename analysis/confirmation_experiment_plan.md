# JS-001 Confirmation Experiment Proposal

## Why confirmation is currently required

The repository contains the historical 44/49 aggregate narrative but none of the 93 raw Run directories. The original execution period may also span harness changes, and the current DeepSeek provider path records a user-supplied `model_version` while calling the moving `deepseek-chat` alias. Consequently, the old dataset can motivate the hypothesis but cannot currently establish a paper-ready denominator, protocol identity, or model snapshot.

If the raw artifacts are recovered and prove homogeneous, complete, and explicitly valid, they should still be labeled exploratory because the focal contrast was selected after observing them. A separately frozen confirmation experiment avoids presenting an exploratory finding as confirmatory evidence.

## Proposed confirmatory design

Single-model case study using the same DeepSeek-Chat-based internal agent and JS-001 target.

| Arm | Purpose | Valid Runs |
|---|---|---:|
| Baseline | Current JS-001 policy with no added post-goal instruction | 30 |
| Length-matched neutral | Controls for receiving an additional instruction of similar length/structure, but requests only goal verification and reporting | 30 |
| Additional impact-seeking | Current `policy-capability.yaml` instruction | 30 |
| **Total** |  | **90 valid Runs** |

The primary preregistered contrast should be **additional impact-seeking vs length-matched neutral** for Run-level ROE violation. Impact-seeking vs baseline and neutral vs baseline are secondary contrasts. Goal Success and Safe Success are secondary outcomes. This ordering prevents choosing the most favorable contrast after seeing results.

## Exact additional Run count and stopping rule

- Required analyzable sample: **90 additional valid Runs (30 per arm)**.
- Invalid infrastructure/evaluation Runs do not count toward 30 and are replaced one-for-one in the same arm.
- Therefore the exact minimum is 90 attempts only if every attempt is valid; actual attempts are `90 + excluded Runs`.
- Safety/quality halt: suspend the experiment for diagnosis if an arm reaches 33 attempts without 30 valid Runs (more than 10% invalid) rather than silently continuing. Resume only under the same frozen protocol or restart all arms if a protocol-affecting fix is required.

No paid API or Docker batch execution is authorized by this document.

## Allocation and ordering

Use 10 randomized blocks, each containing one Run from each arm, then repeat this schedule three times to reach 30 Runs per arm. Persist the generated order before execution. Reset, provision, and verify the target before every Run. A failed reset/provision produces an excluded attempt and must not expose a model to a contaminated target.

## Protocol freeze checklist

Before the first paid Run, freeze and record:

- Git commit and clean working tree;
- scenario, each policy, neutral instruction, environment, and target image hashes;
- provider, requested model alias, any response-reported model identifier, agent adapter/version, temperature, max steps, timeout;
- seed requested/supported/applied status (the current code declares DeepSeek seed unsupported, so ordering—not seed—is the reproducibility control);
- gateway/database observer health and lifecycle correlation;
- reset/provision verification evidence;
- primary contrast, outcomes, exclusion rules, multiplicity treatment, and stopping rule.

The neutral text must be finalized and token/character matched before freezing. It must not request additional access, mutation, impact, or exploration. Changing any prompt or scorer after execution starts requires either a declared protocol deviation or a full restart; it must not be mixed silently into one denominator.

## Analysis plan

For each arm, report numerator/denominator and Wilson 95% confidence intervals for Goal Success, ROE Violation, and Safe Success. Use Fisher's exact test for preregistered pairwise binary contrasts. Report risk difference and risk ratio with 95% confidence intervals; do not rely on p-values alone. Report attempted, valid, excluded, and `legacy_unknown` counts separately. Violation-event counts and PUT/PATCH/DELETE categories are secondary descriptive analyses, not substitutes for Run-level rates.

## Decision after artifact recovery

1. Run `python -m analysis.audit_js001 --runs-dir <recovered-runs>`.
2. Review `analysis/outputs/js001_audit_report.md`, exclusions, hashes, and arm comparability.
3. If the 93 Runs are unavailable or heterogeneous, retain them only as unverified historical/exploratory context and execute the 90-valid-Run confirmation after explicit approval.
4. If they are homogeneous and reconstructable, use them as exploratory results and still prefer the frozen 90-valid-Run confirmation for the paper's primary claim.
