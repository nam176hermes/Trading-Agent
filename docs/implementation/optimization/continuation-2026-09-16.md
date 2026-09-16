# M8/M9/M4/M7 continuation

Parent: `43931b262f58d2de50ac09fb17307f63c7e49df3`. The operator asked to
continue these four milestones while that candidate's hosted CI runs. Work is
isolated on `codex/optimization-continuation-20260916`; the existing candidate,
canonical checkout and preserved S/T checkouts remain separate.

## Implemented source changes

### M8: keep calculation inputs separate from retained outputs

The existing Bubblewrap child receives a released calculation view. Its parent
previously recomputed the returned evaluation through the disk CAS instead.
That fails when custody correctly keeps the raw holdout out of the research
CAS. The parent now reuses `ReplicaArtifactStore` with the released view as
its input reader and the child's private directory as its output reader.

Recomputation also records the exact verified output names. Extra child files,
including a copied raw holdout bar, reject the attempt before any result or
output is retained. Checking only canonical JSON and content hashes was
insufficient: those checks also accept an unrequested copy of protected input.

The regression uses a private synthetic CAS with all 365 holdout bars and the
buffer row removed after constructing the view. Evaluation/readback is real;
the subprocess launcher in this test is synthetic. This is not cross-UID
custody or sandbox qualification.

### M9: require the same released view for exit recomputation

`evaluate_phase_exit` and `prepare_phase_exit` now require an explicit
`HoldoutCalculationView`. The exact manifest and instrument must be present in
that view. Retained provenance still comes from the bounded artifact reader;
calculations use the released inputs plus retained outputs. `ReadbackStore`
continues to require identical retained bytes and cannot fabricate missing
results. No second disclosure or plaintext persistence is introduced.

Exit tests remove raw holdout data before recomputing all thirteen checks and
preparing the existing SQL publication proposal. Missing or wrong views fail;
economic rejection stays FAIL and a native mismatch stays HELD. The actual
pinned native diagnostic also calculates both reference roles directly from
the view, without a disk input-store dependency.

This repairs calculation plumbing. It does not supply the protected native
launch owner, authenticate synthetic fixture receipts or join official jobs.

### M4: consolidate duplicate fixed paths without changing approval

The worker's Phase 1 safety root, migration approval source and semantic
refresher's report/macro roots now derive from the existing
`CANONICAL_SAFETY_SOURCE_ROOT`. Their resolved values and fingerprints remain
unchanged; no environment or HOME fallback is added. Frozen paper projections
and toolchain pins are untouched. V2 child paths already come from attested
`RuntimePathsV2` and retain their two-deployment-root regression.

This removes four repeated absolute path literals. It does not make the fixed
Phase 1 deployment portable. That still requires the separately reviewed
authority migration, including exporter fingerprint, snapshot admission,
semantic producer approval and every consumer on the same deployment.

### M7: diagnose the retained incomplete day precisely

Timestamp rejection now reports the requested day, observed boundaries,
expected boundaries and timestamp unit. A checksum-valid truncated candle
is rejected without artifact retention or transport retry. The regression
uses the observed 2018-02-08 close time, with synthetic price/volume columns.

Offline inspection reconfirmed the retained ZIP SHA-256
`c26a18378ee1e06b466e5e11defd014c67267d4545e49ec61a52cfa86842fc0e`:
one 12-column CSV row; open `1518048000000`, close `1518049694788` milliseconds.
The required inclusive close is `1518134399999`, a shortfall of 84,705.211
seconds. Rechecking identical bytes cannot repair this dataset. The retained
research dataset still has 38 valid days out of 2,800; no new acquisition,
holdout access, resampling, provider substitution or policy change occurred.

## Approved lifetime contract — source implementation in progress

The accepted private interfaces cover release and native requests, but the
current workflow runs one operation and destroys its worker afterward. Keep
official late-operation admission closed until the following contract has
been implemented and verified as a whole. The operator approved this design on
2026-09-16 with “Duyệt, tiếp tục triển khai”; that approval covers source/tests.

1. Use one bounded parent process within one genuine `p3-authority.yml` run.
   It owns the single released view until exit or failure. Reuse worker claim,
   heartbeat, cancellation and output owners; do not add a daemon or raw-data
   persistence. A Python object is calculation transport, not authorization.
2. Admit HOLDOUT, PARITY and PHASE_EXIT independently through their existing
   job/intent/review contracts. Later intents reference actual retained earlier
   results, so their exact approvals cannot be manufactured before those
   results exist. The parent may wait only within an explicit protected session
   deadline and the enclosing workflow deadline; it cannot approve its own
   next step or extend an expired review.
3. Bind the protected session to source, environment, primary selection,
   commitment, workflow run/attempt, process identity and the exact ordered job
   attempts. Session continuity uses the approved private authority binding. Changing the profile's current job must not allow replacement of
   the source, view, native runtime or process while retaining authority.
4. Before every replica and each durable output commit, recheck that stage's
   current claim, safety, protected profile and approval. Completed earlier
   jobs do not authorize later calculations. No precomputed future PASS or
   caller-supplied workflow identity substitutes for these checks.
5. Native execution needs its own reviewed P3 extension of the sealed launch
   owner: exact adapter/runtime inventory, bounded sealed request, fresh
   process per replica, both roles and parent-observed receipts. P1's frozen
   entry inventory cannot be redirected to a different Python entrypoint.
6. Publish only verified, allowed output artifacts through the existing SQL
   publication/readback owner. Exit recomputes with the same released view.
   Commit acknowledgement loss uses durable result recovery, not another run.

| Event | Required result |
| --- | --- |
| Approval unavailable before deadline | HELD; no automatic next-stage approval |
| Cancellation, safety change or lease loss | Stop children; deny further calculations and publication |
| View owner dies or workflow restarts | Attempt remains unusable; no second disclosure |
| Disclosure/release acknowledgement uncertain | Recover ledger facts only; never request raw data again |
| Native role/source/runtime substitution | HELD before a receipt or parity decision |
| Economic primary failure | Retain FAIL/REJECTED; no fallback primary |
| Publication acknowledgement lost | Exact durable readback; no repeated experiment |

## Focused verification

| Check | Result |
| --- | --- |
| Original view and exit baseline | 28 passed |
| Missing-CAS-input regression before repair | 1 failed as expected |
| Extra raw/unrelated child outputs before repair | 2 failed as expected |
| Acquisition, replica execution, view, exit, native-request and parity-pair regression | 115 passed, 2 opt-in native skips |
| Missing/wrong exit view | 2 passed |
| Actual pinned native diagnostic, both roles with three processes each | 2 passed; covers the two opt-in cases above |
| Worker safety/child environment, semantic refresher, real-apply approval | 111 passed |
| Pinned Ruff and root/legacy error-level Basedpyright | Passed; warning-level debt is not measured by this gate |
| High-severity Bandit source scan | Passed; not an independent security review |
| `make audit-portable` | Passed with strict component provenance |

The initial linked checkout failed the standalone-root audit and was replaced
with a verified standalone clone at the same HEAD/tree. Strict audit, contracts
and dashboard build then passed. Its canonical `make test-all` remains separately
tracked in the external evidence ledger. None of these checks grants runtime
authority.

## Session native execution checkpoint

The bounded session now consumes its private native provider through the existing
ProcessRunner, in PRIMARY R1–R3 then SELECTED_BASELINE R1–R3 order. All six
children use the actual parity job/attempt and separate protected stream roots.
The parent checks distinct observed process identities, exact consumed launch
fingerprint/request/closure/policy, successful cleanup and bounded stream bytes
before normalizing each result into the session's fenced store. Failure poisons
the launch owner, closes the session view and preserves observed outcomes for
worker finalization. Raw requests remain sealed inputs; no public replay receipt
is issued by this runner.

Focused tests exercise real sealed launch consumption with synthetic host
attestation, invalid results and revocation. An additional test executes six
actual OS children through ProcessRunner using synthetic output; it does not run
the protected engine. The official coordinator, SQL process replacement and
private parent-proof publication are still required before opening a late lane.

The session aggregate at b851970 failed two headless tests because this fresh
checkout lacked the production dashboard build. Run `make build-dashboard`
before headless qualification that sets `build_dashboard=False`. After building,
all nine headless tests passed; the remaining legacy/dashboard tests, typecheck
and lint also passed. These repairs do not relabel the original aggregate PASS
or qualify the subsequent native-execution changes.

Final focused regression: 323 passed in 165.04s; closed paper import graph: one
passed. Root and legacy error-level typing, production Ruff and scoped Bandit
passed. Independent security review and full candidate qualification remain
separate. Evidence is in the external SESSION.json ledger.

## Closure conditions

### Native SQL source checkpoint

Migration `0028_p3_native_process` adds a separate worker-only compare-and-replace
capability for native PARITY process identity. It preserves the fixture function
and rejects wrong roles, malformed identities, stale previous identities, wrong
lanes, cancellation, expired leases and a changed current attempt. Job/attempt
row locks precede the fresh fence; the repository performs one transaction and
does not retry an uncertain acknowledgement. The parent catalog is checked in
the same `pg_catalog` search path used by custody admission; the expected digest
is unchanged.

The opt-in disposable PostgreSQL harness applies the real migration chain and
uses real API enqueue, authority acceptance, worker claim/start and replacement
transactions with synthetic inputs. It verifies one winner among concurrent
replacements, denials after observed lock waits, and terminal crash recovery
without another experiment. Final SQL check: one passed in 86.14s, four existing
pool deprecation warnings; owned cluster cleanup asserted. Worker regressions:
151 passed; portable runtime-test inventory and paper import checks: two passed.

Existing worker admission stays at 0026 and custodian admission at 0027. This
source capability still requires a separately pinned session catalog/admission
and consumption by the actual coordinator. No production migration, protected
launch, official receipt or new milestone completion is implied.

- M8: reviewed session ownership plus actual protected cross-UID release,
  cancellation/death/expiry tests and official worker integration.
- M9: protected P3 native owner, six parent-observed launches, both-role parity
  and SQL-owned exit publication under that session.
- M4: approved source/consumer migration and separate protected deployment
  verification. Source deduplication cannot migrate a running safety boundary.
- M7: a complete eligible archive for the fixed day from the approved source,
  or an explicitly reviewed new dataset policy/campaign. Fabricating the
  remaining UTC interval or silently moving the research window is invalid.
- Hosted CI must cover the eventual continuation commit. Earlier candidate CI
  and local diagnostics do not qualify these changes. Independent security
  review and protected runtime qualification remain separate verdicts.

## Session source slice (separate from checkpoint 67942d8)

`services/job_worker/p3_session.py` owns a single view through three separately
admitted stage contexts. It reads immutable root-protected `session.json` and
rotating `stage.json`/`profile.json` under the same workflow run/attempt directory.
The session binds source, environment, primary, custody commitment, closure,
parent PID/start/command/group, boot ID and deadlines. Each stage binds the
ordered job/attempt/worker/lease-token hashes and the current host profile digest.
Current reviews are rechecked through the existing host-profile reader; the
worker-supplied fence must query the current lease/cancellation and safety.

The one release attempt is consumed before contacting the existing release
helper. An uncertain acknowledgement closes the session. A released view is
process-bound and revocable; existing reader aliases stop working after close,
between stages, or on expiry. Closing drops owned references, not a claim of
zeroizing copies already made. Children still require the existing bounded
process cleanup owner. The monotonic deadline prevents wall-clock rollback
from extending the session.

The private `NativeParityInput.native_request_ref` identifies a
`p3-native-commitment-v1` artifact containing the two ordered request hashes,
source, environment and manifest/spec references. It contains no steps/prices.
Requests are reconstructed from the same view in PARITY. Phase-exit proposal
preparation uses that view and fences each retained write. SQL publication and
acknowledgement recovery remain with their existing concrete owner.

Holdout calculator retention also rechecks its existing authority callback after
the child exits and before each result, artifact, inventory or replay-receipt
write. Retention goes through the executor's fenced store interface. This
prevents a successful calculation from retaining further outputs after claim
revocation; it does not replace the worker's active-child cancellation or SQL
transaction fences.
The release helper also checks before retaining each request and manifest artifact.

The existing worker also binds receipt recovery to the exact publication request
and commit result returned by its SQL owner. A different canonical readback fails
before writing receipt artifacts or marking local processing complete. Historical
receipt recovery remains available after lease expiry and never reruns research.

This source slice does **not** open the official late lane. Remaining source
work is the consumed bounded workflow/worker coordinator, six observed native
processes with parent-bound results, and complete SQL terminal readback
coordination. `stage()` returning normally is not a durable success receipt; the
worker must finish canonical readback before admitting another stage. The new
owner is not yet called by `run_official_once`, and cannot be counted as M8/M9
completion. No new service, dependency, public contract or P1 inventory change.

The session now admits one private native launch provider during PARITY using
root-protected `native.json` alongside its other profiles. The profile binds the
session, job/attempt, source, digest-only request commitment, runtime inventory
locations and sandbox binary hash. Existing P1 closure verification and sealed
file helpers verify the base engine; a separate P3 entry and adapter extend it.
Requests travel only in bounded anonymous read-only memory. The existing
`ProcessRunner` consumes exact one-use native tokens and owns process cleanup.
Tokens enforce PRIMARY R1–R3 then SELECTED_BASELINE R1–R3, fresh admission and
expiry checks, and no continuation after a preparation failure.

The native launch has its own closure and sandbox policy digests, including its
entry, resources, fixed arguments and socket-denial program. These must never
be relabeled as the driver sandbox digest in a replay receipt. Tests of tokens,
sealed descriptors and the synthetic runner do not prove six actual protected
engine processes. That parent-observed orchestration and accurate result
provenance remain required before enabling the late lane.
