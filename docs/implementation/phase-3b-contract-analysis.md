# Phase 3B Contract Analysis

## Public contract

No OpenAPI or JSON Schema shape changed. `DecisionRecord.price_at_decision`
remains numeric, `report_snippet` remains a string, and cost-session `symbols`
remains `list[str]`. Internal provenance, evidence, run, and lineage tables are
not exposed.

The one source-empty snippet remains `NULL/UNKNOWN` in PostgreSQL and is
rendered as the pre-existing contract-compatible empty string. Every canonical
decision price has exact evidence, so no nullable public price change is
required.

Legacy and PostgreSQL cost adapters now share deterministic uppercase,
deduplicated, sorted symbol normalization. PostgreSQL values originate only
from registry-validated structured links.

## Drift check

`uv run python scripts/generate_contracts.py --check` exited successfully.
Generated OpenAPI, JSON Schemas, TypeScript API types, and Zod schemas did not
change. Generator deprecation warnings are pre-existing and do not represent
contract drift.

No v2 ADR is required.
