# Canonical Source Consolidation Rollback

Source consolidation did not change the active runtime. Rollback is therefore
a source and workspace selection decision, not a production mutation.

## Rollback decision

If the canonical branch fails a gate or should no longer be used:

1. cease selecting `codex/canonical-monorepo` for further source work;
2. retain the branch, its failed evidence, and provenance manifests for
   diagnosis;
3. select the existing old source repositories and the existing sealed Phase
   4B release authority for continued work;
4. leave the installed runtime and all external runtime data unchanged; and
5. require a new reviewed plan before resuming canonical consolidation,
   implementing Release Authority v2, or attempting a cutover.

The fixed rollback source identities are:

| Source | Identity |
|---|---|
| Core application/ops authority | commit `d9d46fa363f26bd78f5560300d26913494e11e4d`, tree `bfac951424d09f21359fcc11abb0bbe000456b4e` |
| Research backend | commit `41f055b48033714c660f44cc20498b7545366e75`, tree `b15af11d8600e042e20403dba982a3c1bc1b4b60` |
| Dashboard | commit `ca57a7e018eb3afdc263e40b343b7ebbe3f8ccbb`, subtree `3246350253575256b0566cfd54076e8e8ce0412e` |
| Existing sealed Phase 4B metadata | SHA-256 `f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c` |

## Explicit non-actions

Rollback does **not**:

- delete, archive, reset, clean, overwrite, rebase, or rewrite any repository;
- revert an imported snapshot by rewriting history;
- modify or remove a provenance manifest;
- install or remove a release;
- start, stop, restart, or repoint a service, dashboard, or scheduler;
- change protected configuration, a database, a migration, an order, or a
  trade;
- change paper mode, either live approval, or the kill switch; or
- push a branch or mutate a remote.

If a future reviewed change must reverse a canonical source commit, it uses a
new ordinary commit. If a future offline stage fails, it remains rejected and
is handled only under the applicable sealed-stage policy. Neither action
changes the currently installed authority.

Read-only rollback verification consists of comparing the selected source
HEAD/tree identities and porcelain state with the recorded checkpoint, then
running the installed verifier. It does not require a service start or a
runtime write.
