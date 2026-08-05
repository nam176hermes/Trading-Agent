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
