# ADR: Phase 3B Cost Symbol Attribution

## Decision

Use only the first top-level structured `symbols` list in each of the newest 20
scratchpad session JSONLs. Canonicalize through the existing asset registry,
deduplicate, sort, and store normalized session-asset links. Current evidence
produces 200 links and zero unknown assets.

## Precedence and quality

1. Top-level session metadata `symbols`: `EXACT`.
2. Other allowlisted structured paths may be proposed in a future ADR.
3. No matching evidence: explicit unknown, not an invented empty set.

## Forbidden inference

Do not infer from filenames, unrestricted prose, ticker-like tokens, or nested
tool arguments while top-level metadata exists. Unknown symbols are rejected
with `UNKNOWN_ASSET` and never create canonical assets.

## Identity and collision

The evidence identity is session + source hash + record index + `phase3b-v1`;
each link additionally includes canonical asset ID. Same evidence reruns skip.
Weaker evidence cannot overwrite exact attribution; equal-quality different
sets record a conflict.

## Contract and rollback

The public `symbols: list[str]` shape is unchanged. The parent stores an
evidence state to distinguish unknown from evidenced empty, while links support
query/filter behavior. Rollback is legacy mode plus verified dump restore.
