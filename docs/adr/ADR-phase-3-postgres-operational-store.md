# ADR: Phase 3 PostgreSQL Operational Store

**Status:** accepted on 2026-07-11.

## Context

Phase 2 serves strict canonical responses from read-only JSON, JSONL, and
SQLite adapters. Those sources remain valuable archives but require repeated
filesystem scans, provide no unified operational lineage, and cannot support a
durable migration history. Phase 3 must add an operational store without
changing the API contract, current live-safety authority, or legacy data.

## Decision

Provision PostgreSQL 16 as a dedicated localhost-only native cluster on port
55432. Use Alembic for every schema change and separate owner/migration/read
roles. Store normalized operational entities and explicit source lineage rather
than full legacy payloads.

Define repository protocols for each Phase 2 read domain and select a complete
legacy or PostgreSQL repository bundle at application composition time using
`TRADING_STORE_BACKEND`. Keep current mode, both hard gates, canonical
kill-switch state, live-price heartbeat, and SQLite order/trade counts behind a
separate current-safety provider so historical PostgreSQL snapshots cannot
become live authority.

PostgreSQL unavailability in PostgreSQL mode fails readiness and never causes a
silent legacy fallback. Operators roll back explicitly by setting the backend
to `legacy`.

## Alternatives considered

1. Use staging tables containing full raw payloads before merging. Rejected
   because it duplicates the archive, increases secret/data exposure, and
   encourages PostgreSQL to become a blob dump.
2. Build a generic source-ledger/event store and derive projections. Rejected
   because it overlaps the durable job/event work reserved for Phase 4 and is
   unnecessary for the current dataset.
3. Continue using only legacy adapters. Rejected because it cannot provide the
   required migration history, database constraints, indexed queries, or
   operational lineage.
4. Use Docker Compose. Rejected for this host because Docker and Podman are not
   available in the WSL distro; the reviewed choice is a native PostgreSQL 16
   package.

## Consequences

- PostgreSQL becomes the staged operational source of truth for migrated
  research/control data after reconciliation, while legacy files remain the
  immutable archive and rollback source.
- Current safety facts remain anchored to the Phase 1 fail-closed sources.
- The Control API contract and candidate dashboard do not need storage-specific
  changes.
- An explicit local PostgreSQL package and protected credentials are required.
- The operational schema contains only queryable canonical fields and archive
  references, so future raw-payload needs must be satisfied by the artifact
  layer rather than arbitrary JSONB growth.
- Redis, Celery, scheduler restoration, command APIs, execution state, and live
  trading remain outside scope.
