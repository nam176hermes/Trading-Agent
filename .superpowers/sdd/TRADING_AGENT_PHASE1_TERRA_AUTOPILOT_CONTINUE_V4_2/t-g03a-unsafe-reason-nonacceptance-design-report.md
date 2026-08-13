# T-G03A unsafe-raw-reason nonacceptance amendment — design report

## Decision

Add one **separate, diagnostic-only, post-custody nonacceptance record** for a
complete exact portable-root remainder report that contains at least one raw
reason which is not a v1-normalized safe reason.  It closes the hosted
Foundation `31717174149` observability gap without changing the reason
redaction predicate, the allowlist, policy validation, receipt-v1, lane
outcomes, or acceptance semantics.

The record is deliberately not an extension of the per-node failure
diagnostic.  That diagnostic binds each observation to a node and, for skipped
or deselected observations, to a policy-reason commitment.  An unsafe raw
reason must never enter either binding.  A new fixed-state record preserves
the existing failure-diagnostic schema/domain and prevents an unsafe reason
from being represented as a normal failure, skip, or policy link.

This is a docs-only amendment to
`docs/superpowers/specs/2026-08-12-t-g03a-hosted-test-capability-topology-design.md`.
It authorizes no source implementation, hosted retry, dependency/build action,
policy edit, runtime authority, service, database, broker, exchange, or live
action.

## Fixed state and exact record

The only new public state is the literal
`UNSAFE_RAW_REASON_OBSERVED`.  It means exactly this: after a successful
retained-custody exit and postcheck, the executor verified that the raw report
has one structurally valid observation for every selected node, and at least
one observation's `reason` failed the existing direct-safe-evidence predicate.
It does **not** identify which observation, why the reason was unsafe, or
whether a reason is a path, URI, secret-like token, control text, unnormalised
text, or another rejected form.

The new record path is fixed:

```text
$(TEST_EVIDENCE_DIR)/capability-topology/portable-root-remainder.unsafe-raw-reason-nonacceptance.json
```

It has schema version `"t-g03a-unsafe-raw-reason-nonacceptance/v1"` and
exactly these top-level keys:

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
custody_postcheck_status
pytest_exit_status
raw_reason_nonacceptance_state
nonacceptance_sha256
```

`diagnostic_only` is literal JSON `true`; `custody_postcheck_status` is
literal `PASS`; and `raw_reason_nonacceptance_state` is literal
`UNSAFE_RAW_REASON_OBSERVED`.  All other fields are strings.  Run/head/date
and digest spellings use the existing canonical validators.  The run/head,
Foundation context/date, locked inventory, verified baseline and generated
remainder self-hashes plus their ID/list digests, and verified custody policy
are exactly the bindings already used by the failure diagnostic and A2
nonacceptance record.

`pytest_exit_status` is a canonical non-negative decimal string.  It may be
`"0"`: pytest can exit zero for a skipped report, but the fixed state itself is
conclusive nonacceptance proof.  No count is emitted, so the result exposes no
raw-derived occurrence count.  `nonacceptance_sha256` is SHA-256 of the
canonical payload with that field omitted.  Canonical bytes remain strict
UTF-8 without BOM/trailing newline, Unicode-code-point-sorted keys,
`ensure_ascii=false`, and `(',', ':')` separators.

The schema intentionally contains **no** `observations`, `test_node_id`, raw
or normalized reason, reason commitment/hash, policy match, policy snapshot,
node count, path, URI, exception text/type, traceback, command, environment,
raw report, or acceptance/receipt field.  In particular it must not derive a
hash from an unsafe raw reason or replace that hash with a fixed fake reason.
The state is not a policy-validation stage/class; A2's closed
`policy_validation_stage`/`policy_validation_class` domains remain unchanged.

## Writer ordering and custody boundary

Only `_execute_exact_with_retained_custody` in explicit immutable
`portable_root_remainder` mode may write this record.  The mode is a hard
selector, not a filename/node-set inference.  The executor reserves this
destination as absent with the provisional report, the normal failure
diagnostic, and A2 policy-validation nonacceptance destination before any
runner is launched.  A lane invocation and every non-remainder caller are
writers of none of these records.

The required order is exact:

1. Reopen and validate the Foundation context/reservation, locked inventory,
   baseline, generated remainder, candidate/list digests, and custody policy;
   reserve all mutually exclusive destinations and reject any existing
   diagnostic, nonacceptance, PASS governance report, or exact lane receipt.
2. Acquire and validate the existing policy snapshot using the unchanged A2
   structural boundary.  A pre-execution policy failure follows A2 only,
   starts no runner, and cannot write this new record.
3. Execute the generated node list under retained custody, remove the runner
   scope, and require the retained-custody postcheck to pass.  If it does not,
   publish nothing.
4. Reacquire/revalidate the policy snapshot after custody exactly as today.
   A typed A2 post-custody failure or source drift follows A2 only; it is
   checked before reading any raw reason and cannot coexist with this record.
5. Strictly parse the provisional report; require the sealed custody policy;
   then validate every raw observation envelope, component, portable-root
   node-ID syntax, outcome/phase/xfail closed domain, uniqueness, sorted
   identity projection, exact selected-node equality, and one-record-per-node.
   These facts remain private working state and must be completed before any
   reason classification.
6. For each already structurally verified observation, apply the existing
   direct-evidence rule without transforming or committing the raw value:
   `reason` must be a string, equal its v1 normalization, and satisfy
   `_reason_is_safe`.  Record only a private boolean that at least one failed.
   Do not call `reason_commitment_sha256`, the policy comparator, a reason
   mapper, or any error-message classifier for a failed value.
7. If the private boolean is true, build exactly the fixed payload above,
   calculate its self-hash, then privately stage, fsync, atomically install
   no-replace, fsync the directory, and byte-reread/strict-parse/self-hash
   verify the final artifact.  Only after that verification, raise the fixed
   redacted public failure `UNSAFE_RAW_REASON_NONACCEPTANCE`.
8. If no unsafe raw reason exists, continue the current per-observation
   failure-diagnostic/PASS path unchanged.  Normal failed or skipped evidence
   still uses the current policy-link and reason-commitment rules.

The writer may clean up only its own uninstalled staging file.  A target,
staging collision, publication error, final-byte change, parse failure, or
self-hash failure is fatal and is never converted to a fresh diagnostic or
PASS.  The provisional raw report is removed in the existing `finally` path
after the decision; it is never published or used as a recovery input.

## Reader, coexistence, and acceptance guards

Add a diagnostic-only reader which strict-parses canonical bytes, validates
the exact key/type/domain set and self-hash, then independently reloads the
same Foundation context, inventory, baseline, remainder, and custody policy.
It re-derives every listed binding and requires exact equality.  It cannot
write a record, read/approve/modify the allowlist, create a receipt, or change
a lane outcome.

The following states are impossible by construction and must be rejected on
mere path presence before an acceptance consumer evaluates any content:

* this record with a normal failure diagnostic, A2 policy-validation
  nonacceptance, portable-root PASS governance report, any lane governance
  report/receipt, aggregate, reconciliation output, deferred claim, or a
  second unsafe-reason record;
* a record read as normal failure evidence, a policy-validation
  nonacceptance, a receipt, a `PASS`, `DEFERRED`, an aggregation input, or an
  allowlist candidate;
* a malformed, stale, foreign run/head, context/baseline/remainder/custody
  drifted, noncanonical, self-hash-invalid, or duplicate record treated as
  absent.

`read_failure_diagnostic`, the A2 reader, lane publication, receipt
aggregation, portable-root reconciliation, topology governance audit, and
every root PASS publication must reject presence of any of the three terminal
artifacts before their normal reader/aggregation work.  This is symmetric:
the new reader rejects the other two terminal artifacts.  A retry requires a
new evidence root and Foundation run context; no-clobber forbids reuse.

## Hostile and regression proof matrix

Focused T-G03F tests must demonstrate all of the following without running a
hosted job:

* Each existing unsafe category (slash/backslash, C0/C1 control, URI scheme,
  credential word, long credential-like token), plus a non-string and a
  non-v1-normalized reason, in an otherwise complete report emits exactly the
  fixed record after postcheck and raises only the fixed public error.
* Raw fragments, any raw-derived digest, every affected node ID, occurrence
  count, path/URI, secret-like text, exception text/type, traceback, and raw
  report bytes are absent from artifact bytes and public errors.  A mixed
  safe/unsafe complete report must disclose neither a safe observation nor the
  unsafe observation identity in this record.
* A complete unsafe skipped report with pytest status zero is nonaccepting;
  it emits neither a normal failure diagnostic nor a PASS governance record,
  receipt, aggregate, reconciliation, or deferred result.  A safe non-pass
  remains the exact existing normal diagnostic flow; a safe all-pass report
  remains the exact existing PASS flow.
* Duplicate, missing, extra, foreign, unordered, non-root, malformed, or
  unsafe-node-ID observations; raw custody drift; runner selected-list drift;
  failed custody postcheck; missing/invalid context/baseline/remainder; and
  policy-snapshot/A2 failure publish no unsafe-reason record.  Structural
  proof failures dominate raw-reason redaction.
* Parser fuzz/mutation tests reject every added/missing/retyped key, nonliteral
  `diagnostic_only`, unknown state, non-`PASS` custody, invalid status/digest,
  noncanonical bytes, duplicate JSON keys, changed self-hash, current-context
  drift, and a record with a forbidden field such as observations, node ID,
  reason, or receipt outcome.
* Pre-existing final target and hostile staging collision do not clobber;
  partial publication, post-write replacement/tamper, and reader binding drift
  fail closed and leave acceptance blocked.  Test writer-readback and reader
  self-hash independently.
* Presence tests cover each named acceptance reader both for a valid record
  and for malformed/foreign bytes.  Pairwise coexistence with every terminal
  record and every receipt/PASS artifact must be rejected before parsing that
  acceptance artifact.  A non-remainder `run_lane` injection cannot create
  this record.

The old combined test
`test_unsafe_or_duplicate_raw_failure_evidence_does_not_publish_a_diagnostic`
must split: duplicate/structural-invalid evidence still publishes nothing;
the complete unsafe-reason branch expects only the new nonacceptance artifact.

## Stop conditions

Stop for a new design—do not implement this amendment—if any required proof
would require relaxing `_reason_is_safe`, normalizing/persisting/hash-committing
an unsafe raw reason, publishing an unsafe node identity, inferring state from
exception text, changing allowlist/CI approval semantics, changing A2's stage
or class matrix, changing receipt-v1, or allowing the new record into a lane
or aggregate.  Also stop if exact one-record-per-selected-node structural
proof cannot be established entirely before reason classification, or if the
existing custody postcheck cannot remain the publication boundary.

## Self-review

No placeholders or open schema fields remain.  The design chooses a new
terminal diagnostic rather than weakening the existing failure-diagnostic
domains, keeps A2 independent, specifies the exact publication order, and
requires all unsafe/raw-sensitive material to remain non-retained.
