# WS-02 Packet 02A implementation report

Status: **DONE**

Branch: `codex/nt-ws02-engine-bridge`

Implementation commit: `4c8b191` (`feat(engine): define v1 bridge contracts`)

The report itself is committed separately after authoring; its commit hash is
included in the final handoff because a Git commit cannot contain its own hash.

## Scope and safety

Implemented only packet 02A contract definitions. The change adds no CLI,
worker, ingestion, service, database, migration, broker, exchange, network, or
live-execution path. It adds no dependency and changes no lockfile. Validation
was source/schema-only and paper-safe.

No pre-existing `.superpowers/sdd` file or report was modified. This requested
task report is the only new SDD report artifact.

## Design and public API

Created `packages/engine_contracts/` with the required modules:

- `commands.py`: a closed discriminated v1 command union containing exactly the
  17 requested commands, `COMMAND_TYPES`, an immutable registry, and
  `parse_command`. There is no live command. Configuration inputs use neutral,
  content-addressed `ArtifactReference` values; target portfolio and order
  commands reuse the canonical engine-neutral domain DTOs.
- `events.py`: the closed `EventFamily` classification for engine lifecycle,
  market-data continuity, strategy lifecycle, order lifecycle, fills,
  positions, account state, runtime risk, reconciliation, health, and halt.
  `EngineEvent` carries only bounded immutable scalar attributes, not an engine
  class or provider payload.
- `envelopes.py`: concrete command and event envelopes. Both require message,
  correlation, causation, engine-run identities, positive sequence, canonical
  event and initialization times, schema version, producer identity, source
  commit, config digest, payload digest, and payload. The models verify timeline
  and payload digest invariants. `validate_envelope_batch` rejects duplicate
  message IDs and non-increasing per-run sequences.
- `capabilities.py`: immutable versioned engine capabilities with only BACKTEST
  and PAPER modes, closed command names and event families, and duplicate-claim
  rejection.
- `manifests.py`: immutable content-addressed run manifests and the closed
  transport artifact-name family.
- `serialization.py`: canonical compact UTF-8 JSON, SHA-256 payload digests,
  canonical UTC `Z` timestamps, commit/digest constraints, and producer identity
  constraints. Raw floats and unsupported JSON values are rejected.
- `versions.py`: the sole public contract version authority, `1.0.0`.

All Pydantic models use v2 strict, frozen, `extra="forbid"`, and instance
revalidation conventions. UUIDs identify protocol entities. Public schemas and
exports contain no Nautilus class, implementation import, provider-specific
payload, or live command/mode.

## Generated public contracts

Registered only these four top-level public outputs in the canonical generator:

- `generated/engine/json-schema/EngineCapabilities.json`
- `generated/engine/json-schema/EngineCommandEnvelope.json`
- `generated/engine/json-schema/EngineEventEnvelope.json`
- `generated/engine/json-schema/EngineRunManifest.json`

Supporting command, event, artifact, and domain models remain nested `$defs`;
they are not emitted as extra top-level files. Generated files were produced by
`make generate-contracts` and were not hand-edited.

## Files touched

- `packages/engine_contracts/__init__.py`
- `packages/engine_contracts/commands.py`
- `packages/engine_contracts/events.py`
- `packages/engine_contracts/envelopes.py`
- `packages/engine_contracts/capabilities.py`
- `packages/engine_contracts/manifests.py`
- `packages/engine_contracts/serialization.py`
- `packages/engine_contracts/versions.py`
- `scripts/generate_contracts.py`
- the four generated engine schema files listed above
- `tests/engine_contracts/test_commands.py`
- `tests/engine_contracts/test_events.py`
- `tests/engine_contracts/test_envelopes.py`
- `tests/engine_contracts/test_capabilities_manifests.py`
- `tests/engine_contracts/test_contract_generation.py`
- this requested report

## TDD evidence

Tests were written and observed failing before their production behavior:

- command family/parser RED: `4 failed`, missing
  `packages.engine_contracts`; GREEN: `4 passed`;
- envelope/serialization RED: `15 failed`, missing envelope/digest API; GREEN:
  `15 passed`;
- event family/envelope RED: `4 failed`, missing event API; GREEN: `4 passed`;
- capability/manifest RED: `7 failed`, missing capability/manifest API; GREEN
  after implementation/refinement;
- canonical generator RED: `3 failed`, engine schema directory absent; GREEN:
  `3 passed`;
- backtest UTC-Z RED: `1 failed` because `+00:00` was accepted; GREEN after the
  shared canonical timestamp primitive was applied.

Required negative behavior is covered for unknown fields, unknown version,
invalid/noncanonical timestamps, invalid/non-strict/non-positive sequence,
duplicate message ID, payload digest mismatch, unsupported command, duplicate
capability/manifest entries, absence of live commands/modes, and absence of
provider/Nautilus schema leakage.

## Fresh validation results

All commands below ran from repository root and exited 0:

```text
make generate-contracts
  canonical OpenAPI/TypeScript generation completed; four engine schemas written

uv run pytest -q tests/engine_contracts
  34 passed in 11.22s

make check-contracts
  canonical temporary render byte-compared cleanly; no stale output

uv run pytest -q tests/domain/test_contract_generation.py
  50 passed in 10.29s

uv run python -m compileall -q packages/engine_contracts scripts/generate_contracts.py tests/engine_contracts
  exit 0, no output

rg -ni "nautilus|binance|coinbase|kraken|provider_payload|startlive|liveengine" packages/engine_contracts generated/engine
  no matches

git diff --check
  exit 0, no output
```

## Self-review findings and known concerns

- Verified the registry and generated command union contain exactly the 17 v1
  names and no live command.
- Verified only concrete command/event envelopes are schemas; the public
  `EngineEnvelope` annotation is their union rather than an invalid instantiable
  base model.
- Verified canonical UTC-Z validation is shared by envelopes, backtest windows,
  and manifests, including JSON input and output.
- Verified digest validation hashes the fully validated canonical payload and
  duplicate detection operates across a batch.
- Verified all four top-level generated schemas are strict and generator drift
  checks remain compatible with the existing domain schema catalog.
- No blocking concern is known. Event attributes are intentionally bounded,
  engine-neutral scalar facts in 02A; concrete adapter behavior, file transport,
  CLI, worker integration, and durable ingestion remain out of scope for later
  packets.

## Fix round 1/5 — nested submission contract hardening

Status: **DONE**

Fix implementation commit: `0861283` (`fix(engine): harden nested submission contracts`)

### Findings addressed and rationale

1. Replaced direct `TargetPortfolio` and `OrderIntent` object exposure in
   `SubmitTargetPortfolio`, `SubmitOrderIntent`, and `ModifyOrderIntent` with
   engine-contract-owned DTOs: `EngineTargetPortfolio`,
   `EngineTargetPosition`, `EngineOrderIntent`, `EngineInstrumentId`,
   `EngineQuantity`, and `EnginePrice`. Every object is a strict, frozen
   Pydantic v2 model with `extra="forbid"`. This preserves the neutral wire
   fields and semantic invariants while ensuring recursive JSON Schema
   `additionalProperties: false`; no shared domain DTO was broadened or changed.
2. Applied `CanonicalUtcDateTime` and `SchemaVersion` directly to nested target
   and order submission authority fields. `effective_at` and `requested_at`
   now require UTC `Z` JSON spellings, and nested `schema_version` is exactly
   `1.0.0` for submit and modify paths.
3. Strengthened the generated union test to compare the complete discriminator
   object (including exactly 17 mapping keys and refs) and the ordered `oneOf`
   reference list against the literal closed command family. The earlier
   intersection/count check could not detect every extra or incorrect mapping.

The canonical generator removed the old nested object definitions
`InstrumentId`, `OrderIntent`, `Price`, `Quantity`, `TargetPortfolio`, and
`TargetPosition` from `EngineCommandEnvelope.json`. Their six engine-contract
replacements are recursively strict. Enums and canonical decimal scalar policy
remain shared engine-neutral primitives, not exposed domain object DTOs.

### TDD evidence for the fix

- Nested authority RED: portfolio/order `+00:00` timestamps and nested
  `2.0.0` versions produced `DID NOT RAISE`; after the wrapper change all target,
  submit-order, and modify-order negative cases pass.
- Recursive schema RED: the generated-schema assertion failed with missing
  `EngineOrderIntent` (the old schema still referenced domain object defs).
  After canonical regeneration, all six engine DTO defs have
  `additionalProperties: false`, old object defs are absent, and timestamp/version
  constraints are present.
- Nested unknown-field tests initially observed dataclass
  `unexpected_keyword_argument` errors. The engine DTOs now produce the intended
  Pydantic `extra_forbidden` errors for portfolio instruments and order
  instrument/quantity/price objects in both submit and modify commands.
- The exact discriminator/`oneOf` assertion passes against the already-correct
  17-command generated union and now protects it from extra, missing, misrouted,
  or reordered entries.

### Files amended

- `packages/engine_contracts/commands.py`
- `packages/engine_contracts/__init__.py`
- `tests/engine_contracts/test_commands.py`
- `tests/engine_contracts/test_contract_generation.py`
- generated `EngineCommandEnvelope.json` via the canonical generator
- this report appendix

### Fresh fix validation

All commands ran from repository root and exited 0:

```text
make generate-contracts
  canonical generation completed; EngineCommandEnvelope.json regenerated

uv run pytest -q tests/engine_contracts
  48 passed in 23.11s

make check-contracts
  canonical temporary render byte-compared cleanly; no stale output

uv run python -m compileall -q packages/engine_contracts tests/engine_contracts
  exit 0, no output

git diff --check
  exit 0, no output

rg -ni "nautilus|binance|coinbase|kraken|provider_payload|startlive|liveengine" packages/engine_contracts generated/engine
  no matches
```

### Fix self-review and known concerns

- Confirmed all three submission command classes reference only engine-contract
  object DTOs.
- Confirmed recursive runtime rejection and generated-schema closure at every
  object level named in the finding.
- Confirmed target, submit-order, and modify-order paths reject both offset
  timestamp JSON and version `2.0.0`.
- Confirmed the discriminator mapping and ordered `oneOf` refs match exactly the
  requested 17-command family, with no live command.
- No blocking or known residual concern remains for fix round 1/5.
