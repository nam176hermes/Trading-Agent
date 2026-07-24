# Phase 4B Pre-provision Checkpoint

Captured read-only at `2026-07-12T11:34:38-04:00` before Phase 4B code,
release, manifest, environment, unit, database-job, service, scheduler or
runtime-output changes.

## Repository identities

| Scope | Branch | Commit | Tracked / untracked state |
|---|---|---|---|
| Phase 4 application | `codex/phase-4-durable-jobs` | `8eb6d2616941423df830099460d07ca5ae86268e` | clean / 0 |
| Research backend | `codex/phase-4-research-only` | `51de1cf06b3d595a336e19390230d0c09b608585` | clean / 0 |
| Candidate dashboard | `codex/security-phase4-hardening` | `843d44975ef1a9d5d16df114cc7a4ab0ff7c8b5a` | clean / 0 |
| Active research backend | `master` | `0b977fe110f2fd66c3bc7e981b8531cb5dd7a8ac` | 22 tracked changes / 1,820 untracked paths |
| Active dashboard | `main` | `a130303114508b5e3fd5ea44eb8d6234b47d6cf8` | 168 tracked changes / 414 untracked paths |

The active dirty repositories are runtime/user state. Phase 4B must not reset,
clean, archive, copy wholesale or use either tree as an immutable release.

## Safety and active runtime

| Invariant | Observed |
|---|---|
| Requested / effective mode | `paper / paper` |
| `LIVE_EXECUTION_ENABLED` | `false` |
| `LIVE_TRADING_APPROVED` | `false` |
| Canonical kill switch | `INACTIVE` (sentinel absent) |
| Orders / trades | `30 / 0`, SQLite read-only query |
| `trading-agent.service` | active, PID `4181928` |
| `trading-dashboard.service` | active, PID `1485234`; Next PID `1485280` |
| Dashboard listener | `0.0.0.0:3002` |
| Cloudflared | PID `3283180` |

The dashboard PID differs from the original Phase 4 checkpoint because an
external explicit stop/start occurred at `2026-07-12T10:35:36-04:00`. The
working directory remains the active legacy checkout. Phase 4B must not
restart or cut over either active service.

**PRE-PROVISION SAFETY GATE: PASS.** Any later mode, gate, kill-switch or
order/trade mismatch stops rollout before spawn.

## PostgreSQL

| Check | Observed |
|---|---|
| Endpoint | `127.0.0.1:55432` |
| Database / read role | `trading_agent / trading_reader` |
| Alembic | `0004_durable_research_jobs` |
| Application tables | `26`, excluding `alembic_version` |
| Canonical / quarantine rows | `43,055 / 222` |
| `jobs` / `job_attempts` / `job_events` | `0 / 0 / 0` |
| `job_artifacts` / scheduler / worker heartbeats | `0 / 0 / 0` |

The protected `trading_jobs` credential file exists outside Git at
`~/.config/trading-agent/postgres-jobs.env`, owned by the current user and mode
`0600`. No application service uses it yet.

## Provisioning surface

- `/opt/trading-agent-phase4`: absent.
- `/opt/trading-agent-research`: absent.
- `/etc/trading-agent` and both manifest subdirectories: absent.
- Job API, worker, scheduler and safety-exporter user units: not installed,
  inactive and not enabled.
- Job API/worker/scheduler protected runtime env files: absent.
- Port `8401`: no listener.
- Only PostgreSQL `127.0.0.1:55432` and dashboard `0.0.0.0:3002` are present
  among the scoped listeners.
- Root filesystem: 1,007 GiB total, 711 GiB available (26% used).
- Passwordless sudo: unavailable. Codex must never request a password; root
  installation requires a generated, reviewable script executed by the user.

## Backup and rollback assumptions

The protected custom-format dump remains available outside Git:

```text
path: /home/thenam176/.local/share/trading-agent-backups/phase4-preapply-20260712T131219Z.dump
owner/mode: thenam176 / 0600
size: 31,823,453 bytes
```

Phase 4B normal rollback is service-forward: timer first, then scheduler,
worker, Job API and safety exporter. It preserves PostgreSQL jobs/events and
artifacts, does not downgrade `0004`, does not restore dashboard process spawn
or `run_status.json`, and does not touch active service definitions, port
3002, Cloudflare, live gates or the canonical kill switch.

## Pre-implementation authority conflict

The audited commits above deliberately contain fail-closed digest constants
and a worker safety provider that does not yet consume the Phase 4B exported
snapshot. Meeting the requested protected-runtime digest and fresh safety JSON
contracts therefore requires reviewed application/backend code changes and
new immutable release commits. Building byte-for-byte releases from exactly
`8eb6d26` and `51de1cf` cannot simultaneously add those behaviors without an
untracked packaging patch, which is forbidden. This decision must be resolved
before production code or release construction.

Operator decision: approved. Phase 4B may advance application/backend commits;
the audited commits remain provenance bases and the final reviewed Phase 4B
commit IDs become the immutable release authority. Packaging-time source
patches and mutable aliases remain forbidden.
