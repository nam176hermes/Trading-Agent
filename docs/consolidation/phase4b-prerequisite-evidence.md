# Phase 4B Consolidation Prerequisite Evidence

Captured read-only at `2026-07-13T16:48:47-04:00` before canonical source
imports. Overall prerequisite result: **PASS**.

## Installed authority

| Check | Result |
|---|---|
| Provisioning process | `PASS` - absent |
| Rejected root residue | `PASS` - absent |
| Installed authority directory | `PASS` - present |
| Sealed metadata SHA-256 | `f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c` |
| Installed verifier final result | `PASS` - `phase 4b installed authority verification passed` |

## Safety invariant

| Check | Result |
|---|---|
| Runtime process/service checked | `trading-agent.service` |
| Checked data root | `/home/thenam176/.hermes/crypto-research` |
| Requested / effective mode | `paper / paper` |
| `LIVE_EXECUTION_ENABLED` / `LIVE_TRADING_APPROVED` | `false / false` |
| Canonical kill switch | `INACTIVE` - sentinel absent |
| Orders / trades | `30 / 0` - read-only query of `/home/thenam176/.hermes/crypto-research/memory/trading.db` |
| Port applicability | `not applicable / not probed by this gate` |

The live-gate check selected only the two named values from the active agent
environment. No PID, other environment value, database row, DSN, credential,
token, or protected file content is recorded here. No runtime, service,
systemd, database, order, or trade state was changed.

## Fixed consolidation authority

| Component | Commit | Tree |
|---|---|---|
| Core application/ops | `d9d46fa363f26bd78f5560300d26913494e11e4d` | `bfac951424d09f21359fcc11abb0bbe000456b4e` |
| Research backend | `41f055b48033714c660f44cc20498b7545366e75` | `b15af11d8600e042e20403dba982a3c1bc1b4b60` |
| Dashboard source prefix | `ca57a7e018eb3afdc263e40b343b7ebbe3f8ccbb` | `3246350253575256b0566cfd54076e8e8ce0412e` |

These identities matched the approved consolidation authority at capture
time.
