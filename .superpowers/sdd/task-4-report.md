# Phase 4B Task 4 Report: Rotatable Semantic-input Authority

## Outcome

Implemented a dry-run-attested, versioned semantic-input publication protocol
and a backend resolver that consumes only the root-owned active authority.
Refresh can publish a new <=30-minute snapshot without replacing or mutating
the current tree. The final atomic active-file replacement occurs only after
the new version tree and immutable manifest are complete.

No runtime tree, root path, service, process, job, network endpoint, exchange,
broker, credential, mode or safety sentinel was accessed or changed.

## Publication protocol

1. Dry run validates exactly six explicitly named, distinct canonical JSON
   paths and `(device, inode)` identities through retained no-follow dirfds.
2. The canonical plan covers source path/inode/size/hash; fixed `generated_at`;
   manifest version; backend commit; runtime uid/gid; validity; output paths;
   and trusted-parent device/inode/owner/group/mode. It returns a SHA-256 plan
   digest without writing an input, manifest or active file.
3. Apply requires root and the exact approved dry-run plan digest. Any source,
   identity, timestamp, destination authority or policy change fails before a
   privileged write.
4. Apply creates a unique version directory under the retained input-parent
   fd, writes only six fixed destinations, fsyncs them, then sets ownership and
   read-only modes before authority publication.
5. Apply writes a unique root-owned immutable version manifest. Only after the
   tree and manifest are complete does it atomically replace the fixed active
   authority in the retained authority-parent fd.
6. Failed or concurrent preparation cannot expose a partial active snapshot.
   A failed refresh leaves the prior active file unchanged; successful
   concurrent refreshes can only select one complete version. Old immutable
   versions remain available for audit and controlled cleanup by later ops.

There is no source-directory, copy-tree, glob, walk, safety-input or credential
interface. CLI failures return one generic redacted message.

## Ownership and mode policy

- Input and authority parents must already exist, be root-owned, and have no
  group/other write bits. Their exact identities are included in the plan.
- Both parents must be traversable by the explicit runtime uid/gid.
- Every destination operation is descriptor-anchored below retained,
  revalidated parent fds and uses `O_NOFOLLOW` for opens.
- Version tree directories are chowned to the explicit runtime uid/gid and
  sealed `0500`; the six files are chowned to the same identity and sealed
  `0400`.
- Version manifests and the active authority are root-owned/root-group and
  exactly `0444`.
- Canonical approved-plan archives are root-owned/root-group and exactly
  `0444`; the fixed per-authority publication lock is root-owned `0600`.
- No ownership or mode mutation occurs after active publication.

## Manifest and active contracts

The immutable manifest persists and the backend validates:

- `manifest_version`;
- `classification = READ_ONLY_EXTERNAL_INPUT`;
- `command = SNAPSHOT`;
- exact protected `backend_commit`;
- exact approved version root and <=30-minute validity;
- exactly six logical files, each with exact path/hash plus
  `required = true` and `read_only = true`.

The root-owned active record pins one version-manifest name and digest, one
version-directory name, its manifest version and the approved plan digest.
The backend reads that record once through the protected authority dirfd, then
opens only the selected manifest/tree. Missing, malformed, stale, mismatched,
partial or unsafe current authority fails closed; it never silently falls back
to an old snapshot.

## Commits

- Original main implementation: `bde659329bf754fc6a017764e1c92c7f41c66ad6`.
- Original backend implementation: `bbdff35429591b091c7b882ff526b18cb085aece`.
- Review-fix backend: `fb606f943ad16bf6b20fcd157d15d59c903696a7`.
- Review-fix main: `4b4c34932c05fd2c1393b7ba8b5d84be16f1300d`.
- Residual-fix backend: `63349fce969f6bc6009da262f3b12fd9bc97988e`.
- Residual-fix main: `9ee632049ef2c1266f8db2cff708a81973c9f6ec`.
- Final-review backend: `741554ba969bb1f5ddbf01b1d2f6c921a7957dee`.
- Final-review main: the commit containing this report, builder and focused
  test; exact hash is recorded in the handoff.

## Strict RED-GREEN evidence

Initial Task 4 cycles:

- Main RED: missing `scripts.build_phase4_semantic_manifest` module.
- Backend RED: `5 failed, 5 passed`; production still consumed an unset code
  constant while tests supplied protected authority.
- Initial GREEN: main `9 passed`; backend semantic `10 passed`.
- Root-apply RED then GREEN proved apply rejects a non-root caller.

Review-remediation cycles:

1. Main protocol RED: focused test failed because production lacked the new
   `manifest_version` and plan-attestation API.
2. Main protocol GREEN: `9 passed`, covering reusable plan matching,
   versioned metadata/modes, refresh failure safety, concurrent complete
   activation, distinct paths/inodes, symlink/unsafe-parent rejection and
   parent path replacement.
3. Backend resolver RED: `6 failed, 6 passed`; production still required the
   old environment digest instead of resolving active authority.
4. Backend resolver GREEN: `12 passed, 87 deselected`, covering active
   rotation, current failure/no fallback, digest/window, strict metadata,
   no-follow reads and descriptor anchoring.
5. Runtime traversal and redaction RED: two focused failures showed missing
   runtime-parent execute validation and a traceback-producing CLI.
6. Final main focused GREEN: `11 passed`.

Residual-review cycles:

1. Clock/monotonic/archive RED: four focused failures showed the missing
   injected clock and publication serialization API.
2. Backend archive-binding RED: `5 failed, 8 passed`; the resolver rejected
   the expanded active contract before attesting the protected plan.
3. Exact-idempotency RED: a corrupted active plan reference with the same
   timestamp/version/digest incorrectly returned a no-op.
4. Final GREEN: main `15 passed`; backend semantic `13 passed, 87 deselected`.

Final-review cycles:

1. Post-lock freshness/idempotent RED: one test proved the injected clock could
   acquire the publication lock, so clock validation still ran too early;
   eight direct probes showed missing/tampered plan, manifest or tree state was
   incorrectly reported as an idempotent success.
2. Full-plan backend RED: sixteen direct probes showed unknown/missing fields,
   wrong destination/authority/runtime identity/validity/backend lineage,
   wrong logical source sets, runtime paths, hashes, sizes and duplicate
   inodes were accepted when a root actor rebound outer digests.
3. Preprovisioned-lock RED: apply silently created a missing lock, which would
   be a mutation before the required post-lock freshness check.
4. Final GREEN: main `25 passed`; backend semantic plus direct schema probes
   `29 passed, 87 deselected`; backend full offline `202 passed, 2 skipped`.

The fixed `0600` root-owned lock must now be provisioned before apply. Apply
opens and exclusively locks it without creating or modifying it, reads current
authority, then invokes the timezone-aware clock immediately before any
publication mutation. Expiry or excess future skew leaves active authority and
all published artifacts unchanged.

An exact idempotent candidate is not accepted from metadata alone. While the
lock remains held, the builder reopens the canonical active record, protected
plan and version manifest through retained authority dirfds, compares their
exact bytes/owner/group/modes, then walks the selected version dirfd and
requires the exact directory/file set, runtime uid/gid, `0500`/`0400` modes,
sizes, bytes and SHA-256 values. Any discrepancy fails closed without repair,
fallback or a successful no-op result.

The backend now validates the complete canonical plan schema and exact field
sets. It checks fixed destination and active-authority paths, manifest version,
classification, SNAPSHOT command, protected backend commit, runtime uid/gid,
1-30 minute validity, actual parent attestations, and exactly six distinct
logical sources. Each source requires canonical absolute reference,
code-defined runtime path, device/inode, bounded size and lowercase SHA-256;
runtime path/hash/size are bound to the selected manifest and actual immutable
file. Errors remain generic and never include source paths.

Apply now validates a timezone-aware injected/default UTC clock before lock
creation. It rejects `generated_at` beyond the explicit 30-second skew and
rejects `now >= valid_until`, leaving all authority entries unchanged.

Refresh uses one derived root-owned `0600` lock file opened below the retained
authority fd and held with exclusive `flock` through active comparison and
replacement. Older or equal-but-different work is rejected; an exact complete
active record is an idempotent no-op. Delayed older concurrent work therefore
cannot overwrite newer authority.

The canonical approved plan JSON is archived next to the version manifest at
`0444`. Its digest covers the complete source path/device/inode/size/hash set
and all destination/runtime/timestamp policy. Both manifest and active record
pin the derived plan filename and digest. The backend reads only that derived
root-owned basename, verifies exact hash and canonical JSON, and binds the
active/manifest version and timestamp without opening or exposing source refs.

## Final verification

- Main focused plus command-registry regression:
  `uv run pytest -q tests/jobs/test_semantic_manifest_builder.py tests/jobs/test_command_registry.py`
  — `49 passed in 0.79s`.
- Backend semantic/integrity/offline suite:
  `.venv/bin/pytest -q -s tests/test_asset_registry.py tests/test_broker.py tests/test_integration_isolation.py tests/test_live_execution_policy.py tests/test_paper_trader.py tests/test_phase1_safety.py tests/test_phase4_research_only.py`
  — `185 passed, 2 skipped in 25.11s`. The two skips are existing intended
  connectivity skips; no network or exchange path ran.
- `compileall` passed for changed production files and focused tests.
- `git diff --check` passed in both repositories.
- The backend `uv.lock` generated by tooling was removed; the tracked main
  lockfile was preserved.

Residual final verification:

- Main Task 4 focused suite: `15 passed in 0.35s`; compile and diff checks
  passed. The first combined command-registry attempt was temporarily
  uncollectable while concurrent Task 2 edits were incomplete. A later retry
  collected and reported `34 passed, 24 failed`; every failure was in the
  concurrently modified command-attestation suite because expected tamper
  rejections were not yet raised. Task 4 did not edit or stage those files.
- Backend offline suite: `186 passed, 2 skipped in 16.63s`; compile and diff
  checks passed. The backend worktree was clean and `uv.lock` absent after the
  residual commit.

Final-review verification:

- Main Task 4 focused suite: `25 passed in 0.66s`; compile and diff checks
  passed.
- Backend semantic plus direct plan-schema probes: `29 passed, 87 deselected`.
- Backend full offline suite: `202 passed, 2 skipped in 16.86s`; compile and
  diff checks passed. The skips remain the intended connectivity skips.

## Scope isolation and rollback

The main commit stages only the Task 4 builder, focused test and this report.
Concurrent Task 2 authority changes are preserved unstaged and uncommitted by
Task 4.

Rollback is a code revert of the Task 4 main/backend commits. No runtime or
data rollback is required because no provisioning or activation occurred.
