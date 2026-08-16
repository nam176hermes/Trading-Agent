# P0 maintainability responsibility boundaries

The frozen P0 hotspots have narrow, reviewed responsibilities. Their approved
first-party imports are pinned in `p0-maintainability-hotspots.json` from the
AST of the exact `baseline_sha` Git blobs. The maintainability checker rejects
any current first-party import not present in that reviewed baseline; a future
dependency requires a deliberate manifest update and code review.

## `scripts/t_g03_capability_topology.py`

Owns P0 test-governance topology; portable, native, and external lane
orchestration; capability and authority classification; P0 receipts and their
validation; P0 native candidate acceptance mechanics; P0 semantic-result
projection; and P0 topology evidence aggregation.

It does not own strategy algorithms, market-data ingestion, real Nautilus
backtest or paper execution, broker or exchange APIs, portfolio optimization,
LLM reasoning, or quant training.

## `scripts/check_artifact_firewall.py`

Owns P0 evidence-tree validation, manifest and checksum validation, artifact
path and custody checks, secret-sensitive evidence screening, and portable
evidence publication validation.

It does not own trading-domain validation, market data, strategy, order
lifecycle, or Nautilus runtime behavior.

## `scripts/check_p0_ci_closure.py`

Owns historical P0 closure and qualification proof. It must not become a
generic P1/P2 qualification engine.
