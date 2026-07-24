# Job Plane dirty-worktree review

## Frozen snapshot

This review records the original worktree at `2026-07-16T16:46:30Z`:

- Repository: `/home/thenam176/projects/trading-agent`
- Branch: `codex/canonical-monorepo`
- HEAD: `9641281a8508709cab212fb460308467681854ef`
- Unstaged modified paths: 70
- Untracked paths: 34
- Staged paths: 0
- Total classified paths: 104

The companion CSV contains exactly one header plus 104 data rows. It is a
frozen inventory, not an instruction to stage the original dirty worktree.

## Classification result

| Classification | Count |
|---|---:|
| `INTENDED_JOB_PLANE_CHANGE` | 90 |
| `DOCUMENTATION` | 10 |
| `GENERATED_REPRODUCIBLE` | 2 |
| `GENERATED_NONDETERMINISTIC` | 2 |
| `PRE_EXISTING_USER_CHANGE` | 0 |
| `RUNTIME_ARTIFACT` | 0 |
| `LOCAL_CONFIG` | 0 |
| `SECRET_RISK` | 0 |
| `UNKNOWN_REQUIRES_REVIEW` | 0 |

Every frozen path maps to the reviewed Prompt 1 A0/A1 evidence, Prompt 2 A2/B1
evidence, or their corresponding tests. This is path-and-diff-scope provenance;
it is not cryptographic proof of authorship.

## Required exclusions

The following tracked internal agent reports are nondeterministic supporting
material and are not named release inputs:

- `.superpowers/sdd/task-4-report.md`
- `.superpowers/sdd/task-5-report.md`

Exclude their worktree modifications from the candidate patch unless a later
review explicitly promotes them to maintained evidence. They must never enter
the immutable release payload.

## Generated-output rule

The following files are reproducible contract outputs:

- `generated/job-api/json-schema/EnqueueJobRequest.json`
- `generated/job-api/openapi/openapi.json`

Do not copy these bytes blindly. Regenerate them from the reviewed
`scripts/generate_contracts.py` and contract source inside the isolated clean
worktree. Include them only when `make check-contracts` proves byte parity.

The ten documentation records may be reviewed and included in an evidence
commit, but documentation and historical evidence are excluded from the
minimal immutable Job API/worker release payload.

## Runtime, local, and secret-risk review

No frozen path was classified as runtime data, local configuration, secret
risk, or unknown. All files were textual; no binary path was present. A
high-risk pattern scan over changed additions and complete untracked files
found zero matching paths. The scan emitted no values. The two
`ops/systemd/*.env.example` paths are tracked fail-closed templates, not
protected runtime EnvironmentFiles.

This does not authorize reading protected configuration, copying credentials,
or assuming that an arbitrary later worktree remains secret-free.

## Selected-patch policy

1. Create the isolated worktree from the approved base commit; do not reset,
   clean, overwrite, or blanket-stage this original worktree.
2. Apply only paths marked `APPLY_REVIEWED_PATCH`, preserving Prompt 1
   containment/provenance prerequisites and Prompt 2 Job Plane changes.
3. Review the actual patch hunks before acceptance; a matching path alone does
   not authorize unrelated hunks.
4. Recreate files marked `REGENERATE_AND_COMPARE` through the canonical
   generator and prove contract parity.
5. Treat documentation separately from executable release contents.
6. Confirm the isolated candidate has no runtime, local, secret, unknown, or
   nondeterministic-agent-report paths.
7. Require a clean status and exact diff review before committing or building.

## Re-snapshot requirement

The repository is shared by concurrent tasks. This CSV intentionally excludes
itself and this review because neither existed at the frozen timestamp. Re-run
the complete status inventory immediately before patch extraction, staging,
commit, and release build. Any added or changed path must receive a new
classification; an unknown critical path stops candidate preparation.
