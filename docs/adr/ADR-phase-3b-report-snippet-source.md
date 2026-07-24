# ADR: Phase 3B Report Snippet Source

## Decision

Use the direct same-record `memory/decisions.jsonl.report_snippet`. 16,516
canonical decisions are `EXACT`; the empty field at record 19 is
`NULL/UNKNOWN` with `SNIPPET_SOURCE_MISSING`.

## Precedence and quality

1. Non-empty explicit decision field: `EXACT`.
2. Explicitly linked stored report/summary/rationale field under a separately
   approved deterministic extraction policy: `DERIVED`.
3. Otherwise: `NULL/UNKNOWN`.

Only item 1 is enabled. Maximum observed length is 500, so Phase 3B performs no
truncation. If a future contract adds a limit, it requires a new normalization
version and deterministic audit.

## Forbidden inference

Do not generate, summarize, paraphrase, combine prose, or fall back to typed
decisions/documents for the one empty source record. No LLM participates.

## Identity and collision

Identity is domain + decision ID + source hash + record index + `phase3b-v1`.
Equal-quality different text records a conflict and preserves the stored value.

## Contract and rollback

The existing string field remains; internal unknown semantics may be rendered
as the contract-compatible empty value at the adapter boundary while storage
remains `NULL/UNKNOWN`. Rollback is legacy mode plus verified dump restore.
