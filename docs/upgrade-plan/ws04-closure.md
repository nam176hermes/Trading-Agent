# WS-04 closure evidence

WS-04D is an offline source-acceptance gate for the deterministic fixture and
catalog path. It does not start a worker, create a promotion API, write to a
runtime authority path, or authorize paper or live trading.

The close_ws04_research function accepts only:

- a typed 04A MarketDatasetManifestV1;
- an exact, hash-bound 04C RunBacktest command and result envelope;
- typed 04D evidence for lookahead, recursive replay stability, walk-forward
  folds, fee/slippage scenarios, reference/legacy/Nautilus comparison, and
  provenance.

The closure verifies the existing 04C result boundary before evaluating the six
research gates. It binds the canonical dataset rows to the market-data artifact
and binds the catalog, strategy, configuration, full evidence, result, report,
and source commit into one deterministic closure SHA-256.

Legacy evidence is a required comparator, never an authority: the sole
accepted evidence authority is reference-and-nautilus. Any missing,
non-canonical, mismatched, or failed evidence blocks closure.
