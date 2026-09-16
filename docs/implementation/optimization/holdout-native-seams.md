# Remaining protected P3 interfaces — proposed source contract

Status: APPROVED FOR SOURCE/TEST by the operator's “Duyệt” in this task.
Existing official HOLDOUT/PARITY/PHASE_EXIT lanes stay closed until implemented and qualified.
This fills the previously unspecified private delivery/native-request interfaces;
it does not change C01–C16, research thresholds, the accepted public wire schemas,
or grant deployment, database, provider, key-access or official-run authority.

## Why the existing plan is insufficient at these two boundaries

`docs/implementation/p3/operation-transport-v1.md` explicitly leaves the protected
plaintext delivery protocol and native request/runtime closure unspecified.
The accepted CustodyRecord records digests and distinct identities, but defines
neither the attestation bytes nor the algorithm for the plaintext commitment.
A view object, arbitrary Python callback, or artifact digest cannot authenticate
an independent custodian. The accepted native recipe requires the pinned engine
and both primary and selected-baseline paths; the current native fixture is not
that producer. Implementing an implicit success fallback would weaken the plan.

## Proposed M8 interface

1. Keep the canonical SQL disclosure ledger and its one-use commitment key.
   No new database, queue, service installation or permission change.
2. Supply the producer through a bounded local Unix-domain socket, with the
   exact socket path and distinct custodian/research UIDs in the root-owned
   official host profile. Check the protected parent path and both SO_PEERCRED
   identities. A same-UID synthetic test never qualifies custody separation.
3. The parent validates metadata, current profile/review and source; the
   custodian authenticates its own approved identity. Only after exact SQL
   consumption and committed readback may a release be requested. Recheck the
   live claim immediately before release and before each child spawn.
   The custodian independently verifies committed disclosure and current claim
   through an approved authority reader; a worker-provided PASS flag or receipt
   alone is insufficient. Reader privileges and exact attestation/commitment
   bytes must be frozen before implementing this boundary, without a digest cycle.
4. Freeze an internal versioned release request containing source, job/attempt,
   authorization/intent/request digests, commitment, custody-record reference,
   and the exact approved holdout/buffer row inventory. Reject unknown fields,
   oversized requests, expired/stale binding and a second logical request.
5. Freeze the plaintext bundle as canonical JSON mapping content-addressed
   locators to their canonical JSON strings, containing exactly 365 DailyBar
   objects and one BufferOpen. Verify its digest against CustodyRecord. The
   custodian attestation binds ciphertext reference, plaintext digest, exact
   inventory, identities and access-policy digest; only bytes supplied through
   the authenticated custodian channel can satisfy this private contract.
6. Pass one sealed memfd with SCM_RIGHTS; require exactly one descriptor, all
   write/grow/shrink/seal seals, bounded size, and close every received FD on
   failure (including truncated/extra ancillary messages). No plaintext files
   in the research CAS. Encryption/key provisioning stays with the independent
   custodian; no private keys are loaded by the worker.
7. Combine the released rows with already-admitted research metadata to build
   the existing exact 681-artifact view. Give children that view only. Holdout
   replicas share the single authorized experiment; no re-release after an
   uncertain attempt and no fallback primary.

Proof obligations: source/profile/UID substitutions, lost SQL acknowledgement,
expired/revoked claim, symlink/socket replacement, malformed and extra FDs,
wrong digest/inventory, cancellation cleanup and repeat-release denial. Real
cross-UID and disposable SQL qualification require their separate runtime scope.

## Proposed M9 interface

1. Keep P1's pinned sealed Nautilus 1.231.0 launch owner. Add a private,
   content-addressed native request for the already released primary and selected
   baseline paths; bind HoldoutManifest, InstrumentSpec, transition inventory,
   environment and source. No strategy supplied by a caller and no provider IO.
2. Implement the frozen quote/order recipe in that pinned runtime. Demonstrate
   with actual two-bar native execution that no previous-quote/close fill occurs.
   Failure is E_NATIVE_RECIPE; the synthetic calculator is never an engine substitute.
   The P3 adapter lives at `engines/nautilus/p3_next_open.py`, outside the frozen
   P1 `runtime_v1` inventory. Its additional direct API is exactly
   `nautilus_trader.backtest.models.LatencyModel(base_latency_nanos=1)`; pinned
   engine timing tests exercise that setting. Source import coverage checks
   bind this extra API to that one P3 file. This grants no P1 launch authority.
   The boundary checker applies the existing runtime import restrictions to this
   exact entry and rejects core imports of it. Other engine files receive no new
   import exemption; runtime_v1 membership and P1 launch policy stay frozen.
3. Retain three parent-observed native runs for each role. Use an internal pair
   envelope containing the two existing ParityResult references and their
   exact role bindings. Keep accepted public ParityResult bytes unchanged.
4. Recompute the 13 frozen exit checks from retained evaluation, replay,
   primary selection, both executable paths and native pair evidence. Invalid
   provenance is HELD; economic rejection preserves FAIL and forbids fallback.
5. Reuse existing EXIT_DECISION proposals, SQL atomic publication, readback and
   receipt recovery. Parent validation recomputes the proposal before commit;
   no caller-supplied passed flags or standalone success receipt.

Proof obligations: role swaps, forged results/receipts, missing baseline native
execution, policy/source drift, numeric boundary conditions, noncanonical traces,
no fallback after primary failure and commit-acknowledgement-loss recovery.

Approval recorded: adopt these private contracts for source/test implementation.
Runtime provisioning, protected-profile changes and official experiments remain
separate actions even after source approval.

## Private M8 encoding and release discipline

- Domain-separated commitment: SHA256 of canonical JSON with schema_version
  `p3-holdout-commitment-v1`, ciphertext_ref, plaintext_bundle_digest,
  access_policy_digest, custodian_identity, research_identity and row_refs.
  row_refs are the 365 chronological holdout days followed by BufferOpen.
- Attestation adds schema_version `p3-custodian-attestation-v1`, that commitment
  and the two approved OS UIDs to the same fields. CustodyRecord points to these
  bytes; the attestation never contains its own or the CustodyRecord reference.
- Socket frames use AF_UNIX/SOCK_SEQPACKET, canonical bounded JSON and exactly
  one sealed read-only memfd on the successful response. No descriptor on a
  request. Custodian validates peer UID against its protected profile, and the
  client validates the custodian UID. Timeout, truncation and disconnect fail closed.
- SQL marks the release attempt before plaintext access. A distinct custodian
  capability checks committed disclosure, exact request and live job itself.
  Unique commitment prevents release retries, including restart or lost commit
  acknowledgement. An uncertain release is HELD; it is never repeated.

## Per-replica parent fence (source implementation)

The existing BASELINES/OOS driver now requests a sequence-bound grant on its
stderr pipe immediately before each replica. Its worker re-reads the protected
profile and transport identities, then checks current SQL lease/cancellation
and safety through the existing heartbeat before replying on stdin. Timeout,
revocation, malformed/replayed frames and pipe loss deny the grant and follow
bounded process cleanup. Replica children receive DEVNULL streams; they cannot
inherit this channel. The private sandbox profile digest binds this protocol.

This uses the existing worker/driver pipes and adds no service or persistent
permission. Grants are ephemeral control messages, not receipts. Tests cover
real pipe exchange and synthetic worker authority separately; neither is
protected-host qualification. HOLDOUT/PARITY/PHASE_EXIT remain closed: this
fence does not solve the retained released-view lifetime across those jobs.

## Complete native trace admission (source implementation)

Native results must contain the exact requested day/event sequence, source,
timestamps, side, per-event numeric fields and terminal position shape. Summary
validation completes before any trace artifact is retained, so rejected native
output cannot leave a partial trace in the artifact store. Both-role pinned
engine tests exercise this normalization, without granting protected launch
or official parity authority.
