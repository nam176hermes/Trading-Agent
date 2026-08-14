# Foundation test governance evidence

Date: 2026-08-02
Package: Test skip governance and critical coverage

## Implemented controls

- Exact node-level pytest reporter for passed, failed, skipped, deselected and not-run tests.
- Collection-integrity detection for individual tests removed by repository hooks.
- Fail-closed session status for any collected test that never executes.
- Shared recursive Dashboard inventory consumed by `npm test` and governance, with file-qualified TAP and shell integration observations plus per-file zero-observation rejection.
- Committed 259-entry skip and deselection allowlist sealing component, exact node ID, outcome and normalized reason.
- Strict approved-category, field, ownership, expiry and approval validation.
- New skip, stale allowlist, outcome drift and runtime-reason drift rejection.
- Security-critical approval-metadata enforcement based on canonical component, path and category rules.
- Machine-readable merged governance report.
- Fail-closed stale-artifact removal and machine-readable error reports.
- Private evidence directories and descriptor-relative atomic report writes resistant to symlink and parent-replacement races.
- Per-unit Python and dashboard branch coverage ratchets.
- Exact parameterized executed-test proof for the required safety cases.
- Sealed critical source and required-case identities with denominator and covered-count shrinkage rejection.
- Canonical `make check-test-skips` and `make check-critical-coverage` targets.
- Both gates added to `make ci`, which is already called by `.github/workflows/foundation.yml`.
- CI artifact publication for `/tmp/trading-agent-test-evidence`, retained for 14 days.
- Runtime evidence stored outside the checkout to preserve secret-hygiene scanning.

## Test-first evidence

The first focused governance run failed during import because the checker modules did not exist. After implementation and one message-contract correction, the focused policy suite passed:

```text
7 passed in 0.09s
```

A host-independent stale lease fence test and report-integrity cases were then added. Focused verification passed:

```text
18 passed in 0.29s
```

No existing failure was converted into a skip. No selection filter was broadened. No safety assertion was lowered.

## Critical coverage evidence

Fresh canonical checker result:

```text
692 passed, 44 skipped, 5 deselected in 10.35s
packages/domain: lines=95.1359% branches=87.3737%
packages/event_ledger: lines=95.2596% branches=90.1316%
services/job_worker/safety.py: lines=86.8613% branches=80.3571%
job state machine: lines=100.0000% branches=100.0000%
transition authority: lines=86.3636% branches=78.2609%
transition repositories: lines=85.4801% branches=75.4237%
dashboard auth and mutation policy: lines=95.7684% branches=88.6792%
```

Exit status: 0.

Machine report:

```text
/tmp/trading-agent-test-evidence/critical-coverage/critical-coverage.json
```

The report records status `pass` only when every required safety case appears in successful test observations. Failed, skipped and deselected nodes do not satisfy this requirement. Every collected variant of a parametrized required test must pass.

## Inventory evidence

| Check | Result |
|---|---:|
| Managed entries | 259 |
| Root skips | 228 |
| Root deselections | 29 |
| Legacy skips | 2 |
| Dashboard skips | 0 |
| Unknown categories | 0 |
| Duplicate component/node pairs | 0 |
| Security-critical entries | 248 |
| Security-critical entries without approval metadata | 0 |

The historical supplied aggregate said 226 root skips. The current full-suite observation found 228. The node-level live result is committed and the discrepancy is visible rather than normalized away.

## Security hygiene evidence

The first in-checkout report exposed a deliberate interaction with the repository secret scanner: synthetic security-test node IDs contain pattern names such as credential URI and private key. The scanner correctly rejected those untracked report files.

The implementation now writes runtime evidence under `/tmp/trading-agent-test-evidence`. Fresh targeted verification returned:

```text
1 passed in 0.97s
```

This preserves the scanner and changes report placement instead of weakening secret detection.

## Latest production-gate evidence

The transition-repository evidence now includes 23 additional provider-free cases for scheduler enqueue rollback and authority validation, read-filter boundaries, empty claims, lease-control and heartbeat validation, retry finalization, and expired-lease recovery observations. The measured transition-repository floor is 365/427 lines and 89/118 branches; the policy advances the next steps to 95% lines and 80% branches.

The final `make check-critical-coverage` run executed 692 Python tests and 52 focused dashboard tests, validated every exact required case, and held every line, branch, denominator and covered-count ratchet. Exit status: 0.

`make check-test-skips` collected all three components and validated the exact 259-entry allowlist. Its merged report recorded 5,085 passing tests, zero failures, 230 skips, 29 deselections, zero not-run tests and 245 approval-blocked observations. Dashboard evidence came from the same recursive inventory used by `npm test`: 14 Node test files plus `dashboard-security.integration.sh` and `mode-auth.integration.sh`. The command exited zero.

Portable source-authority integration separates strict and portable verification. Strict mode verifies source authority and imported bytes at each unique atomic introduction commit, while permitting legitimate later monorepo evolution. Portable audit is explicit and available only when every declared external repository is absent; it verifies introduction and HEAD against immutable embedded evidence. Partial authority availability, modified imported bytes, manifest identity drift, aggregate drift and coordinated authority-plus-manifest tampering fail closed. The complete consolidation suite passed 171 tests.

The canonical `make ci` result and independent-review verdict are candidate-bound evidence. They must be generated after the final repository bytes are sealed and retained outside the repository. Earlier green CI or review output never accepts a modified candidate.

## Decision rule

Use `GO -- TEST EVIDENCE IS MANAGED` only when:

- the allowlist comparison passes with no unknown, stale, changed-outcome or changed-reason entry;
- no collected test is hidden or left not-run;
- required critical cases are observed as executed under their exact sealed identities;
- every coverage ratio and exact counter is at or above its committed ratchet;
- current private machine reports are generated;
- canonical `make ci` exits zero on the exact candidate;
- a fresh isolated read-only review of the same candidate has no unresolved high or medium finding;
- candidate bytes do not drift between review, CI and the acceptance decision;
- no unrelated safety or security assertion is weakened.

The final GO or NO-GO decision belongs in the external exact-candidate acceptance record, not this source document.

## P0-07 native-capability evidence boundary

Native capability evidence is now distinct from external-authority evidence.
The two native codes use strict receipt schema
`t-g03a-native-capability-receipt/v2`; external codes continue to use v1.
Native v2 binds Foundation run/head/date/context, the locked active-inventory
digest, exact 16/8 node lists, one closed retained-FD probe, exact execution
counts, outcome, completeness, and receipt hashes.

The implementation executes real inert namespace operations through retained
root-owned `/usr/bin/bwrap` and `/usr/bin/unshare` descriptors. Bubblewrap uses
the committed sealed-UV policy validator. Absence and only exact reviewed host
namespace-policy denial can defer. Invalid identity, misleading or partial
output, timeout, execution error, test failure, and executable replacement
publish FAIL and fail the lane. No synthetic executable establishes native
availability.

Native receipt/governance evidence is private, no-clobber, descriptor-retained,
and replacement-postchecked. PASS requires exact governance bytes matching the
baseline's sealed custody and all expected nodes. DEFERRED requires no
governance. Portable aggregation accepts and surfaces explicit DEFERRED, while
the future-facing `validate-native --require-pass` caller rejects it. That host
qualification mode is not wired into portable CI in P0-07.

TDD covers native-v1 rejection with external-v1 preservation; strict context,
probe, count, code and node binding; real retained-FD argv; narrow denial and
timeout classification; absent versus present-invalid authority; strict FAIL
publication; exact 16-plus-eight execution; retained executable replacement at
multiple boundaries; symlink, mode, owner, parent and leaf artifact mutation;
forged PASS; and no-clobber retry behavior. Exact final-head runtime evidence
and independent review remain candidate-bound and belong in the external task
report; this tracked document does not convert a pre-commit run into final
acceptance and does not claim full CI.

The P0-07 review correction closes two additional acceptance gaps. Aggregate
and governance consumers now require the complete canonical filename set from
`CODE_CLASSIFICATION` independent of caller order, reject renamed/extra/
missing/duplicate paths, and route trusted native identities through paired v2
receipt/governance validation. The former public raw native status reducer is
gone; portable and future host-require-pass callers consume validated artifact
sets, while external v1 remains supported. AVAILABLE publication now treats
the outer retained-authority postcheck, PASS receipt write, and
post-publication identity postcheck as one guarded transaction. Replacement at
any of those boundaries removes only byte-identical task-owned PASS/governance
leaves with descriptor-relative no-follow rollback, publishes exact
`NATIVE_IDENTITY_REPLACED` FAIL, and cannot leave acceptance evidence.

## P0-07 architecture-A evidence correction

The preceding rollback description is historical and is not the implemented
acceptance model. Native v2 publication is now append-only. Each code has a
deterministic private `<CODE>.artifacts` directory containing canonical
`receipt.json`, optional PASS `governance.json`, and a self-hashed
`manifest.json`; the final canonical `<CODE>.json` marker contains exactly the
bundled receipt bytes and is the sole acceptance point. External v1 remains
flat.

Candidate leaves and directories are fsynced, the retained executable is
postchecked before and after atomic Linux `renameat2(RENAME_NOREPLACE)` bundle
publication, and the marker is installed no-clobber last. The native path has
no rollback/unlink API. Crash, rename failure, identity drift, foreign
occupancy, or ambiguous publisher response never authorizes deletion or
overwrite. Exact marker bytes plus a fully valid exact bundle resolve an
after-write exception as success; any foreign/invalid state fails closed.
Identity drift before a free marker publishes strict FAIL and leaves earlier
candidate/bundle bytes inert. A post-marker authority check cannot revoke the
already accepted transaction.

The retained reader validates the canonical marker and deterministic bundle as
one set: private parent/bundle/leaves, exact inventory, marker/receipt byte
identity, strict manifest bindings and self-hash, exact PASS governance with
sealed custody, no governance for DEFERRED, and no accepted FAIL. It rejects
legacy flat native governance, stale bundle layouts, symlink or identity
replacement at parent/bundle/leaf boundaries, manifest tampering, and extra
entries. Random candidate and execution directories remain inert. Focused TDD
also covers unresolved versus exact rename response, deterministic foreign
preoccupancy without deletion, inert crash-stage candidates, ambiguous marker
publication, canonical filename routing, portable DEFERRED versus future
host-require-pass, and external-v1 preservation. Final-head real-host counts and
artifact hashes remain task-report evidence rather than a claim in this tracked
document.

## P0-08 external-authority v2 evidence boundary

The two external codes now use strict
`t-g03a-external-authority-receipt/v2` and append-only Architecture-A bundles;
flat external v1 is stale. Each receipt binds the exact three governed nodes,
Foundation run/head/date/context, inventory, execution counts, strict
code-specific safe authority facts, completeness, outcome and self-hash.
External facts are counts, identities and SHA-256 commitments only: no absolute
paths, corpus or database values, research contents, raw subprocess output,
credentials or environment secrets are serialized.

Phase-3B retains the fixed reviewed corpus-root descriptor and rechecks the
production analyzer plus required-entry commitment on both sides of
deterministic bundle publication. Legacy qualification retains and invokes the
fixed UV descriptor through `/proc/self/fd`, performs frozen offline sync with
fixed argv/environment, snapshots the exact legacy closure, and postchecks the
named and held UV identity/digest plus closure commitment. Exact tests run only
for VALID sessions. Entire-authority ABSENT emits DEFERRED with no governance;
PARTIAL, INVALID, DRIFTED, timeout or exact-test nonpass emits FAIL and stops.

External acceptance is marker-last and no-clobber: a private fsynced candidate
with receipt, optional PASS governance and self-hashed manifest is published by
`RENAME_NOREPLACE` at `<CODE>.artifacts`; only after the second authority check
may identical receipt bytes be installed as `<CODE>.json`. The transaction has
no unlink or rollback path. Readers retain and postcheck the parent, marker,
bundle and leaves, reject foreign occupancy/tampering/replacement, require exact
sealed-custody governance for PASS, forbid it for DEFERRED, and never accept
FAIL. Portable validation allows explicit DEFERRED; the separate
`validate-external --require-pass` mode requires both external groups PASS.

Focused TDD covers strict v2 bindings and stale v1 rejection, retained Phase-3B
and UV/closure drift, exact 3-plus-3 selection, whole absence versus invalid
FAIL, artifact tampering and renamed markers, foreign bundle/marker occupancy,
ambiguous marker completion, no-unlink behavior, portable DEFERRED versus host
require-pass, and standalone custody/baseline orchestration. Candidate-bound
real-host results, hashes and final commit identity belong in the controller
report and do not become an evergreen readiness claim here.
