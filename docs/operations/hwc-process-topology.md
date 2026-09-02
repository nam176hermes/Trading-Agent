# HWC future process topology

**Status:** Source design only; deployment and activation held.

## Processes

```text
trading-control-api      127.0.0.1:8400
trading-job-api          127.0.0.1:8401
trading-operator-api     127.0.0.1:8402
dashboard                separate disposable process
job worker/scheduler     independent
paper runtime            independent
safety exporter          independent
```

The Operator API must not be a parent of the paper runtime. The dashboard must
not be a parent of any backend, worker, runtime, or safety exporter. Stopping or
restarting either client must leave all canonical processes and state unchanged.

## State and credentials

The future service reads the existing protected source root
`/home/thenam176/.hermes/crypto-research`. Its requested mode and kill-switch
files remain `.mode` and `.kill_switch`; its append-only command journal is
`/home/thenam176/.hermes/crypto-research/.operator-commands`. No service receives
a broad writable bind to the legacy root. A dedicated service identity and the
minimum writable subpaths remain deployment decisions, not source assumptions.

WEB and CLI bearer values are delivered as separate owner-only credential files.
The service reads them at startup; clients receive only their own file. Clear
requires fresh canonical safety-exporter evidence and an exact expected state
digest. Journal intent, applied record, receipt, state replacement, and clear
tombstone use durable writes. A tombstone rename and its source must be on the
same filesystem so atomic rename semantics hold.

## Start, stop, and rollback

The future dependency order is state/credentials, safety exporter, the three
APIs, workers and paper runtime, then the disposable dashboard. Readiness must
not imply paper-runtime activation. Stop in reverse dependency order, except an
emergency kill-switch remains an independent operator command.

Rollback selects a previously reviewed source release while retaining all
journal and state files. It never clears `.kill_switch`, discards a journal, or
treats an unknown newer record as success. Release Authority v2 integration and
a host-specific install/rollback drill are blockers before any unit or process
activation. This source plan does not create systemd units, users, directories,
credentials, mounts, schedulers, or running services.

## Held verdicts

```text
SYSTEMD_SOURCE=HELD
RELEASE_V2_INTEGRATION=HELD
HOST_QUALIFICATION=HELD
RUNTIME_ACTIVATION=HELD
```

Broker, exchange, live, production, and external-network authority remain false.
