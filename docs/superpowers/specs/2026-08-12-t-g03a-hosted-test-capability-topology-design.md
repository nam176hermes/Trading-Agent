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

The immutable input is
`.superpowers/sdd/TRADING_AGENT_PHASE1_TERRA_AUTOPILOT_CONTINUE_V4_2/t-g03a-inventory.tsv`:

* Exactly 62 rows, one exact failed node ID per row.
* SHA-256: `99e2e9f0ea91c65fd841a0b81b8948eb6d3967203627d0911c151794737a8bfe`.
* 32 `PORTABLE_SOURCE_DEFECT`, 24 `NATIVE_CAPABILITY_REQUIRED`, and 6
  `EXTERNAL_AUTHORITY_REQUIRED` rows; no unclassified row.

Any implementation must reject a different inventory hash, omitted/duplicate
ID, blank required field, or classification outside these three values. A
future category change needs a new reviewed inventory, not an in-place edit.
`t-g03a-inventory.tsv` is the canonical inventory; any prior `.csv` reference
is incorrect and has no authority.

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
immutable 62-node inventory (hash bound)
              |
  collection-completeness verifier
     /             |              \
portable-source native-capability external-authority
     |             |              |
  execute       preflight/run    preflight/run
     \             |              /
        sealed exact-node receipts
                  |
 controller aggregation (never a false GREEN)
```

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
`outcome` are non-empty ASCII strings. `expected_node_ids` and
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
`inventory_sha256` equals the locked canonical `t-g03a-inventory.tsv` hash.
It must then validate the completeness digest and exact lane/code/node mapping.
Only after those bindings hold may it evaluate `PASS`, `FAIL`, or `DEFERRED`.
Thus a valid receipt from another run/head, or one for a different inventory,
cannot contribute a green or deferred outcome.

The union of lane IDs must equal the locked 62 exactly; every ID belongs to one
lane/code once. A PASS receipt proves every selected test was collected and
executed once. A DEFERRED receipt proves no affected test executed and only
`UNAVAILABLE` or `ABSENT` caused it. Unknown/duplicate/omitted/stale/skipped/
xfail/deselected IDs fail aggregation.

`DEFERRED` is not a passing test result and never makes Foundation green. The
aggregate remains `NOT READY` until all required lanes have valid PASS receipts.

## Rejected alternatives

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
4. Add lane orchestration without changing strict `make ci`, `make audit`, or
   `make audit-release`; retain both live flags false.
5. Run portable source. Native/external may defer only as specified; `BROKEN`,
   `PARTIAL`, and `INVALID` are red.

Stop for a new design if a portable repair needs production behavior change, a
native proof needs emulation, exact collection cannot be bound, an authority is
partial/invalid, strict Make behavior would change, a live flag changes, or a
proposal requires engine build, download, corpus copy, service/database/
scheduler mutation, broker, exchange, or protected runtime mutation.
