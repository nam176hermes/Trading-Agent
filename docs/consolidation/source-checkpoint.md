# Canonical Consolidation Source Checkpoint

Captured read-only at `2026-07-13T16:48:47-04:00`. Source checkpoint result:
**PASS**.

## Source repositories

| Component | Branch | Observed HEAD | Authority tree | Status entries |
|---|---|---|---|---:|
| Core source | `codex/phase-4-durable-jobs` | `5a808f5bffa5faeb85ed7ad546c91c850a2f5a10` | `bfac951424d09f21359fcc11abb0bbe000456b4e` at `d9d46fa363f26bd78f5560300d26913494e11e4d` | 0 |
| Research backend | `codex/phase-4-research-only` | `41f055b48033714c660f44cc20498b7545366e75` | `b15af11d8600e042e20403dba982a3c1bc1b4b60` | 0 |
| Dashboard | `codex/security-phase4-hardening` | `ca57a7e018eb3afdc263e40b343b7ebbe3f8ccbb` | `3246350253575256b0566cfd54076e8e8ce0412e` from `trading-agent` | 0 |

The core source HEAD is the approved documentation-only authority amendment.
The canonical application/ops bytes were checked out directly from
`d9d46fa363f26bd78f5560300d26913494e11e4d`; they were not copied from the
later source worktree HEAD.

## Standalone canonical root

| Check | Result |
|---|---|
| Path | `/home/thenam176/projects/trading-agent` |
| Branch | `codex/canonical-monorepo` |
| Initial HEAD | `d9d46fa363f26bd78f5560300d26913494e11e4d` |
| Initial tree | `bfac951424d09f21359fcc11abb0bbe000456b4e` |
| Git common directory | `/home/thenam176/projects/trading-agent/.git` |
| `.git` form | standalone directory, not a symlink |
| Multi-linked Git object files | 0 |
| Remotes | one local provenance remote, `migration-source` |
| Network remote | absent |

The source repositories were inspected only. They were not edited, reset,
cleaned, committed, or pushed.
