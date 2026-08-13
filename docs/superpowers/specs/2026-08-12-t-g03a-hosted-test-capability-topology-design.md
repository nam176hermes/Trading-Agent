# T-G03A: hosted test-capability topology design

**Status:** proposed design only; pending independent design review. This
authorizes no implementation, dependency install, engine build,
external-authority acquisition, workflow/Make change, runtime change, release,
or live trading behavior. Do not commit it until the controller requests that
after clean independent review.

## Locked hosted evidence and inventory

Hosted Foundation run `31641536482`, job `94264953010`, ran at
`18f22198c65c7bc735aeb848d8fda55209d01e78` and ended `62 failed, 5451
passed, 281 skipped, 29 deselected`. The workflow held
`LIVE_EXECUTION_ENABLED=false` and `LIVE_TRADING_APPROVED=false` and called
`make ci-portable`.

The single canonical inventory for all hosted T-G03C selectors and verifiers is
the tracked file `tests/fixtures/t-g03a-hosted-failure-inventory.tsv`. This
design-only change does **not** create that file. T-G03C must add it as a
reviewed source/test fixture whose bytes are exactly identical to the historical
sealed ignored evidence inventory
`.superpowers/sdd/TRADING_AGENT_PHASE1_TERRA_AUTOPILOT_CONTINUE_V4_2/t-g03a-inventory.tsv`:

* Exactly 62 rows, one exact failed node ID per row.
* SHA-256: `99e2e9f0ea91c65fd841a0b81b8948eb6d3967203627d0911c151794737a8bfe`.
* 32 `PORTABLE_SOURCE_DEFECT`, 24 `NATIVE_CAPABILITY_REQUIRED`, and 6
  `EXTERNAL_AUTHORITY_REQUIRED` rows; no unclassified row.

The tracked fixture must have SHA-256
`99e2e9f0ea91c65fd841a0b81b8948eb6d3967203627d0911c151794737a8bfe` and
must be byte-for-byte identical to that ignored evidence inventory, including
TSV ordering and final newline. The ignored SDD copy is historical evidence
only, is never a hosted runtime input, and has no selector/verifier authority.
Any implementation must reject a different tracked-fixture hash,
omitted/duplicate ID, blank required field, or classification outside these
three values. A future tracked-fixture change needs a newly reviewed inventory
and design, not an in-place category edit. The `.tsv` file is canonical; any
prior `.csv` reference is incorrect and has no authority.

T-G03C must install the tracked fixture through one reviewable copy-and-verify
step before any lane selection: read the tracked bytes, verify the literal
SHA-256 above and schema/row mapping, copy them to a new private evidence path,
then reopen and byte-compare the installed file to the tracked source and
recompute the same hash. All hosted selectors, lane preflights, completeness
verifiers, and receipt writers must consume that verified installed copy of the
tracked fixture, never the ignored SDD file and never an ad-hoc embedded node
list. The implementation tests must fail closed on drift among the embedded
literal hash, tracked bytes, installed bytes, and every row's node/lane/code
mapping; they must also reject a changed row count, reordered/duplicate node,
or a selector result not exactly derived from the installed fixture.

## Boundaries that must remain unchanged

| Boundary | Preservation requirement |
| --- | --- |
| `make ci`, `make audit`, `make audit-release` | Strict/local semantics remain unchanged. Portable is never a release mode. |
| Foundation workflow | Retain canonical Make orchestration and both live flags false; do not replace it with individual test commands. |
| `scripts/test_governance_pytest.py` and Foundation artifact | Retain node-level collection/outcome reporting. Foundation owns and must keep artifact name `test-governance-${{ github.run_id }}`, path `/tmp/trading-agent-test-evidence`, and retention `14` days. |
| Production/authority/runtime sources | No validator, manifest, protected root, service, database, scheduler, or trading behavior change. |
| Engine/corpus inputs | No build, download, corpus copy, substitute binary, broker/exchange action, or live route. |

## Verified classifications

### Portable source: 32 rows

The three semantic tests construct plan data with fixed UID/GID 1000 while the
attestor correctly checks `os.geteuid()`/`os.getegid()`. Repair only their test
fixtures; never relax production semantic identity enforcement.

The two fakeroot tests write current-process staging identity but run
`chown -R 1000:1000`; production correctly rejects the contradiction. Repair
only the fakeroot harness metadata/ownership construction; do not change the
1000:1000 production boundary.

Twenty-seven sealed-UV rows call `_task3_policy()`, which reads, hashes, and
executes `/usr/bin/bwrap` before tests whose primary proof is source ordering,
policy shape, descriptor topology, or injected failure. Their test-local policy
fixture needs a reviewed controlled sandbox binding. Existing source, memfd,
and hostile-input regressions stay mandatory.

### Native capability: 24 rows

Sixteen rows intentionally prove real Bubblewrap behavior: two engine-build
tests, two generated real-spawn tests, nine CLI OS-sandbox tests, two
sealed-UV sandbox-identity mutations, and the committed sealed-UV policy
binding. Fake sandbox, skip, or direct-execution fallback cannot pass them.

Eight provisioning rows intentionally invoke `unshare --user --map-root-user`.
The hosted uid-map refusal is a native constraint, not permission to simulate
the root path or convert these tests to fakeroot.

### External authority: 6 rows

Three Phase 3B tests require the reviewed
`/home/thenam176/.hermes/crypto-research` corpus and exact hashes/counts. Three
legacy producer tests require the fixed retained
`/home/thenam176/.local/bin/uv` authority, digest/version, and legacy closure.
Neither may be fabricated, downloaded as a substitute, or treated as a normal
dependency.

## Proposed topology

The following interfaces are proposed, not current Make targets:

```text
audited portable root candidate collection
              |
  +-----------+------------------------+
  |                                    |
portable-root-remainder       immutable 62-node inventory (hash bound)
  |                                    |
exact generated-node run        collection-completeness verifier
                                       /             |              \
                              portable-source native-capability external-authority
                                       |             |              |
                                    execute       preflight/run    preflight/run
                                       \             |              /
                                  sealed root/lane evidence
                                              |
                               controller aggregation (never a false GREEN)
```

### Hosted `ci-portable` root-test routing

The witnessed run proves that the current generic portable aggregate is not a
valid T-G03C source-required route: `ci-portable-private` reaches
`test-all-portable-private`, which reaches `test-portable-embedded-proof`, then
generic root `test`. That root selection executes the locked 62 failures before
the topology target appended afterward can create exact lane receipts. It cannot
produce a topology/source green result by adding the topology after the generic
test.

T-G03C must replace only that **hosted `ci-portable` root-test component** with
a new private aggregate, proposed as
`test-all-portable-topology-private`. The canonical Make topology must be:

```text
Foundation workflow
  -> make ci-portable                         # unchanged workflow command
     -> private RUNNER_TEMP 0700 wrapper      # unchanged allocation/cleanup
        -> make ci-portable-private
           -> make prepare-root-test-install  # retained
           -> make test-all-portable-topology-private \
                   check-test-governance-topology check-critical-coverage \
                   build-dashboard \
                   audit-python-source audit-dependencies

test-all-portable-topology-private:
  audit-portable check-d0-closure check-contracts check-secrets
  test-backend test-dashboard typecheck-dashboard lint-dashboard
  ci-portable-topology

ci-portable-topology:
  verified tracked inventory install
  audited portable-root candidate collection
  portable-root-remainder lane (generated exact node-ID list executed)
  portable-source lane (exact 32 executed)
  native-capability lane (exact 24 executed or valid DEFERRED)
  external-authority lane (exact 6 executed or valid DEFERRED)
  receipt aggregation

check-test-governance-topology:
  uv run python -m scripts.check_test_governance --topology-audit \
    --report-dir "$(TEST_EVIDENCE_DIR)/test-governance-topology" \
    --topology-evidence-root "$(TEST_EVIDENCE_DIR)" \
    --inventory "tests/fixtures/t-g03a-hosted-failure-inventory.tsv" \
    --foundation-run-id "$$GITHUB_RUN_ID" \
    --foundation-head-sha "$$foundation_head" \
    --foundation-context-path "$$FOUNDATION_CONTEXT_PATH"
```

This is a Make-only orchestration contract: Foundation continues to invoke only
`make ci-portable`; it must not gain a hand-written pytest invocation, node
list, marker, skip, or xfail. `ci-portable-topology` obtains its three
capability selections from the verified installed tracked inventory and its
ordinary-root selection from the audited dynamically collected baseline, never
from a Makefile or workflow literal. It is the sole root-test route in this
source-required path.

### Amendment A1 — one sealed Foundation policy-validation date

This amendment resolves a narrow source-owned time-anchor split observed in
the hosted T-G03F symptom.  The supplied hosted excerpt masks the validator
cause, so the proposed midnight/host-clock timeline is an inference, not a
proven fact about that run.  It does **not** authorize an allowlist renewal or
edit.  A policy whose review date has already expired when the Foundation
invocation begins must continue to fail closed.

The sole time authority for an accepting portable Foundation route is a
canonical, no-clobber Foundation-context record at
`$(TEST_EVIDENCE_DIR)/capability-topology/foundation-context.json`.  At the
first executable action of the source-owned `make ci-portable` recipe, before
its private temp wrapper, topology reservation, or any governance operation,
an internal capture helper must reject a pre-existing context record or any
existing topology acceptance artifact.  It independently obtains the active
`GITHUB_RUN_ID` and checked-out `git rev-parse HEAD`, captures
`datetime.now(timezone.utc).date().isoformat()` exactly once, validates all
three values, and atomically publishes/reopens the private record before it
exports only the context path to `ci-portable-private`.  The validation date is
never a Make variable, environment value, or topology/governance CLI argument.

The record uses the exact schema `"t-g03a-foundation-context/v1"` and complete
key set `schema_version`, `foundation_run_id`, `foundation_head_sha`,
`foundation_validation_date`, and `foundation_context_sha256`.  It is strict
canonical UTF-8 with no BOM/trailing newline; its self-hash covers the same
object with `foundation_context_sha256` omitted.  Its directory and file use
the existing private-owner/no-symlink/no-replace/fsync/re-read discipline.
Record validation requires the run ID to equal the current Foundation context,
the head to equal the current checkout, and the date to satisfy the parser
below.  The helper is a private source function called only from the
`ci-portable` recipe, not a Make target or topology-script CLI action; leaves
have no reviewed-source creation mode.  This is an internal data-integrity
boundary, not a claim that a same-owner local writer cannot construct valid
bytes.
Foundation continues to invoke only `make ci-portable`; no workflow variable,
workflow clock expression, or workflow conditional is added.  Production code
uses only this UTC capture; a controlled clock parameter is a unit-test seam
inside that private function and cannot be selected through Make, environment,
or a production CLI argument.

The canonical date parser accepts only ten ASCII bytes matching
`^[0-9]{4}-[0-9]{2}-[0-9]{2}$`, for which `date.fromisoformat(value)` succeeds
and round-trips byte-for-byte through `isoformat()`.  This rejects an absent
value, whitespace, a timestamp/timezone, compact or non-ASCII spelling, and
an impossible date.  The captured value is a run context, not a policy
approval, and neither receipt-v1 nor the tracked allowlist schema changes.

`ci-portable-private`, `test-all-portable-topology-private`,
`ci-portable-topology`, `test-portable-root-remainder`,
`test-portable-source`, `test-native-capabilities`,
`test-external-authorities`, and `check-test-governance-topology` are consumers
only.  Each must require the exported context path, first reopen and verify the
pre-existing context record, and obtain the parsed date only from that record
before reservation, collection, preflight, test selection, snapshot, receipt,
failure diagnostic, governance report, or aggregate work.  They reject a
validation-date CLI argument, `--today` in topology mode, and any
`FOUNDATION_VALIDATION_DATE` environment input; none may generate, default,
substitute, reformat, or consume a caller-supplied historical date.  A direct
invocation is a developer/test tool, not a Foundation authority path: its files
have no hosted acceptance authority even when they are internally well-formed.
Only an isolated unit-test function may inject a clock, and it writes no
Foundation evidence.

Every module invocation of `scripts.t_g03_capability_topology` in the portable
route receives only `--foundation-context-path "$FOUNDATION_CONTEXT_PATH"`.
Its `reserve`, baseline collector/loader, remainder preparation/execution,
lane runner, aggregation, and failure reader validate the context and derive
the typed date from it before use.  `reserve` moves to
`"t-g03a-topology-reservation/v2"` and binds the verified
`foundation_context_sha256`; it cannot reserve an evidence namespace without
that context.  The collector writes both `foundation_validation_date` and
`foundation_context_sha256` into the canonical baseline and includes both in
the baseline self-hash.  Every later topology operation must reopen the
context and baseline and require exact equality, not merely a valid date.  A
missing, forged, stale, cross-run, cross-head, changed, or mismatched context
is fatal before an acceptance artifact.

The standard topology-governance invocation receives the same
`--foundation-context-path` and rejects `--today` in topology mode.  It opens
the context itself, derives the typed date, and passes that typed value directly
to the existing allowlist validator; the context date and baseline date must be
identical.  The receipt aggregator and failure reader repeat that integrity
verification rather than trusting a prior process.  A new Foundation run has a
different run ID; a new checkout has a different head; and a rerun in an
already used evidence root encounters the no-clobber context record.  Each is
rejected as internally inconsistent, but this is not cryptographic provenance.

The existing standard governance CLI remains the sole allowlist validator.
In topology mode its date comes only from the verified context record; topology
audit rejects a `--today` override and a missing, malformed, or baseline-
mismatched context date.  The normal strict
`check-test-skips` / `make ci` route remains unchanged: this amendment neither
changes its invocation nor gives it a topology fallback.  The topology helper
passes the same parsed immutable `date` explicitly to
`validate_allowlist_document(..., today=validation_date)` for both the
pre-execution snapshot and the required post-custody reread, and the failure
reader reconstructs with the date bound in the diagnostic/baseline.  There is
no downstream bare call that may read the wall clock.

Baseline records therefore move to
`"t-g03a-portable-root-baseline/v3"`; their exact key set is the prior v1 set
plus `foundation_validation_date` and `foundation_context_sha256`, and v1/v2
are not accepted by this route.  The failure-only record likewise moves to
`"t-g03a-portable-root-failure-diagnostic/v3"`; its exact key set is the prior
v1 set plus those two fields, both included in `diagnostic_sha256` and required
to equal the verified context/baseline values.  The policy snapshot remains
v1: it is already source-byte-bound and the enclosing baseline/diagnostic
binds which valid date and Foundation context validated those bytes.  Remainder
records bind the new baseline hash transitively.  The locked
`"t-g03a-capability-receipt/v1"` key set and bytes remain unchanged; receipt
aggregation obtains the date binding from the verified baseline rather than
adding a redundant receipt field.

### Evidence authenticity boundary

Canonical topology evidence becomes Foundation authority only when it is read
from the `test-governance-${{ github.run_id }}` artifact uploaded by the
reviewed `.github/workflows/foundation.yml` `verify` job after that job's
`make ci-portable` invocation, and the artifact/run/head bindings agree with
the GitHub run under review.  The reviewed workflow invocation and its
GitHub-associated artifact are the evidence-authenticity boundary.  A local or
direct filesystem writer running as the same owner is outside that threat
model, exactly as it is for baseline, governance, and receipt files generally.
No script-side digest, self-hash, no-clobber operation, owner/mode check, or
receipt-v1 field authenticates who wrote a same-owner file.  Those controls
establish canonical bytes and internal consistency only.

Accordingly, an internally valid recomputed Foundation-context record, baseline
or receipt produced locally must never be presented as hosted provenance, and
direct-target output is developer/test evidence only.  Production acceptance
reviews only the workflow-associated artifact for the matching run/head; it
does not accept a caller-provided path, copied artifact contents, or local
digest as a substitute.  This design adds no workflow change, OIDC identity,
signature, remote API query, network operation, or cryptographic attestation.
Any stronger provenance mechanism is separately scoped and reviewed.
This qualification does not relax Foundation behavior: in the reviewed workflow
path, a policy expired at the one internal UTC capture still fails closed and
cannot contribute an accepted topology result.

If snapshot validation fails, the topology command and its redacted
non-acceptance error report may expose only one closed class:
`FOUNDATION_CONTEXT_ABSENT`, `FOUNDATION_CONTEXT_MALFORMED`,
`FOUNDATION_CONTEXT_BINDING_MISMATCH`, `FOUNDATION_CONTEXT_REUSE_REJECTED`,
`POLICY_DATE_CONTEXT_ABSENT`, `POLICY_DATE_CONTEXT_MALFORMED`,
`POLICY_DATE_CONTEXT_MISMATCH`, `POLICY_REVIEW_DATE_EXPIRED`,
`POLICY_SCHEMA_INVALID`, `POLICY_FIELD_TYPE_INVALID`,
`POLICY_REVIEW_DATE_INVALID`, `POLICY_REASON_NORMALIZATION_INVALID`,
`POLICY_DUPLICATE_ENTRY`, `POLICY_SOURCE_DRIFT`, or
`POLICY_VALIDATION_INVALID`.  The last is the safe catch-all for a validator
failure outside the classified domain.  Console/error-artifact text may say
only `policy validation failed: <class>`; it must not include an entry index,
node ID, reason, owner, approval, target, path, raw source bytes, or exception
text.  Exception chaining may remain internal for local debugging, but no
chained detail is serialized or printed by the hosted route.  A failed
pre-snapshot, postcheck reread, date validation, or date-baseline comparison
publishes no snapshot, failure diagnostic, PASS governance record, receipt, or
aggregate.  A review date earlier than the captured date maps to
`POLICY_REVIEW_DATE_EXPIRED`, remains fatal, and is not repaired by changing
policy data.

Focused implementation tests must cover all of the following without a hosted
workflow run:

1. Freeze the source-owned date at `2026-10-31`, simulate a later wall clock,
   and prove the standard topology governance validation plus both snapshot
   reads accept the unchanged source bytes only because all receive the sealed
   date explicitly.
2. Capture `2026-11-01` against the same bytes and prove expiry fails closed
   with only `POLICY_REVIEW_DATE_EXPIRED`; no snapshot, diagnostic, receipt,
   or acceptance report is published.  Literal JSON `allowed_in_ci: true`
   remains required.
3. At every topology/governance boundary, prove a validation-date CLI argument,
   topology `--today`, and `FOUNDATION_VALIDATION_DATE` environment value are
   rejected rather than parsed.  Exercise missing, malformed, stale, cross-run,
   cross-head, reused, and baseline-mismatched context records; each fails the
   Foundation source path before it can produce an accepted hosted result.
   Prove no leaf invokes the capture helper or self-captures a replacement date.
4. Invoke the sole capture helper through the `ci-portable` first-action seam
   with a controlled test clock.  Prove it writes one canonical/self-hashed
   context, rejects an existing context/acceptance namespace, and that normal
   topology/governance children derive their date only from that record.  The
   seam itself must create no production bypass or public CLI.
5. Build a locally recomputed, canonical/self-hashed context record with a
   valid run/head/date and prove parsers can establish only byte integrity and
   field consistency—not who wrote it.  The review fixture must classify it as
   non-authoritative unless it is supplied by the matching GitHub
   `foundation.yml` workflow artifact/run/head.  A companion hosted-evidence
   fixture accepts only that workflow-associated artifact and never a local
   path, copied receipt, or matching digest.
6. Tamper the baseline or v3 failure diagnostic date/context hash and
   self-hash, then prove remainder execution, aggregation, and the diagnostic
   reader reject it.  Prove receipt-v1 canonical bytes/key validation is
   unchanged.
7. Retain hostile malformed-schema, field-type/non-boolean,
   review-date-format, non-normalized-reason, duplicate-entry, and source-byte
   drift matrices.  Assert their exposed class is one of the closed redacted
   domain and contains no allowlist detail.

This amendment is limited to portable test-governance/topology provenance and
diagnostics.  It adds no policy renewal, workflow dependency, authority
acquisition, network action, runtime/service/database change, or live-trading
behavior.

### Supplemental design — canonical package-module entrypoints

Hosted Foundation `31671498668` at `4b9fd51` retained a canonical sealed
context, baseline, collection, and generated remainder, then emitted only the
redacted `SHARED_VALIDATOR_IMPORT` / `POLICY_VALIDATION_INVALID` nonacceptance
record.  The retained source digest equals the current tracked allowlist, so
this evidence authorizes neither an allowlist change nor a policy/authority
change.  The confirmed source-owned defect is launch shape: executing either
package-importing tool as `python scripts/<tool>.py` puts `scripts/` rather
than the repository root at the direct script import boundary, while the tools
import each other as `scripts.<module>`.

The only canonical Make entrypoints for these two package-importing tools are:

```text
uv run python -m scripts.t_g03_capability_topology <unchanged arguments>
uv run python -m scripts.check_test_governance <unchanged arguments>
```

The eventual Make-only implementation must replace each of the following
current direct-file launch sites, preserving every argument token, environment
assignment, shell guard, order, exit propagation, evidence path, and custody
binding exactly:

| Make target | Tool | Required module invocations |
| --- | --- | --- |
| `check-test-skips` | `scripts.check_test_governance` | one strict governance invocation |
| `check-test-governance-topology` | `scripts.check_test_governance` | one topology-audit invocation |
| `test-portable-source` | `scripts.t_g03_capability_topology` | `reserve`, `collect-baseline`, `run-lane --lane portable-source` |
| `test-native-capabilities` | `scripts.t_g03_capability_topology` | `reserve`, `run-lane --lane native-capabilities` |
| `test-external-authorities` | `scripts.t_g03_capability_topology` | `reserve`, `run-lane --lane external-authorities` |
| `test-portable-root-remainder` | `scripts.t_g03_capability_topology` | `collect-baseline`, `prepare-remainder`, `run-remainder` |
| `ci-portable-topology` | `scripts.t_g03_capability_topology` | `reserve`, three exact `run-lane` calls, `aggregate` |

At the reviewed current source this is exactly two governance invocations and
fifteen topology invocations.  The earlier fourteen-topology count was stale:
the current `ci-portable-topology` target also launches `aggregate`, and this
amendment deliberately includes it.  Every direct Make spelling
`uv run python scripts/t_g03_capability_topology.py` or
`uv run python scripts/check_test_governance.py` is prohibited after this
repair.  This prohibition is confined to these two tools; no unrelated
`scripts/*.py` Make invocation, helper, package layout, `PYTHONPATH`,
dependency, workflow command, or source import is changed.

Module execution is an invocation normalization, not a new interface.  The
two modules retain their current parser arguments, validation order, redacted
public errors, nonzero failure behavior, evidence/custody rules, and direct
unit-level `main(argv)` behavior.  Process-level CLI tests that intend a
supported executable interface must invoke `uv run python -m
scripts.<module>` from the repository root.  The known file-execution import
failure is not a supported successful CLI contract and must not be preserved
as an acceptance route.  In particular, `-m scripts.check_test_governance
--help` must load its reciprocal import successfully and retain its normal help
exit behavior; the equivalent topology module help route must do the same.

The source contract tests for this amendment must be hermetic and cover all of
the following without a hosted workflow, native-capability extension, external
authority, engine build, network, policy edit, or evidence acceptance:

1. Parse the root Makefile and assert the exact two governance and fifteen
   topology commands above use `uv run python -m` with the stated module name,
   while preserving their target/action membership, ordering, and existing
   argument fragments.  Assert no direct-file spelling for either tool remains
   anywhere in the Makefile.  The test must fail if a future target reintroduces
   either direct executable shape.
2. Invoke both module entrypoints with `--help` from the repository root and
   assert the existing successful help/exit contract with no
   `ModuleNotFoundError` or raw reciprocal-import detail.  Retain focused
   parser tests that invoke `main(argv)` directly; they test command parsing,
   not Make routing.
3. Use the existing sealed-context fixture and a test-owned evidence root to
   prove the module-launched portable-root snapshot reaches the shared
   validator boundary.  With the deliberately absent native custody extension,
   it must stop at the already governed custody boundary before any exact
   runner starts; it must not publish `SHARED_VALIDATOR_IMPORT`, acceptance,
   receipt, deferred claim, or policy change.  Any genuine import failure after
   module launch remains redacted as the existing A2 stage/class and fails
   closed.
4. Keep the strict `check-test-skips` and all strict/release targets' policy,
   receipt, audit, and fail-closed semantics unchanged.  This amendment changes
   only their Python module launch spelling; it creates no portable fallback,
   release acceptance, or workflow-level direct command.

Foundation still invokes only `make ci-portable`; no workflow edit or new
workflow variable is authorized.  Capability receipt-v1, Foundation context,
baseline, failure/nonacceptance diagnostics, allowlist, approval date, policy
stage/class domains, live flags, and all runtime/external authorities remain
unchanged.  After a successful package-module launch, the existing A2 shared
validator remains the sole policy validator and all of its redaction,
nonacceptance, and no-acceptance-artifact rules continue to apply.

### Amendment A3.1 — hermetic GNU Make expansion proof for package entrypoints

Round 7 correctly repaired a literal and one simple-variable shape, but a
source-only hand-written Make extractor is not an authority for GNU Make
semantics.  At `08a46fb`, each of the following generic expressions can still
materialize a prohibited direct-file topology launcher while escaping the
extractor's 15+2 count:

```make
RUN = uv run python scripts/t_g03_capability_topology.py reserve
future: ; $(call RUN)
future: ; $(value RUN)
future: ; $(strip $(RUN))
future: ; $(addprefix ,$(RUN))

RUN = uv run python scripts/t_g03_capability_topology.py
RUN += reserve
future: ; $(RUN)
```

This is a contract-test defect only.  It authorizes replacing the current
hand-rolled launch extractor in
`tests/governance/test_t_g03d_hosted_disclosure.py`; it does **not** authorize
a Makefile, workflow, package, policy, topology-script, receipt, diagnostic,
runtime, dependency, service, database, engine, network, authority, or live
behavior change.

#### Canonical authority and exact observable

The contract test must use a real GNU Make evaluator, not another partial
parser, as the authority for Make expansion.  It must reject a non-GNU Make
binary before inspecting evidence.  Its observable is the complete list of
literal shell command words that GNU Make emits **after Make expansion and
before recipe execution**, paired with the traced target/recipe source span.
The test must conservatively parse the printed shell command into command
segments.  A guarded invocation is valid only when its complete literal argv
is one of the entries below; a quoted, escaped, shell-variable, substitution,
`eval`, wrapper, option-shifted, or otherwise non-literal executable/module
position is noncanonical and fails closed.

| Target recipe | Required expanded guarded argv suffixes |
| --- | --- |
| `check-test-skips` | `scripts.check_test_governance --report-dir <controlled-governance-root>` |
| `check-test-governance-topology` | `scripts.check_test_governance --topology-audit --report-dir <controlled-topology-report-root> --topology-evidence-root <controlled-evidence-root> --inventory tests/fixtures/t-g03a-hosted-failure-inventory.tsv --foundation-context-path <controlled-context-path>` |
| `test-portable-source` | `scripts.t_g03_capability_topology reserve ...`; `collect-baseline ...`; `run-lane --lane portable-source ...` |
| `test-native-capabilities` | `scripts.t_g03_capability_topology reserve ...`; `run-lane --lane native-capabilities ...` |
| `test-external-authorities` | `scripts.t_g03_capability_topology reserve ...`; `run-lane --lane external-authorities ...` |
| `test-portable-root-remainder` | `scripts.t_g03_capability_topology collect-baseline ...`; `prepare-remainder ...`; `run-remainder ...` |
| `ci-portable-topology` | `scripts.t_g03_capability_topology reserve ...`; `run-lane --lane portable-source ...`; `run-lane --lane native-capabilities ...`; `run-lane --lane external-authorities ...`; `aggregate ...` |

Here every topology suffix after its action retains exactly the current
`--evidence-root <controlled-evidence-root>` then
`--foundation-context-path <controlled-context-path>` tokens.  The command
line controls `TEST_EVIDENCE_DIR`, `FOUNDATION_CONTEXT_PATH`, `GITHUB_RUN_ID`,
`RUNNER_TEMP`, and the few required test-only custody values with fixed,
temporary, shell-safe paths.  Thus the expected expanded argv is exact, has no
ambient home, runner, clock, policy, or environment dependency, and proves
both ordering and the argument values rather than merely a module-name count.
The one existing `ci-portable` inline `python -c` Foundation-context capture is
not a package-tool launch and may remain only as its current exact literal
source/expanded command; it cannot contribute to the 15+2 inventory or become
a generic alternate launcher.

Across the complete root Makefile, the canonical set is exactly **15**
`uv run python -m scripts.t_g03_capability_topology` invocations and exactly
**2** `uv run python -m scripts.check_test_governance` invocations, with the
target/action membership and order in the table.  There may be no other
rendered or source-materialized reference to either guarded tool.  A direct
`scripts/<tool>.py` argv is always fatal, even when it is constructed through
a variable, a continuation, an inline shell clause, a `define`, a target- or
pattern-specific variable, an ordinary/conditional/simple assignment, `+=`,
or a pure GNU Make function.  Canonical count/argv drift, a duplicate site, an
omitted site, an unexpected target/prerequisite site, or an unparseable
guarded command all fail before any acceptance claim.

#### Pre-expansion conditional quarantine

One controlled GNU Make expansion cannot prove the contents of an inactive
conditional branch: GNU Make removes that branch before its target database
and `--trace` observable exist.  Enumerating all valuations is not a finite or
safe substitute for ordinary/dynamic variables, environment-derived `ifdef`,
or macros that construct the guarded words in pieces.  Therefore this
contract adopts the narrower source-compatible policy that the root Makefile
has **no active GNU Make conditional directives at all** while the guarded
launch contract is in force.  The reviewed current root Makefile has none;
this is a test-only future-regression guard, not authorization to change
Foundation behavior.

Before creating the projection or invoking GNU Make, a lexical directive
scanner over the original root Makefile must reject every real top-level
`ifeq`, `ifneq`, `ifdef`, `ifndef`, `else` (including `else ifeq`, `else
ifneq`, `else ifdef`, and `else ifndef`), and `endif` directive.  A directive
cannot be waived because its currently selected branch has no observed guarded
argv.

The scanner must first construct logical lines for the **whole original
file**, before looking for any conditional word.  It must use the GNU
Make-compatible trailing-backslash parity rule for every eligible physical
line: an odd final run of unescaped backslashes joins the next physical line;
an even run does not.  It must retain the complete physical-span map and the
first-line prefix for each resulting logical line.  Only then, in this order,
it classifies the logical line as a full-line comment, a tab-prefixed recipe,
a balanced `define`/`endef` value-body line, an ordinary variable assignment,
a rule/target declaration, or an eligible root directive.  Assignment and
rule classification take precedence over conditional matching.  Consequently:

```make
SAFE_DISPLAY = first \
               ifeq literal display data
```

is one assignment value and is not an `ifeq` directive.  Likewise, a comment,
recipe display, or literal `ifeq` text inside a `define` body is not a root
directive.  A `define` body becomes executable Make syntax only through an
evaluator route, which the preceding evaluator quarantine already rejects.

For an eligible root logical line, the scanner recognizes only the exact GNU
directive words above with a lexical word boundary after the word, then parses
the structural sequence (`else` optionally followed by one exact `if*` word,
or standalone `endif`) and tracks a nested conditional stack.  It must scan
the complete file rather than stop at the first detection so structural
errors remain observable.  Any unmatched `else`/`endif`, duplicate `else`,
missing closing `endif`, unterminated continuation, malformed `else if*`,
unexpected trailing directive material, nonstandard conditional spelling that
is ambiguous with a directive, or an inability to distinguish an eligible
line from assignment/rule syntax is a pre-launch fail-closed error.  No GNU
Make database, projection, target enumeration, or subprocess may be created
after any such error.

This is deliberately a structural conditional quarantine rather than another
attempt to infer a generic macro's meaning from its name.  It closes a future
`ifeq`/`ifneq`/`ifdef`/`ifndef` branch containing a direct topology/governance
file launch, a canonical-looking extra module launch, or a `define`/`call`/
`value`/assignment fragment that only materializes a guarded launcher when
the branch becomes active.  Because current source has no conditional
directives, accepting an unrelated future conditional would be less defensible
than rejecting it before the expansion proof.

The scanner must not falsely treat a full-line Make comment, a tab-prefixed
recipe/shell line, an ordinary variable value, or literal display text inside
a `define` body as a Make conditional directive.  It must recognize and skip
comments/recipe bodies and balanced `define`/`endef` value bodies before
directive classification; a real conditional directive outside those regions
is fatal irrespective of comments that follow it.

The hostile test matrix must use temporary Makefile variants and prove, each
without creating a projection or GNU Make subprocess, the pre-launch failure
of: every inactive `ifeq`, `ifneq`, `ifdef`, and `ifndef` guarded-route
branch; standalone `else` and standalone `endif`; each `else ifeq`, `else
ifneq`, `else ifdef`, and `else ifndef` spelling; valid nested conditionals;
unmatched `else`/`endif`, duplicate `else`, missing `endif`, malformed
directive headers, and an unterminated or backslash-continued conditional
directive.  It must include a guarded macro/`define` defined in an inactive
branch and a condition nested beneath an inactive parent.  The complementary
positive matrix must assert the current Makefile's zero-directive result and
accept a comment display, a tab recipe display, a literal `ifeq` inside a
balanced `define` body, and continued ordinary assignment/display/comment
values whose later physical line begins `ifeq`; none may be misclassified.

#### Hermetic expansion harness and safety boundary

The new test helper must make a test-owned copy/projection of the root
`Makefile`, retain a source-byte digest and a recorded, allowlisted transform
map, then invoke GNU Make only with an argument-vector subprocess equivalent
to:

```text
env -i <controlled make variables> \
  make --no-builtin-rules --no-builtin-variables --always-make \
  --dry-run --trace --file <test-owned-projection> <one-concrete-target>
```

It must enumerate the concrete root targets from the GNU Make database of the
same projected bytes, dry-run every one in an isolated fresh process, and use
`--trace` plus the source map to associate each rendered guarded command with
its actual target and logical recipe span.  This covers command sites reached
through prerequisites as well as an otherwise orphaned future target.  The
test deduplicates only the same traced source recipe span observed from more
than one requested target; it must never deduplicate distinct expansion
contexts or argv.  Pattern/generated guarded recipes and target-specific
variable contexts must be included where GNU Make can instantiate them;
ambiguous/unenumerable generated target machinery is a fail-closed error.

Before GNU Make is launched, the helper must reject parse-time or evaluator
routes that could execute, write, import, or synthesize an uninspectable
recipe: `include`/`-include`/`sinclude`, `$(eval ...)`, `$(shell ...)`,
`$(file ...)`, `$(guile ...)`, `$(load ...)`, recipe `+` execution escapes,
shell-level `MAKE` indirection, and any equivalent spelling.  The sole
current exception is the exact checked-in
`RUNTIME_RELEASE_LOCK_SHA256 := $(shell sha256sum uv.lock | cut -d' ' -f1)`
assignment: the harness supplies that variable as a fixed command-line value
so GNU Make need not evaluate its body, and asserts the ignored source span is
byte-for-byte that one declaration.  Any second, altered, or guarded-reachable
parse-time evaluator is fatal.  `$(call ...)`, `$(value ...)`, `$(strip ...)`,
`$(addprefix ...)`, continuations, `define`, and ordinary Make assignment
forms including `+=` are not hand-interpreted; genuine GNU Make expands them
and the resulting argv is checked.

GNU Make normally executes a recursive `$(MAKE)` recipe even under
`--dry-run`.  The projection may replace only a syntactically verified,
literal root recursive-Make word with a non-recursive no-op word, preserving
the rest of that same logical shell command for inspection.  It must record
each replacement, and accept only the checked-in literal recursive forms:
declared literal root target names, or the existing `-C
native/package6_custodian` form with its current literal `build` target and
the exact existing `BUILD_DIR=$$build_dir` assignment where present.  Any new
option, target, variable expansion, altered subdirectory, or shell-level
`$$MAKE` form is rejected.  The helper must prove no replacement span contains
or removes a guarded tool word.  No other source rewriting, included file,
environment inheritance, shell command, recipe, prerequisite, or recursive
Make process is allowed.  The projection uses a test-owned temporary current
directory, fixed `PATH`, empty inherited environment, and a shell tripwire
that fails the test if GNU Make attempts to execute a recipe.  Therefore the
proof performs no build, installation, network input, external-authority
acquisition, engine action, service/database mutation, runtime release, or
live-trading operation; it is expansion evidence only.

The output reader must accept normal path-valued Make interpolation only after
an already literal canonical module head and must reject a guarded identifier
created through shell syntax or opaque tokenization.  It must also prove that
the current target/prerequisite graph and all unrelated Make variables retain
their current semantics: the test harness is an observer, not a new Make
entrypoint or a replacement for Foundation orchestration.

#### Required hostile and positive proof matrix

The focused test suite must pass the unmodified current Makefile and then use
temporary Makefile variants (never the tracked Makefile) to prove all of the
following fail before a recipe or external action occurs:

1. direct-file topology and governance forms on one line, an inline shell
   clause, and a backslash continuation;
2. the five concrete bypasses above: `call`, `value`, nested `strip`,
   `addprefix`, and `RUN +=` composition, using a generic variable name;
3. a `define`/function expansion, target-specific or prerequisite-provided
   variable, and a new otherwise-orphaned target that materializes a guarded
   launcher;
4. a second/modified `shell`, `eval`, `file`, include, recursive-Make escape,
   shell substitution, or opaque evaluator route; and
5. an extra, missing, reordered, option-shifted, direct-file, or dynamically
   selected module site relative to the exact 15+2 table.

At least one benign non-launcher macro, including the existing
`$(SAFE_DISPLAY)` positive case, must remain accepted.  Tests retain the
module `--help`, sealed-context custody-boundary, direct `main(argv)`, and
strict/release non-regression checks already required by A3.  The reviewed
implementation must remove superseded hand-rolled parser authority rather
than retaining it as an alternate acceptance path.  `check-test-skips`,
`make ci`, `make audit`, `make audit-release`, all strict/release policy and
receipt semantics, and the single workflow `make ci-portable` invocation stay
behaviorally unchanged.

### Amendment A2 — policy-validation non-acceptance stage evidence

Hosted Foundation `31668464716` at `9b5ae3f` proved a valid sealed context,
baseline, and generated remainder, then reported only the existing public
class `POLICY_VALIDATION_INVALID`.  Replaying the exact source bytes at the
sealed date validates the same policy locally.  That evidence does not prove a
policy defect, a clock defect, or which protected snapshot branch raised.  No
allowlist renewal, edit, broadening, workflow change, receipt-v1 change, or
runtime-authority action is authorized by this amendment.

The portable-root executor must therefore add one diagnostic-only,
non-acceptance record at:

```text
$(TEST_EVIDENCE_DIR)/capability-topology/policy-validation-nonacceptance.json
```

It is written only by the executor that has already reopened the verified
Foundation context, reservation, installed inventory, baseline, and generated
remainder for the active run/head.  Pure snapshot helpers, failure-diagnostic
readers, receipt readers, aggregate/reconciliation paths, and direct developer
invocations are readers only and must never create this record.  A direct
filesystem record is internally checkable evidence only; it becomes hosted
review evidence solely at the existing matching `foundation.yml` Foundation
artifact/run/head boundary described below.

The stage is a structural control-flow fact, never a guess from an exception
message, class text, stack trace, or policy content.  The closed v1 stage
domain is exactly:

```text
SOURCE_ACQUISITION_HEAD_BINDING
SHARED_VALIDATOR_IMPORT
STRICT_JSON_PARSE
SHARED_ALLOWLIST_VALIDATION
ROOT_PROJECTION_REASON_NORMALIZATION
POST_CUSTODY_REREAD_COMPARISON
```

`SOURCE_ACQUISITION_HEAD_BINDING` covers opening the fixed allowlist source,
obtaining its exact `git show <Foundation-head>` bytes, strict UTF-8/BOM
checks, and byte equality.  `SHARED_VALIDATOR_IMPORT` covers only loading the
existing standard governance validator.  `STRICT_JSON_PARSE` covers strict
duplicate-key-rejecting JSON parsing.  `SHARED_ALLOWLIST_VALIDATION` covers
only `validate_allowlist_document(..., today=<sealed-date>)` and retains its
existing fail-closed policy semantics.  `ROOT_PROJECTION_REASON_NORMALIZATION`
covers root filtering, required-key access, exact literal-boolean handling,
v1 reason normalization, commitments, entry hashes, sorting, and final
snapshot construction.  `POST_CUSTODY_REREAD_COMPARISON` covers the second
complete acquisition/import/parse/validation/projection after successful
retained-custody exit and the equality comparison with the pre-execution
snapshot.  A second-read failure is this final stage even when its underlying
operation would have been one of the earlier stages.  No unknown, default,
combined, or caller-supplied stage is valid.

The existing closed public `policy_validation_class` domain and redacted
console spelling remain unchanged, but this record has the following exhaustive
stage/class compatibility matrix. A listed `POLICY_VALIDATION_INVALID` is the
intentional generic class for an unexpected non-content error at that named
structural boundary; it is not a fallback to message matching. All other
public context/date classes, and every pair not listed here, are invalid in
this record and readers reject them.

| Structural stage | Exactly permitted public class or classes |
| --- | --- |
| `SOURCE_ACQUISITION_HEAD_BINDING` | `POLICY_SOURCE_DRIFT`, `POLICY_VALIDATION_INVALID` |
| `SHARED_VALIDATOR_IMPORT` | `POLICY_VALIDATION_INVALID` |
| `STRICT_JSON_PARSE` | `POLICY_SCHEMA_INVALID`, `POLICY_VALIDATION_INVALID` |
| `SHARED_ALLOWLIST_VALIDATION` | `POLICY_SCHEMA_INVALID`, `POLICY_FIELD_TYPE_INVALID`, `POLICY_REVIEW_DATE_INVALID`, `POLICY_REVIEW_DATE_EXPIRED`, `POLICY_REASON_NORMALIZATION_INVALID`, `POLICY_DUPLICATE_ENTRY`, `POLICY_VALIDATION_INVALID` |
| `ROOT_PROJECTION_REASON_NORMALIZATION` | `POLICY_FIELD_TYPE_INVALID`, `POLICY_REASON_NORMALIZATION_INVALID`, `POLICY_VALIDATION_INVALID` |
| `POST_CUSTODY_REREAD_COMPARISON` | `POLICY_SOURCE_DRIFT`, `POLICY_SCHEMA_INVALID`, `POLICY_FIELD_TYPE_INVALID`, `POLICY_REVIEW_DATE_INVALID`, `POLICY_REVIEW_DATE_EXPIRED`, `POLICY_REASON_NORMALIZATION_INVALID`, `POLICY_DUPLICATE_ENTRY`, `POLICY_VALIDATION_INVALID` |

The writer must construct a typed internal stage result before any broad
exception boundary: source and parser wrappers select their known class at
their own operation; the shared validator exposes a typed redacted validation
result/code at the same check that currently raises (without changing its
allowlist acceptance semantics); and the projection wrapper selects its own
code. The post-custody wrapper propagates the already typed second-read result
under the post-custody stage, or selects `POLICY_SOURCE_DRIFT` for a successful
second snapshot that does not equal the first. It must not select either field
from exception-message fragments, exception text, or an external mapper. The
legacy broad mapper may remain only for non-recorded caller compatibility and
must not feed this artifact. Neither the record nor the console may contain an
exception type or text, traceback, chained cause, entry index, node ID, reason,
owner, approval, target, service, filesystem path, raw policy bytes, or an
inferred cause.

The exact record schema is
`"t-g03a-policy-validation-nonacceptance/v1"` with exactly these top-level
keys:

```text
schema_version
diagnostic_only
foundation_run_id
foundation_head_sha
foundation_validation_date
foundation_context_sha256
inventory_sha256
baseline_sha256
baseline_candidate_ids_sha256
baseline_node_list_sha256
remainder_sha256
remainder_candidate_ids_sha256
remainder_node_list_sha256
custody_policy_sha256
custody_status
policy_validation_stage
policy_validation_class
policy_source_hash_status
policy_source_sha256
nonacceptance_sha256
```

`diagnostic_only` is the literal JSON boolean `true`.  All other fields are
strings.  Run/head/date/hash spellings use the already defined canonical
validators.  `inventory_sha256` equals the locked inventory hash;
`baseline_sha256` and `remainder_sha256` equal the verified self-hashes of
those records; candidate/remainder ID hashes are hashes of their canonical
sorted arrays; list hashes equal the verified canonical node-list-file
digests; and `custody_policy_sha256` hashes the already verified baseline
custody policy using the existing canonical JSON rule.  Thus the record binds
the exact Foundation context/date, inventory, baseline, remainder, and custody
policy without copying their node IDs, policy content, paths, commands, or raw
test report.

`custody_status` has the closed domain
`PRE_EXECUTION_VALIDATED` and `POST_CUSTODY_POSTCHECK_PASS`.  An error in any
of the first five stages may be published only after the baseline custody
policy has been validated but before an exact runner is entered, and must use
`PRE_EXECUTION_VALIDATED`; it must not claim a retained-custody postcheck.
The final stage may be published only after the retained custody context has
exited successfully and its postcheck has passed, and must use
`POST_CUSTODY_POSTCHECK_PASS`.  If the applicable context, reservation,
inventory, baseline, remainder, custody-policy validation, or required
postcheck is missing, malformed, stale, foreign, or fails, no non-acceptance
record may be emitted; that earlier condition itself fails closed.

`policy_source_hash_status` has the exact closed domain
`UNAVAILABLE`, `CURRENT_STAGE_BYTES`, and `PRE_EXECUTION_SNAPSHOT`.
`UNAVAILABLE` requires `policy_source_sha256` to be the empty string and is
permitted only for `SOURCE_ACQUISITION_HEAD_BINDING`, when no source bytes were
safely acquired and head-bound.  `CURRENT_STAGE_BYTES` requires a lowercase
SHA-256 digest of the exact successfully source-acquired/head-bound bytes and
is required for the import, parse, validator, and projection stages; it is a
commitment only, never raw policy content.  `PRE_EXECUTION_SNAPSHOT` requires
the verified digest of the first successfully validated source bytes and is
required for `POST_CUSTODY_REREAD_COMPARISON`; it makes no claim that second
read bytes were safely acquired.  No other blank, fallback, synthesized, or
late-recomputed source hash is allowed.

The diagnostic-only reader independently reacquires only the tracked,
Foundation-head-bound allowlist bytes using the same safe source-acquisition
operation; it must not parse or validate the document merely to read this
artifact. For `CURRENT_STAGE_BYTES` and `PRE_EXECUTION_SNAPSHOT`, it requires
`sha256(reacquired_bytes) == policy_source_sha256` before considering any other
record binding. A reacquisition failure or digest mismatch rejects the record
without exposing the failure detail. For `UNAVAILABLE`, the reader requires
the source-acquisition stage and the literal empty digest, performs no
substitute digest comparison, and still independently verifies every
non-source binding. Thus the reader never turns an inability to reacquire a
source into a claimed source digest, and it never reparses a document whose
recorded failure was strict parsing, shared validation, or projection.

`nonacceptance_sha256` is SHA-256 of the canonical UTF-8 payload with that
field omitted.  Canonical bytes are strict UTF-8 without BOM or trailing
newline, Unicode-code-point sorted keys, `ensure_ascii=false`, and separators
`(',', ':')`.  Writer and reader compute and verify the self-hash
independently.  Publication reserves the destination as absent, writes and
fsyncs a private staging file, atomically installs it with a no-replace
operation, fsyncs the directory, then rereads/parses/self-hash-verifies exact
bytes before raising the original redacted failure.  A pre-existing target,
staging collision, publication failure, or failed reread is fatal.  Cleanup
may remove only the writer-owned uninstalled staging file; it must never
replace, truncate, or unlink a final destination whose ownership or contents
can no longer be proven.  A malformed or post-write-drifted final destination
therefore remains a blocking artifact, not an acceptance fallback.

Before the first snapshot attempt, the executor must reserve the
non-acceptance destination and verify that no failure diagnostic, portable-root
PASS governance record, or exact per-code lane receipt (`<code>.json`) exists.
`aggregate` intentionally writes no aggregate artifact; its only acceptance
inputs are that portable-root PASS governance record and the per-code receipts,
so no invented “aggregation result” path is reserved. The aggregate action,
portable-root reconciliation, and topology-governance audit must instead
reject the concrete non-acceptance artifact's presence before they inspect
those inputs. On any
pre-execution stage non-acceptance it publishes this record, starts no runner,
cleans only its provisional staging file, and exits nonzero.  On a post-custody
stage non-acceptance it may publish only after the stated postcheck; it must
not publish a failure diagnostic, PASS governance record, receipt, or
aggregate.  On policy success no non-acceptance record is published.  A
nonacceptance record and a failure diagnostic are mutually exclusive.  All
attempts to publish a lane/receipt, root PASS governance record, reconciliation
or aggregate while either artifact exists must fail closed; a non-acceptance
record is never a receipt, PASS, deferred receipt, reconciliation input, or
substitute for a skipped runtime proof.

The new diagnostic-only reader must reject a missing, noncanonical,
malformed, self-hash-invalid, stale, foreign-run/head, context/date/inventory/
baseline/remainder/custody-binding-drifted, source-hash-status-invalid, or
stage/class-invalid record.  It may return only the verified redacted payload
to a separate failure-review flow and has no API that writes policy, changes
approval, emits a receipt, or changes a lane outcome.  The ordinary receipt
aggregator, portable-root reconciliation, topology-governance audit, lane
publication, and failure-diagnostic reader must reject the *presence* of the
record before evaluating acceptance evidence, including if it is malformed or
foreign.  A subsequent exact run must use a new evidence root/run context;
the existing no-clobber context rule rejects reuse.

The shared exact-execution helper is also used by the inventory lane runners;
its API must carry an explicit immutable `portable_root_remainder` mode (or
equivalent closed selector) that is set only by
`execute_portable_root_remainder`. It is a hard precondition of this writer,
not an inferred report filename or node set. A `run-lane` call, including one
whose injected snapshot operation fails at any listed stage, must fail closed
without creating `policy-validation-nonacceptance.json`.

Focused implementation tests must inject one failure at every listed stage and
prove all of the following: the emitted class/stage is exact and structural;
the artifact and console contain no raw exception/policy content; the schema,
canonical bytes, self-hash, all declared bindings, custody status, and source
hash status are exact; pre-execution stages never run pytest; the post-custody
stage requires a real successful retained-custody exit; and policy success
emits no artifact.  They must prove failed publication/staging collisions,
post-write tamper, missing/malformed/stale/foreign records, cross-run/head
reuse, every illegal source-hash convention, a wrong-but-hex source digest,
source drift during reader reacquisition, a post-custody pre-execution-snapshot
digest, every matrix-invalid stage/class pair, and coexistence with a failure
diagnostic, PASS governance record, receipt, aggregate, or deferred claim all
fail closed. They must also inject a snapshot-stage error through a
non-remainder `run-lane` invocation and prove no non-acceptance record is
written. Existing validator dates, allowlist rules, workflow invocation,
strict/release routes, and capability-receipt/v1 exact bytes must be shown
unchanged.

### Portable-root remainder lane

The locked 62 explain the historical hosted failures; they are not the whole
portable root-test universe. T-G03C must therefore add the
`test-portable-root-remainder` lane/target before the three inventory lanes.
It dynamically accounts for ordinary root tests rather than freezing the
historical `5451` passing count or a second static node list.

First, a single audited canonical collector creates a private, no-clobber
baseline candidate record and a sorted UTF-8 node-ID file under
`$(TEST_EVIDENCE_DIR)/capability-topology/`. It uses the same root location,
`--portable-embedded-proof` policy, marker expression
`not runtime_postgres and not host_coupled`, and
`-p scripts.test_governance_pytest` governance plugin as the previous hosted
portable root policy, including the same single native-custody-extension
precondition and exported identity variables, but operates in explicit
collection-only mode. Its canonical tool-owned invocation is therefore
equivalent to `uv run pytest -q --collect-only --portable-embedded-proof -m
"not runtime_postgres and not host_coupled" -p scripts.test_governance_pytest
tests`. That collection-only plugin mode may report candidates but must not
manufacture execution outcomes or a successful test result. The collector is
the only portable source-required operation permitted to use the bare root
selector `tests`; it must be invoked through the canonical topology tool, not a
Makefile/workflow pytest literal.

The baseline record binds schema/version, current Foundation run/head, the
sealed canonical Foundation validation date, tracked inventory hash, exact
collector policy, sorted candidate node IDs, and a digest of the node-ID file.
The collector fails closed if collection emits an unknown
or malformed selector result, a duplicate ID, a candidate outside the root test
tree, an omitted/changed policy field, an inventory node absent from the
baseline, or drift between the plugin's observed candidates and the sealed
file. Its tests must prove that a newly added ordinary root test becomes a
baseline candidate on the next run.

The remainder is exactly `baseline_candidate_ids - locked_inventory_ids`. The
executor must reopen and verify the generated node-ID file and baseline digest,
then invoke pytest only with that explicit complete node-ID list, the same
portable marker/policy, native-custody-extension identity, and governance
plugin. It must not execute `pytest ... tests`, a directory selector, `-k`,
broad marker deselection, skip, or xfail.
It writes one no-clobber `portable-root-remainder.governance.json` record. Its
complete collected and passed sets must each equal the generated remainder set;
any skipped, deselected, xfailed, xpassed, failed, not-run, duplicate, omitted,
or additional node fails the lane. An empty remainder still needs a sealed
empty record and may not bypass collection or aggregation.

#### Failure-only portable-root diagnostic

The successful governance record deliberately cannot preserve a failed run's
provisional execution evidence. To make a failed exact run diagnosable without
weakening governance, T-G03D must add exactly one separate, failure-only
record at
`$(TEST_EVIDENCE_DIR)/capability-topology/portable-root-remainder.failure-diagnostic.json`.
It is not a lane receipt, does not replace the governance record, and is never
written on a passing remainder lane.

The runner may publish that record only after it has completed every existing
no-clobber, baseline, generated-remainder, candidate/list-hash, and raw
per-node completeness validation, and after the retained native-custody
**postcheck** has passed. The output name is reserved as absent before the
run; publication writes a private complete file, fsyncs it, and atomically
installs it with a no-replace operation. A pre-existing destination, failed
atomic publication, or post-write byte/hash reread is fatal. If custody
postcheck, selector/raw-report validation, or complete one-node-per-selected-ID
evidence is unavailable, no diagnostic may be published: that condition fails
closed rather than emitting an incomplete diagnostic. Only the remaining case
of a completely evidenced exact execution with a non-pass proof may publish
the diagnostic, and must then exit nonzero. It must not also publish a passing
remainder governance record or a `PASS` receipt.

The record has the one versioned schema
`"t-g03a-portable-root-failure-diagnostic/v3"`. Its complete top-level key set
is exactly:

```text
schema_version
diagnostic_only
foundation_run_id
foundation_head_sha
foundation_validation_date
foundation_context_sha256
inventory_sha256
baseline_candidate_ids_sha256
baseline_node_list_sha256
remainder_candidate_ids_sha256
remainder_node_list_sha256
custody_policy_sha256
custody_postcheck_status
pytest_exit_status
policy_snapshot
policy_snapshot_sha256
observations
diagnostic_sha256
```

`diagnostic_only` is the literal JSON boolean `true` and
`custody_postcheck_status` is the literal string `PASS`. Apart from
`diagnostic_only` and the literal `allowed_in_ci` boolean within each snapshot
entry, booleans are forbidden; all other scalars are strings. Run IDs and
nonzero pytest exit status are canonical decimal strings without leading zeros.
The head is the exact lowercase 40-character Git SHA; the listed top-level
`*_sha256` fields, including `diagnostic_sha256`, are lowercase 64-character
SHA-256 hex strings. No timestamp, filesystem path, command line, environment
value, raw pytest output, secret, corpus data, or authority material is
permitted.

`policy_snapshot` is the complete, canonical, privacy-safe projection of the
validated existing root skip policy used for this execution. It is constructed
before the test command from the exact strict-UTF-8 bytes of the tracked
`tests/skip-allowlist.yaml` at the checked-out Foundation head, retained only
in private memory until diagnostic publication, and re-read unchanged after
custody postcheck. It has exactly these keys:

```text
snapshot_schema_version
allowlist_schema_version
allowlist_source_sha256
policy_entry_schema_version
entries
```

`snapshot_schema_version` is exactly
`"t-g03a-portable-root-policy-snapshot/v1"`; `allowlist_schema_version` is
the canonical decimal string `"1"`; and `policy_entry_schema_version` is
exactly `"t-g03a-skip-policy-entry/v1"`.
`allowlist_source_sha256` is SHA-256 of the exact strict-UTF-8 source bytes,
without a BOM, before parsing. The source document must first pass the current
allowlist-v1 schema, owner/security derivation, approval, review-date against
the sealed `foundation_validation_date`,
allowed-in-CI, and normalized-reason validation. Any source-byte drift, parse
failure, duplicate `(component, test_node_id)`, invalid entry, or changed
postcheck reread prevents diagnostic publication.

The snapshot's `entries` are the complete validated existing `component=root`
allowlist entries, not merely entries that match an observed node. They are
sorted by the bytewise `(component, test_node_id)` tuple, duplicate-free, and
each has exactly these keys:

```text
component
test_node_id
outcome
allowed_in_ci
reason_class
normalized_reason_commitment_sha256
policy_entry_sha256
```

`outcome` is the existing policy's `skipped` or `deselected` value;
`allowed_in_ci` is its literal JSON boolean; and `reason_class` is exactly
`POLICY_SKIP_REASON` for `skipped` or `POLICY_DESELECT_REASON` for
`deselected`. No raw policy reason, owner, approval text, target, service,
filesystem path, or other policy content is copied into the snapshot.

For reproducible entry provenance, each `policy_entry_sha256` is SHA-256 of
the canonical UTF-8 bytes of this complete policy-entry payload, with keys
sorted as defined below:

```text
approval_record_type
allowed_in_ci
component
outcome
owner
reason
reason_category
required_binary_or_service
review_by
security_critical
target_phase
test_node_id
```

The payload copies every validated source field exactly except that `reason`
is first normalized by the fixed v1 reason normalizer: split the decoded
Unicode scalar sequence on Unicode 15.1 `White_Space` runs, discard empty
leading/trailing runs, and join the remaining runs with one ASCII `U+0020`
space. The source validator must already require byte-for-byte equality with
that result. There are no defaults, inferred fields, YAML formatting
dependencies, or runtime-message substitutions. Every payload is serialized
as strict UTF-8 without BOM/trailing newline, sorted Unicode-code-point keys,
`ensure_ascii=false`, and separators `(',', ':')`. The raw payload is hashed
but is never retained in the diagnostic. Its
`normalized_reason_commitment_sha256` is SHA-256 of the same canonical-byte
rule applied to an object whose complete key set is `schema_version` and
`normalized_reason`, whose first value is exactly
`"t-g03a-policy-reason-commitment/v1"` and whose second value is exactly the
fixed-normalizer result.

`policy_snapshot_sha256` is SHA-256 of the canonical bytes of the complete
`policy_snapshot` object. A writer and reader must compute it independently;
it is both a required diagnostic binding and the provenance root for every
per-node policy link. This snapshot is a record of the policy that actually
governed the failing execution, not a newly proposed policy and not an
authority to approve an outcome. `allowlist_source_sha256`, every snapshot
`normalized_reason_commitment_sha256`, and every `policy_entry_sha256` are
lowercase 64-character SHA-256 hex strings; any other type, spelling, order,
extra field, omitted root entry, or noncanonical snapshot byte sequence is
invalid.

`observations` is a non-empty array sorted by bytewise `test_node_id`; each
entry has exactly these keys:

```text
test_node_id
component
outcome
phase
xfail_state
reason_class
reason_provenance
normalized_reason_commitment_sha256
policy_match_result
existing_policy_entry_sha256
```

It contains every selected generated remainder ID exactly once, no more and no
less. `component` is the literal ASCII string `root`. The following table is
the complete closed v1 domain; any outcome, phase, xfail representation,
reason class, provenance, or combination not listed is rejected before a
diagnostic can be created or read.

| `outcome` | Permitted `phase` | Required `xfail_state` | Required `reason_class` | Required `reason_provenance` |
| --- | --- | --- | --- | --- |
| `passed` | `call` | `NOT_WAS_XFAIL` | `NONE` | `NONE` |
| `skipped` | `setup`, `call`, or `teardown` | `NOT_WAS_XFAIL` | `PYTEST_SKIP_REASON` | `PYTEST_REPORT` |
| `xfailed` | `setup`, `call`, or `teardown` | `WAS_XFAIL` | `PYTEST_XFAIL_MARKER` | `PYTEST_WASXFAIL` |
| `xpassed` | `call` | `WAS_XFAIL` | `PYTEST_XPASS_MARKER` | `PYTEST_WASXFAIL` |
| `deselected` | `collection` | `NOT_WAS_XFAIL` | `MARKER_DESELECT_REASON` | `PYTEST_DESELECT_HOOK` |
| `failed` | `setup`, `call`, or `teardown` | `NOT_WAS_XFAIL` | `PYTEST_FAILURE_REASON` | `PYTEST_REPORT` |
| `failed` | `collection` | `NOT_WAS_XFAIL` | `GOVERNANCE_COLLECTION_FAILURE` | `GOVERNANCE_COLLECTION_HOOK` |
| `error` | `setup` or `teardown` | `NOT_WAS_XFAIL` | `PYTEST_ERROR_REASON` | `PYTEST_REPORT` |
| `error` | `collection` | `NOT_WAS_XFAIL` | `PYTEST_COLLECTION_ERROR` | `PYTEST_COLLECTOR` |
| `not_run` | `session` | `NOT_WAS_XFAIL` | `MISSING_FINAL_REPORT` | `GOVERNANCE_SESSION` |

The writer derives that table entry solely from the raw pytest/governance
event type and final outcome, never by classifying arbitrary message text.
It normalizes the event reason by the same fixed v1 Unicode-15.1 whitespace
rule and writes only the domain-separated
`normalized_reason_commitment_sha256` defined for the snapshot. It writes the
empty string for that field only for the `passed`/`NONE` row. Before hashing, a
redaction gate rejects a normalized reason containing `/`, `\\`, a Unicode
control code point, a substring matching the ASCII regex
`(?i)[a-z][a-z0-9+.-]{1,31}://`, a whole-word match for `token`, `secret`,
`password`, `authorization`, or `bearer` under ASCII case-folding, or a token
matching `[A-Za-z0-9+/_=-]{20,}`. A rejected reason is a
diagnostic-publication failure, not an `UNKNOWN` class, so diagnostic bytes
contain no content paths or secrets beyond the test node IDs.

`policy_match_result` also has a closed v1 domain. It is `NOT_APPLICABLE` with
an empty `existing_policy_entry_sha256` for every outcome other than `skipped`
or `deselected`. For a skip-like outcome, the writer looks up exactly one
snapshot entry by `(component, test_node_id)` and applies this ordered rule:

1. No entry: `NO_POLICY_ENTRY` and an empty existing-entry hash.
2. Different outcome: `OUTCOME_MISMATCH` and that entry's
   `policy_entry_sha256`.
3. Same outcome but different normalized-reason commitment:
   `REASON_MISMATCH` and that entry's `policy_entry_sha256`.
4. Same outcome/reason but `allowed_in_ci=false`: `CI_DISALLOWED` and that
   entry's `policy_entry_sha256`.
5. Otherwise: `EXACT_POLICY_MATCH` and that entry's `policy_entry_sha256`.

Thus every observation has an exact, mechanically reproducible policy result;
there is no category, count, common-text, or newly inferred approval path.
`existing_policy_entry_sha256` is either the lowercase 64-character hash
required by the rule above or the empty string exactly where required. A
matching snapshot entry remains informational provenance only: no non-pass
outcome becomes an accepted remainder result, a `PASS` record, or a receipt.

The exact diagnostic bytes are strict UTF-8 without a BOM or trailing newline,
with every object key sorted by Unicode code point, `ensure_ascii=false`, and
separators exactly `(',', ':')`. Strings must be valid UTF-8 and all IDs and
enums are ASCII. JSON booleans are permitted only in the two positions defined
above; JSON numbers, `null`, alternate escapes, duplicate keys, insignificant
whitespace, or any byte sequence that does not reserialize to the same bytes
are rejected. The payload is the same object with
`diagnostic_sha256` omitted; SHA-256 of those canonical UTF-8 payload bytes
must equal `diagnostic_sha256`, and every diagnostic reader must verify that
self-hash before using any observation.

The diagnostic is uploaded only as part of the existing Foundation artifact
`test-governance-${{ github.run_id }}` from
`/tmp/trading-agent-test-evidence`, with the locked 14-day retention and an
always-on failed-job upload path. It has no other publication destination.
Topology aggregation and source-required green evaluation reject a present
diagnostic before receipt/remainder reconciliation; they never consume it as
`PASS` governance evidence, a receipt, a deferred mapping, or a substitute for
the normal exact record. Empty, malformed, stale-run/head/inventory/baseline/
remainder/custody bindings, duplicate, missing, foreign, or tampered
diagnostics fail closed. The only permitted consumer is a separate
failure-review reader that reports its verified node IDs for remediation; it
cannot alter policy or emit a receipt.

Before any allowlist or other skip-policy change after such a failure, the next
exact hosted or reproducible topology run must first verify and consume this
diagnostic. Its failure-review reader must: verify the diagnostic self-hash and
all run/head/inventory/baseline/remainder/custody bindings; obtain the exact
historical allowlist source at the diagnostic's bound Foundation head; require
its raw-byte hash, parsed validation result, and reconstructed canonical root
snapshot to equal the retained snapshot; and recompute every observation's
reason commitment, entry hash, and `policy_match_result`. It then reports each
verified node's ID, outcome, phase, xfail state, closed reason
class/provenance, reason commitment, existing-entry hash, and exact match
result—never a count-only or category-only conclusion. A missing, unavailable,
changed, or inconsistent source/snapshot/link blocks review and the
policy-change path.

For any proposed new approval, the reader must first validate the proposed
entry with the same allowlist-v1 rules and then require that
`allowed_in_ci` is the literal JSON boolean `true`. `false`, a missing field,
or any non-boolean value is nonmatching and fails before review, even where
the general v1 schema merely type-checks that field. Only then may it prove the
candidate's component, node ID, outcome, and normalized-reason commitment
exactly equal the retained observation. A same-node/same-outcome entry with a
changed reason, a matching reason class, an equal count, common text, or
`allowed_in_ci=false` is insufficient. This true-only predicate must be the
same one used by the topology failure-review reader and the current exact
governance comparator's CI-permission decision; neither path may accept an
entry the other rejects. It is type-exact, not truthy: the reference predicate
is `type(value) is bool and value is True`, so the string `"true"`, integer
`1`, and every other non-boolean fail exactly like `false` or an absent value.
The comparator must either enforce that type-exact predicate itself or accept
only a sealed typed-entry value produced by an unbypassable validation boundary
that performs it; no public reader/comparator route may pass a raw mapping to a
truthiness check or bypass validation. The reader is diagnostic-only: it
cannot write the allowlist, alter authority, emit a receipt, or turn the prior
run's 281 skips, or any future skip, into an accepted result. This adds no
broad skip/xfail policy.

T-G03D tests must prove the record is absent before custody postcheck and is
published only for a fully validated, non-pass exact execution; a custody,
baseline, list, raw-report, or no-clobber failure must publish no record.
They must prove canonical bytes/self-hash and all current run/head/inventory/
baseline/remainder/custody bindings, the exact canonical policy snapshot and
source hash, one complete observation per selected ID, redaction, and every
closed outcome/phase/xfail/reason/provenance/match-result combination. Hostile
tests must reject policy-document digest drift, policy-entry canonicalization
or hash drift, a same-node/same-outcome changed reason, and each
`NO_POLICY_ENTRY`, `OUTCOME_MISMATCH`, `REASON_MISMATCH`, `CI_DISALLOWED`, and
`EXACT_POLICY_MATCH` transition. They must reject an unsafe or unrecognized
reason class, unknown outcome/phase/xfail state/provenance/match result, a
reader-created approval candidate without the verified per-node commitment, or
an otherwise matching node/outcome/reason candidate whose `allowed_in_ci` is
false, missing, or non-boolean. A differential validation-plus-comparison
boundary matrix must exercise the public failure-review reader and public
governance-comparator route—not merely a prevalidated internal helper—with an
otherwise exact candidate: literal JSON boolean `true` accepts; `false`, an
absent field, string `"true"`, integer `1`, and every other non-boolean reject.
It must prove that no direct/raw caller can bypass the type-exact validation
boundary or obtain truthiness-based acceptance. They must reject a tampered,
stale, missing, duplicate, foreign, or malformed diagnostic. They must also
prove that no diagnostic can satisfy receipt aggregation, root reconciliation,
a `PASS` governance record, or deferred acceptance, and that the next exact
run exposes verified diagnostic node IDs and bindings before any
allowlist/policy input can be changed.

The root-accounting set is closed only when the baseline candidate set equals
the disjoint union of: the passed remainder IDs; all passed portable-source
IDs; all passed native/external IDs whose capability/authority is available;
and the expected IDs of valid native/external `DEFERRED` receipts. Every
baseline ID appears in exactly one term. Thus ordinary remainder plus portable
source tests execute exactly once, while native/external IDs execute exactly
once when available or are governed deferred without a claimed pass. Any
inventory drift, candidate/list digest mismatch, duplicate execution,
unaccounted baseline ID, or selector output outside this equality fails closed.

T-G03D source contracts must expose a narrow collector result and remainder
executor interface so tests can inject candidate records without running the
host suite. They must prove: the collector binds the exact policy and rejects a
changed marker/plugin/root/extension identity; inventory IDs are a subset of
the baseline; the generated remainder has no duplicates and is its exact set
difference; a new ordinary root node enters the next baseline; every execution
argv comes only from the verified generated file; and baseline/receipt/remainder
union or execution-count drift is fatal. They must additionally exercise the
failure-only diagnostic contract above, including its postcheck ordering and
failure-only/non-acceptance boundary. These are test-governance/orchestration
contracts only: they change no production validator, authority, runtime, live
flag, service, database, or trading behavior.

### Topology-aware test governance

`check-test-governance-topology` is a new portable-only Make target. It invokes
the existing governance script in the required `--topology-audit` mode and with
all five arguments shown above. This mode replaces only the generic **root**
suite launch made by the current `check-test-skips`; it must never call the
current root `run_suites()` branch or execute `pytest ... tests`.

Instead, topology audit consumes and revalidates the sealed baseline candidate
record, `portable-root-remainder.governance.json`, and the exact lane governance
records at `$(TEST_EVIDENCE_DIR)/capability-topology/<code>.governance.json`
together with their exact lane receipts. It must first perform the baseline
record's policy/list-digest/current-run/head binding and each receipt's
canonical-byte, self-hash, current Foundation run/head, tracked-inventory hash,
and completeness checks. It must also reject any failure-only remainder
diagnostic it finds before reconciliation, whether current, stale, foreign, or
malformed; the diagnostic is available only to the separate failure-review
reader and cannot enter an aggregate. It requires the remainder governance
record to have
complete collected and passed sets exactly equal to the generated remainder. For
every inventory code it then requires exactly one of:

* a `PASS` receipt with one no-clobber root governance record whose complete
  collected and passed node sets each exactly equal that receipt's sorted
  `expected_node_ids`; or
* a valid native/external `DEFERRED` receipt with the permitted matching state,
  no root governance record, and no claimed test pass.

The baseline candidate IDs must be exactly the disjoint union of the remainder
record's passed IDs, every `PASS` receipt's passed/collected IDs, and every
valid `DEFERRED` receipt's expected IDs. The locked 62 must remain an exact
subset of the baseline and must not leak into the remainder. No
extra/duplicate/missing node, code, lane, or execution is allowed. A root
remainder or exact-lane record containing skipped, deselected, xfailed, xpassed,
failed, or not-run outcomes fails; a deferred receipt cannot be converted into
a pytest skip or a test `PASS`. Thus every root test obligation previously
supplied to generic governance—including dynamically discovered ordinary root
tests—is accounted for by one exact execution or a valid, visible
native/external receipt rather than silently lost.

Topology audit must retain the existing governance policy semantics and
evidence, not replace them with a new permissive allowlist. It validates the
same tracked allowlist schema, owner/security derivation, approval/review date,
reason, allowed-in-CI, stale/new/changed skip and deselection checks, and
fail-closed collection/outcome rules. It applies those checks to the audited
root remainder and exact-lane observations and continues the existing governed
legacy and dashboard collection/reporting paths, merging all three component
records into the topology governance report. Legacy and dashboard tests may use
their existing component suite invocations; only the portable root invocation
changes to audited collection and exact generated-node records. The report
preserves per-component records and policy decisions so the Foundation artifact
remains comparable and inspectable.

`check-critical-coverage` remains a separate required control with its existing
sealed coverage policy and exact coverage selections; topology routing must not
remove, lower, or replace that coverage gate. The portable governance mode must
also reject any unapproved skip/xfail or unaccounted test loss in its root,
legacy, dashboard, or coverage evidence.

`test`, `test-portable-embedded-proof`, and
`test-all-portable-private` remain available with their existing generic/local
semantics and are not changed by this topology work. `make ci`, `make audit`,
and `make audit-release` remain strict and unchanged. The one explicit routing
change is that `ci-portable-private` uses
`test-all-portable-topology-private` instead of
`test-all-portable-private`; neither the new aggregate nor anything it invokes
may depend on generic `test` or `test-portable-embedded-proof`.

The existing strict `check-test-skips` target remains byte-for-byte behaviorally
unchanged for strict `make ci`: it continues to use generic root, legacy, and
dashboard governance collection. It must not acquire `--topology-audit`, and
the new target must not alter production behavior, allowlist authority, or
strict CI semantics.

T-G03C must add topology tests that parse the Make dependency graph and fail
if: the `ci-portable` wrapper stops using its private `RUNNER_TEMP` child;
`prepare-root-test-install`, `audit-portable`, D0, contracts, secrets, backend,
dashboard, dashboard typecheck/lint, test-governance, critical coverage,
dashboard build, Python-source audit, or dependency-audit controls disappear;
the source-required route directly or transitively reaches `test`,
`test-portable-embedded-proof`, or `test-all-portable-private`; an individual
pytest command appears in Foundation; or the workflow ceases to invoke exactly
`make ci-portable`. Those tests must also prove the legacy targets retain their
current generic definitions. They must also prove the new remainder target is
present, that it consumes a generated candidate-minus-inventory file rather
than a literal list, and that `ci-portable-topology` orders audited collection
before every root execution. Exact inventory and dynamic baseline validation,
not broad skip/xfail, prevent loss of the complete root-test universe.

Beyond this Make-edge check, T-G03C must add a script-level transitive-routing
test. It invokes the topology lane runner and `check_test_governance.py
--topology-audit` with sealed fixture receipts/reports and an injected command
recorder in place of every subprocess launcher reachable from the
`ci-portable` source-required route. The recorder must fail closed on an
unknown/dynamic command. It must reject every generic **execution** invocation,
including `uv run pytest ... tests`, `python -m pytest ... tests`, direct
`pytest ... tests`, and shell-string equivalents. The sole exception is the
canonical topology collector's audited `--collect-only` root command with the
exact portable marker/policy/plugin; the recorder must reject it if any field
or collection-only status differs. It must inspect recorded argv after wrapper
expansion, not merely grep Make prerequisites. The only permitted root pytest
execution invocations are the generated remainder list or exact
inventory-derived lane node argv, each with the governance plugin; no `-k`,
broad marker deselection, skip, or xfail mechanism is permitted. The test must
cover the collector, remainder executor, and governance topology branch, so
reintroducing `run_suites()` or a new helper that root-runs `tests` fails even
when the Make dependency graph is unchanged.

### Portable-source lane

It executes all and only the 32 portable node IDs after the narrow test-fixture
repairs. Selection is generated from the locked inventory, not a hand-written
`-k` expression. Every listed ID must be collected and executed exactly once;
skip, xfail, deselect, collection-hook removal, or an extra/missing ID fails.
The existing governance reporter and non-runtime gates remain in use.

The permitted source scope is test-only: semantic fixture construction,
fakeroot harness construction, and sealed-UV policy fixture/direct test helpers.
Any required production policy, materializer, validator, authority, runtime, or
native-source edit stops this work for a new design.

### Native-capability lane

Before selecting its exact native IDs, future orchestration writes a sealed
preflight receipt with one of these states:

| State | Meaning | Required result |
| --- | --- | --- |
| `AVAILABLE` | All executable identity/capability checks and actual user-namespace probe needed by the selected code succeed. | Run every selected node; PASS only if all execute and pass. |
| `UNAVAILABLE` | Required component is absent, or runner policy explicitly disallows required namespace operation before test execution. | Only this state may write governed `DEFERRED`; name all affected IDs and preflight fact. |
| `BROKEN` | Component exists but fails identity, version, required option, isolation, or execution validation. | Fail; no deferral and no fallback. |

For `NATIVE-BWRAP-OS-SANDBOX`, `AVAILABLE` requires the real Bubblewrap
identity and required options. For `NATIVE-USERNS-ROOT-PROVISION`, it requires
an actual permitted `unshare --user --map-root-user` probe. A pathname,
`which`, fake executable, or emulation is not availability. Partial capability
is `BROKEN` unless a new reviewed inventory splits it into complete codes.

### External-authority lane

It has no acquisition or synthesis behavior. It preflights the exact Phase 3B
corpus and legacy UV authority and writes one sealed receipt per authority code:

| State | Meaning | Required result |
| --- | --- | --- |
| `ABSENT` | Entire declared authority root/executable is absent before any test or substitute action. | Only this state may write governed `DEFERRED`, listing every affected ID. |
| `VALID` | Path, direct identity, digest/version, completeness, and reviewed inventory binding validate. | Run every selected node; PASS only if all execute and pass. |
| `PARTIAL` | Only some declared paths/material are present. | Fail; do not defer or select a subset. |
| `INVALID` | Present authority fails digest, ownership, mode, expected corpus inventory, or other validation. | Fail; do not defer or substitute. |

Receipts must not expose corpus records, database values, credentials, or UV
contents. They may identify only the code, exact node list, state, inventory
hash, and redacted diagnostic class.

## Completeness, receipt, and outcome rules

Each proposed receipt is no-clobber published inside
`/tmp/trading-agent-test-evidence`, therefore captured only by the locked
Foundation artifact `test-governance-${{ github.run_id }}` with 14-day
retention. A receipt remains redacted: it must not contain corpus records,
database values, credentials, executable bytes, or unredacted paths/failure
output.

### Receipt schema and exact bytes

Every lane uses the one versioned schema
`"t-g03a-capability-receipt/v1"`. Its complete object key set is exactly:

```text
schema_version
foundation_run_id
foundation_head_sha
inventory_sha256
lane
capability_or_authority_code
expected_node_ids
collected_node_ids
completeness_sha256
preflight_state
redacted_fact_class
outcome
receipt_sha256
```

`foundation_run_id` is a decimal string with no leading zero;
`foundation_head_sha`, `inventory_sha256`, `completeness_sha256`, and
`receipt_sha256` are lowercase 64-character SHA-256 hex strings except that
the head is the exact lowercase 40-character Git SHA. `lane`,
`capability_or_authority_code`, `preflight_state`, `redacted_fact_class`, and
`outcome` are non-empty ASCII strings; `outcome` is exactly one of `PASS`,
`DEFERRED`, or `FAIL`. `expected_node_ids` and
`collected_node_ids` are duplicate-free arrays of ASCII pytest node-ID strings
in ascending bytewise order. Schema v1 permits only strings and arrays of
strings: JSON numbers, booleans, null, nested objects, and nested arrays are
forbidden. Counts are re-derived from arrays, so no implementation-dependent
numeric encoding is accepted.

The exact receipt bytes are strict UTF-8 without a BOM, produced by serializing
the object with object keys sorted by Unicode code point, `ensure_ascii=false`,
separators exactly `(',', ':')`, no insignificant whitespace, and no trailing
newline. Because schema v1 prohibits JSON numbers, there is no alternate
numeric spelling. A verifier must parse strict UTF-8, reserialize by this rule,
and require byte-for-byte equality with the received bytes; escaped Unicode or
different key order/whitespace is rejected even when semantically equivalent.

The **payload** is the same object with `receipt_sha256` omitted. Its canonical
UTF-8 bytes are hashed with SHA-256; the lowercase hexadecimal digest must equal
`receipt_sha256`. The writer computes this payload hash before adding the field.
Every consumer, including aggregation, must perform this self-hash verification
before reading an outcome or evaluating a preflight state. `completeness_sha256`
is SHA-256 over the same canonical-byte procedure applied to an object whose
complete key set is exactly `lane`, `capability_or_authority_code`,
`expected_node_ids`, and `collected_node_ids`; it prevents an inventory list
from being detached from its receipt.

### Aggregation order

Before evaluating receipt outcome, aggregation must reject unless all of these
are true: strict canonical-byte parse; exact schema/key/type validation;
self-hash validation; `foundation_run_id` equals the current Foundation run;
`foundation_head_sha` equals that run's checked-out head; and
`inventory_sha256` equals the locked tracked
`tests/fixtures/t-g03a-hosted-failure-inventory.tsv` hash.
It must then validate the completeness digest and exact lane/code/node mapping.
Only after those bindings hold may it evaluate `PASS`, `FAIL`, or `DEFERRED`.
Thus a valid receipt from another run/head, or one for a different inventory,
cannot contribute a green or deferred outcome.

### Lane and Foundation outcomes

Each lane's test status is exactly `PASS`, `DEFERRED`, or `FAIL`.

* The portable-root-remainder and portable-source lanes are `PASS` only when
  every selected test was collected and executed once successfully. Neither can
  defer.
* A native-capability or external-authority lane is `DEFERRED` only when its
  otherwise valid receipt proves the matching allowed `UNAVAILABLE` or `ABSENT`
  state, respectively, and no affected test executed. The deferred job must
  report its test status as `DEFERRED`; it must not call its tests a `PASS`.
* Any executed test failure, invalid receipt, stale binding, missing/duplicate
  node, incomplete collection, `BROKEN`, `PARTIAL`, `INVALID`, or a failed
  state-to-lane/code/node mapping is `FAIL` and fails closed.

The union of **inventory** lane IDs must equal the locked 62 exactly; every ID
belongs to one lane/code once. The separate dynamic remainder plus those
inventory receipts must equal the baseline candidate collection exactly, as
defined above. A `PASS` receipt proves every selected test was collected and
executed once. A `DEFERRED` receipt proves no affected test executed and only
the permitted state caused it. Unknown/duplicate/omitted/stale/skipped/xfail/
deselected IDs fail aggregation.

The Foundation source-required job may be green only when the portable-source
and portable-root-remainder lanes are `PASS` and the receipt aggregator has
validated every lane's canonical bytes, self-hash, current run/head, locked
inventory hash, baseline collection, completeness, and
state-to-lane/code/node mapping. Valid native/external `DEFERRED` receipts may
therefore coexist with that green job, but they never become test `PASS`.
When any valid deferred receipt exists, the separate overall runtime-proof
summary is `COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS`, not full `PASS`; when none
exist and every lane is `PASS`, it is `COMPLETE`. This distinction is visible
in the aggregate evidence and cannot be converted into a hidden green result.

## Rejected alternatives

**Run only the locked 62:** rejected. Those IDs classify historical failures,
not every portable root test. A fixed remainder list would likewise lose newly
added ordinary root tests. The audited baseline collector plus generated
remainder is required to preserve the whole portable root candidate universe
without broad root execution.

**Blanket marker, skip, or xfail:** rejected. It cannot bind exact 62-node
coverage, lets new tests evade governance, and hides unavailable versus broken
native capability or absent versus partial/invalid authority.

**Install every capability in Foundation:** rejected as an assumption. Runner
kernel policy controls Bubblewrap/user namespaces; exact legacy UV and corpus
are authorities, not packages; engine/toolchain build is out of scope; and a
convenience install expands hosted trust. A later proposal may assess a separate
pinned native runner only with provenance, identity, capability, no-network,
no-live, and receipt evidence.

**Move all sealed-UV tests to native:** rejected. Twenty-seven tests inject the
sandbox/build boundary or inspect policy/descriptors only, while three rows
intentionally attest real Bubblewrap identity. Collapsing either distinction
loses security coverage.

## Later implementation sequence and stop conditions

1. Independently review this document and inventory hash.
2. Apply only focused test-fixture repairs for the 32 portable rows, with
   adversarial tests preserving the original security assertions.
3. Add reviewed parser, exact collection verifier, and hostile receipt/state
   tests through the existing evidence boundary.
4. Add the audited dynamic baseline collector, generated portable-root-remainder
   lane, exact-inventory capability lanes, and portable hosted aggregate as
   specified above, while retaining the private `RUNNER_TEMP` wrapper and every
   listed non-root-test gate. Do not change strict `make ci`, `make audit`, or
   `make audit-release`; retain both live flags false.
5. Run portable source. Native/external may defer only as specified; `BROKEN`,
   `PARTIAL`, and `INVALID` are red.

Stop for a new design if a portable repair needs production behavior change, a
native proof needs emulation, exact collection cannot be bound, an authority is
partial/invalid, strict Make behavior would change, a live flag changes, or a
proposal requires engine build, download, corpus copy, service/database/
scheduler mutation, broker, exchange, or protected runtime mutation.
