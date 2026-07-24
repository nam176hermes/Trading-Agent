# Phase 4 Command Allowlist

## Code-owned registry

The worker maps the closed `JobType` enum to fixed `CommandSpec` values. A
client cannot supply executable, module, command, argv, shell, cwd,
environment, output path, timeout or retry count.

| Type | Fixed backend mode | Typed client contribution | Timeout | Max attempts | Validator |
|---|---|---|---:|---:|---|
| `SNAPSHOT` | `snapshot --research-only` | fixed default scope only | 900 s | 2 | `legacy-report-v1` |
| `DEBATE` | `debate --research-only` | registered asset | 1,200 s | 1 | `legacy-report-v1` |
| `REPLAY` | `replay --research-only` | validated session ID | 120 s | 1 | `legacy-replay-v1` |
| `BACKTEST` | `backtest --research-only` | registered asset and fixed legacy strategy | 900 s | 1 | `legacy-report-v1` |

The final argv starts with the exact release interpreter and `-I -B`, uses a
fixed `main.py --mode ... --research-only` prefix and appends only values from
strict payload models. Symbols canonicalize through the fixed asset registry;
session identifiers use a narrow ASCII contract. Metacharacters, newlines,
paths, extra flags and unknown fields are rejected by validation even though
no shell is involved.

## Release attestation

The currently reviewed backend identifier is
`51de1cf06b3d595a336e19390230d0c09b608585`. The intended release root and
external manifest are under `/opt/trading-agent-research/releases/` and
`/etc/trading-agent/releases/`. Attestation performs a no-follow exact-set
walk, hashes every file, rejects links, extra files, unsafe ownership/mode and
extended attributes, and issues only short-lived single-use capabilities.

Those root-owned artifacts are not provisioned and the approved release
manifest digest remains intentionally `None`; therefore every real command
preparation fails closed. The reviewed backend also requires a separate
root-owned, digest-pinned semantic-input manifest; its digest is intentionally
`None` until a fresh bounded input snapshot is provisioned.

No service has executed an allowlisted real-data command. The release and
manifest blocker is separate from, and additional to, the canonical dynamic
safety-sentinel blocker described in [worker](phase-4-worker.md).

References: [command-allowlist ADR](../adr/ADR-phase-4-command-allowlist.md),
[artifact ADR](../adr/ADR-phase-4-job-result-artifacts.md), and
[attribution sync](phase-4-worker-attribution-sync.md).
