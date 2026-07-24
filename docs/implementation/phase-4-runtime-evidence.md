# Phase 4 Runtime Evidence

## Current runtime disposition

The schema is deployed, but the Phase 4 application services are not. This is
an intentional fail-closed stop, not a completed runtime rollout.

| Surface | Current evidence |
|---|---|
| PostgreSQL | `127.0.0.1:55432`, revision `0004_durable_research_jobs` |
| Phase 4 queue data | All six Phase 4 tables contain zero rows |
| Job API | unit not installed, inactive; no listener on `127.0.0.1:8401` |
| Worker | unit not installed, inactive; no worker heartbeat |
| Scheduler service | unit not installed, inactive |
| Scheduler timer | unit not installed, inactive/not enabled |
| Latest enqueued/completed job | none |
| Queue depth | `0` |

The protected pre-apply backup and restore evidence is recorded in
[phase-4-test-evidence.md](phase-4-test-evidence.md). The runtime database has
26 application tables excluding `alembic_version`, retains 43,055 canonical
rows and 222 quarantine rows, and has zero jobs, attempts, events, artifacts,
scheduler heartbeats and worker heartbeats.

## Preserved active services

Read-only checks preserved the agent identity and detected one explained
external dashboard restart:

| Service/surface | Pre-change | Current observation |
|---|---:|---:|
| `trading-agent.service` | PID `4181928`, active | PID `4181928`, active/running |
| `trading-dashboard.service` | PID `4183789`, active | PID `1485234`, active/running after external manual restart |
| Dashboard listener | Next PID `4183820`, `0.0.0.0:3002` | Next PID `1485280`; address/port unchanged |
| Cloudflared | PID `3283180` | unchanged |

Phase 4 did not issue a dashboard restart or cutover. The user journal records
an external explicit stop/start at `2026-07-12T10:35:36-04:00`; `NRestarts=0`,
so this was not an automatic service crash restart. The active working
directory remained `/home/thenam176/.hermes/trading-agent`, port 3002 remained
unchanged, and the isolated candidate was not deployed. The isolated dashboard
branch ending at
`843d449` contains the BFF/UI implementation and passed its recorded tests,
typecheck, lint, build and bundle scan. It has not been merged into or deployed
over the active dashboard.

## Safety and audit evidence

The last accepted safety baseline remains paper/paper, both live gates false,
canonical kill switch `INACTIVE`, and orders/trades `30/0`. The stable agent
PID plus absence of a worker/scheduler prove Phase 4 did not run a research
child through the new boundary. The Phase 1 safety regression passed 85 tests
with 2 intended connectivity skips.

During early isolated-dashboard handler tests, the test root was not set soon
enough and 11 authentication events were appended to the active append-only
audit: nine `jobs.create` and two `jobs.cancel`. They were preserved rather
than rewritten or deleted. Follow-up tests isolated their audit root and
confirmed this file remained stable:

```text
path: /home/thenam176/.hermes/crypto-research/memory/dashboard_mutation_audit.jsonl
mode: 0600
size: 3,081 bytes
sha256: 3ed19740f3177dda1e9a940c32e95265874de2bb59439813b29925cc8fac1d40
preserved appended events: 11
```

The incident did not change `run_status.json`, orders or trades, but it is a
real legacy write and remains disclosed. It must not be erased to manufacture
a clean audit history.

Final isolated-dashboard verification preserved the following active legacy
file hashes:

```text
run_status.json:     465457bb4064881137d36d132e5b7463c905fca065115e5720ad1f35a3c76d66
orders.jsonl:        18c517c01e880fb6c2f5e42ffe037f9ac30e096494e6623e33095aa8d8eeebd0
trade_journal.jsonl: c9c335f473360dcf8feb776838bb27d493c293cd2188ba9e7024a008adb06130
```

The active audit hash, these three hashes, port 3002 and the active service
PIDs remained unchanged during the earlier dashboard 69-test plus integration,
typecheck, zero-finding lint and production-build verification. During the
later 559-test Python suite, the active public dashboard independently appended
one `auth.status` `CONFIGURATION_ERROR` event at `2026-07-12T13:58:09.053Z`.
At that checkpoint the append-only file had 17 lines, mode `0600`, size 3,259
bytes and SHA-256
`c0231ba2bb39a456e9d56408188f7645a83742f6261872cdc7a4597019a38afb`.
No Phase 4 Python test imports or writes this dashboard audit path; the event
is recorded as an explained external runtime change and is not removed.

The later `843d449` dashboard boundary hardening passed 67 tests plus isolated
integration, typecheck, zero-warning lint, production build and source/bundle
scans. Its integration server used a random loopback port plus temporary HOME
and research roots. Concurrently, the externally restarted active dashboard
appended ten additional `auth.status` authorization/configuration events. The
final audit has 27 lines, mode `0600`, size 4,977 bytes and SHA-256
`777733cd48a56b759a7fb41ffef5c2c336e45eb1e747bb632753084f5c49f64c`.
Those external append-only records are preserved. The candidate remains
undeployed.

## Runtime blockers

- The root-owned immutable application and backend releases are absent.
- The externally reviewed release manifest and pinned digest are absent; the
  command registry therefore fails closed before spawn.
- The reviewed backend's external semantic-input manifest and pinned digest
  are absent, so semantic inputs also fail closed if command attestation is
  later provisioned.
- No approved least-privilege runtime environment file has been installed for
  the units.
- `systemd-analyze --user verify` exits nonzero because the fixed release
  interpreter under `/opt/trading-agent-phase4/releases/phase4-0001` is absent.
- Job API smoke, worker heartbeat/safety smoke, scheduler heartbeat observation
  and service rollback have not run.
- The final repository suite passed 559 tests, the backend offline suite
  passed 178 with 2 intended skips, isolated integration passed 43/43, the
  Phase 1 safety target passed 85 with 2 intended connectivity skips, and the
  static contract/Alembic chain passed.

Current runtime decision: **NO-GO — PHASE 4 JOB DURABILITY, SAFETY, OR
SCHEDULER BLOCKERS REMAIN.** This decision does not authorize live trading.
