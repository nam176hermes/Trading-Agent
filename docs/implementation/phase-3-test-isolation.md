# Phase 3 Legacy Integration Test Isolation

## Root cause

`predscope_signals.py` and `adanos_signals.py` bound report, signal, and output
paths to the production repository at import time. The standalone integration
harness called both generators, which unconditionally persisted derived JSON to
those paths.

## Resolution

Legacy backend commit `0b977fe` adds one centralized `runtime_paths.py` resolver.
Production defaults remain the repository's existing `reports/` and `signals/`
directories. Tests set `TRADING_DATA_ROOT` to a temporary root before importing
the generators. The harness copies only the latest prediction-market and social
sentiment fixtures into that root and writes derived signal output there.

## Proof

The path-resolution regression test failed before the change and passed after.
The isolated standalone integration suite passed 43/43. Before/after size,
mtime, and SHA-256 comparisons proved both live signal output files unchanged;
report count, decision stat, and logical SQLite signals also remained unchanged.

No existing legacy signal file was reset, checked out, or reverted.
