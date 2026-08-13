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
  uv run python scripts/check_test_governance.py --topology-audit \
    --report-dir "$(TEST_EVIDENCE_DIR)/test-governance-topology" \
    --topology-evidence-root "$(TEST_EVIDENCE_DIR)" \
    --inventory "tests/fixtures/t-g03a-hosted-failure-inventory.tsv" \
    --foundation-run-id "$$GITHUB_RUN_ID" \
    --foundation-head-sha "$$foundation_head"
```

This is a Make-only orchestration contract: Foundation continues to invoke only
`make ci-portable`; it must not gain a hand-written pytest invocation, node
list, marker, skip, or xfail. `ci-portable-topology` obtains its three
capability selections from the verified installed tracked inventory and its
ordinary-root selection from the audited dynamically collected baseline, never
from a Makefile or workflow literal. It is the sole root-test route in this
source-required path.

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

The baseline record binds schema/version, current Foundation run/head, tracked
inventory hash, exact collector policy, sorted candidate node IDs, and a digest
of the node-ID file. The collector fails closed if collection emits an unknown
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
`"t-g03a-portable-root-failure-diagnostic/v1"`. Its complete top-level key set
is exactly:

```text
schema_version
diagnostic_only
foundation_run_id
foundation_head_sha
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
allowlist-v1 schema, owner/security derivation, approval, review-date,
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
entry the other rejects. The reader is diagnostic-only: it cannot write the
allowlist, alter authority, emit a receipt, or turn the prior run's 281 skips,
or any future skip, into an accepted result. This adds no broad skip/xfail
policy.

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
false, missing, or non-boolean. A differential hostile test must prove the
failure-review reader and current exact governance comparator both reject that
false-CI candidate. They must reject a tampered, stale, missing, duplicate,
foreign, or malformed diagnostic. They must also prove that no diagnostic can
satisfy receipt aggregation, root reconciliation, a `PASS` governance record,
or deferred acceptance, and that the next exact run exposes verified diagnostic
node IDs and bindings before any allowlist/policy input can be changed.

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
