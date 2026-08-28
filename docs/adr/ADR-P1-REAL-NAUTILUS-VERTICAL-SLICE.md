# ADR: P1 real Nautilus vertical slice

Status: Accepted for paper/local P1.

P1 uses the qualified low-level Cython-v1 `BacktestEngine` behind an
engine-neutral `BacktestSession` seam. Only `engines/nautilus/runtime_v1` may
add new Nautilus imports. A future v2 implementation lives in a separate
package and closure; one process never imports both families.

Root contracts, worker custody, event ledger and portfolio accounting remain
engine-neutral. P1 adds no provider, credential, socket, live adapter or
client-selected executable/profile. The existing launchers are frozen
qualification references; new responsibilities use cohesive product modules.

P1-A creates schema-8 real-backtest authority derived from the approved G1
receipt. P1-B adds a network-free local replay session using the same contracts,
strategy, events and accounting. Legacy Phase4 stays on 1.227/schema 6.
