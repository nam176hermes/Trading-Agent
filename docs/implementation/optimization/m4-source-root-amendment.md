# M4 source-root amendment — decision and implementation contract

Status: DESIGN PREPARED; runtime authority change NOT APPLIED.
Base: main `0ba0229ab972642360530eb7483a060b28898be2`.
This document records the missing architecture decision; it is not a profile,
credential, signature, deployment approval or qualification receipt.

## Outcome and boundary

One protected deployment binding must identify the safety and semantic source
roots used by producers and consumers. No HOME/environment fallback, automatic
root discovery or reuse of old-root evidence is permitted. An unavailable or
changed binding must stop admission and publication.

The current request authorizes preparation, source refactoring and integration.
The repository additionally requires an approved written contract for durable
or cross-component authority changes. Production Release Authority v2 remains a
separate architecture/cutover scope; extending its loader is not a path cleanup.

## Verified current flow

- `packages/safety_evidence.py`: canonical root and exact sentinel fingerprint.
- `packages/runtime_release/config.py`: protected Phase4 authority validates the
  fixed fingerprint; `load_runtime_authority_v2` admits Package6 staging only.
- `services/safety_state_exporter/main.py`: fixed producer roots/mount/output.
- `services/safety_state/provider.py`: snapshot consumer rechecks authority.
- `services/operator_control/composition.py`: writer root must equal canonical
  root; its normalized evidence must match the same source fingerprint.
- `packages/runtime_risk/safety.py`: canonical filesystem safety reader.
- `services/job_worker/safety.py`: factory-issued root and legacy file reader.
- `services/semantic_input_refresher/main.py`: fixed report/macro source roots,
  destination, manifest path and runtime UID/GID.
- `services/job_worker/environment.py`: V2 already binds child paths to attested
  RuntimePathsV2. The normal worker entrypoint depends on V2 authority; production
  loading is unavailable. Migrating Phase4 source roots alone does not open it.
- `apps/control_api/trading_control/approval.py`: historical Phase3 real-import
  approval binds a specific root AND inventory/schema/DB identity. Keep that
  historical import capability unchanged; a new import is a separate operation.
- `packages/runtime_release/paper_application`: preserved release projection,
  subject to generator/pin review, never a directory to hand-edit.

## Recommended source design: explicit Phase4 deployment amendment

Retain the exact existing Phase4 v1 format and semantics. Add a separately
versioned Phase4 document format in the existing protected loader, at the same
fixed authority file. This is not the production Release Authority v2 loader.

The new format binds a deployment identifier, safety source and mounted source,
snapshot output, semantic report/macro sources, input destination, manifest path,
and owner UID/GID. Release/interpreter/command pins remain in the existing
release authority. All paths must be absolute/canonical, with reviewed separation
of writable source, output and protected authority roots. Reject aliases,
ancestor symlinks and writable authority ancestors. Typed factory-issued binding
is consumed only after protected canonical-file validation.

Bind the selected safety source root into the existing source fingerprint.
Bind semantic destination and manifest path into its existing policy digest.
Keep sentinel names, snapshot TTL, parser limits and fail-closed UNKNOWN semantics.
Do not accept an old fingerprint merely because the destination bytes match.

Read/recheck the same deployment binding at producer selection and before each
publication, and at consumer admission/use. Changing a deployment mid-operation
must fail rather than combine fields from different document generations.
No arbitrary runtime `Path` argument or environment selector grants authority.

For legacy v1, retain all old constants and the exact old fingerprint. New paths
are selected only by the explicit protected document version; malformed v2
cannot fall back to v1. Historical migration/import scripts retain old approvals.

## Dependency-ordered implementation

1. Characterize current v1 acceptance and rejection with
   `tests/runtime_release/test_config.py`, worker safety and exporter tests.
2. Add typed deployment binding and exact versioned schema in the existing
   loader; validate canonical bytes, exact keys/types, UID/GID and path policy.
   Tests: two valid roots, wrong fingerprint, duplicate keys, unsafe owner/mode,
   symlink, path overlap, document replacement and invalid-version fallback.
3. Wire exporter composition and operator composition to the same binding.
   Recheck before a write; changing root/profile between read and write denies.
   Verify emitted fingerprint matches the consumer and old evidence rejects.
4. Wire semantic source selection and plan/apply boundary to the bound roots,
   runtime identity and destination. Existing source attestation and approved
   plan digest remain mandatory. Recheck between planning and apply.
5. Wire canonical risk/legacy reader factories where still active, maintaining
   issued-root checks and no untrusted override. Verify all real callers before
   deleting compatibility code. Keep V2 staging child-root contract unchanged.
6. Regenerate affected projections/pins using their owning tools; test exact
   projected imports, old v1 runtime and new deployment consumption. Never
   weaken a frozen inventory to make the changed tree admissible.
7. Run focused tests, static, audit/contracts, aggregate and dashboard build,
   independent scoped review and hosted CI. Freeze an exact committed candidate.
8. The source change invalidates current HWC closure. Retain the original receipt
   externally/history, retire its active source copy, regenerate HELD status,
   then qualify fresh main and import a genuine signed receipt. Do not rebind it.

## M12 dependency that this amendment does not solve

The production V2 loader is explicitly unavailable. The operator must choose a
protected qualification environment and either the already-defined Package6
staging authority route or separately commission production Release Authority v2.
Do not invent a permissive production loader or label the Phase4 amendment as
complete production portability. Protected host, cluster/system identity,
distinct worker/custodian UIDs, profile issuer and independent reviewer are
required inputs, not values to synthesize in code.

## Qualification and rollback

- Read-only preflight: revision, catalog, role/profile/artifact identity, paths,
  clock stability and UTC offset. A short clock probe is not qualification.
- Rehearse the approved migration chain in an isolated disposable cluster;
  never upgrade the installed 0004 cluster directly as a convenience.
- Backup and restore rehearsal must precede any applied migration/cutover.
- Deploy producer/consumer bindings together; fresh snapshots and semantic
  evidence must pass before workload admission. Old evidence stays historical.
- Rollback restores the complete prior deployment/producer/consumer set and
  regenerates evidence. After writes, reconcile deltas before routing back;
  never overwrite new data or reuse expired receipts.
- Use synthetic inputs for fault injection. Only after qualification and exact
  campaign/stage approvals may official research consume holdout once.

## Acceptance

M4 source is complete only when the real entrypoints consume the reviewed
binding, old/new compatibility tests pass, and source/CI/review evidence matches
one commit. M4 deployment and M12 remain separate until actual protected-host
execution and rollback evidence exist. No live authority is introduced.
