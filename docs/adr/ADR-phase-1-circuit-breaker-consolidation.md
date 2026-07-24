# ADR: Phase 1 Circuit-Breaker Consolidation

## Status

Accepted on 2026-07-11.

## Context

`safety_engine.py` contained two textually identical top-level `check_circuit_breaker` definitions. Python used only the second because it shadowed the first. The function in `risk_engine.py` is a separate portfolio-loss policy and was not renamed in Phase 1.

## Decision

Remove the first duplicate and retain the previously effective second implementation. Preserve default thresholds: portfolio panic `-15%` and single-asset move `-30%`. Preserve the returned `triggered` and `detail` contract.

## Evidence

AST regression coverage proves exactly one top-level definition remains. A fixture at `-31%` proves the single-asset threshold and detail remain unchanged.
