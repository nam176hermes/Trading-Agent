# WS-04 closure evidence

WS-04D is an offline source-acceptance gate for the deterministic fixture and
catalog path. It does not start a worker, create a promotion API, write to a
runtime authority path, or authorize paper or live trading.

The close_ws04_research function accepts only:

- a typed 04A MarketDatasetManifestV1;
- an exact, hash-bound 04C RunBacktest command and result envelope;
- a ResearchEvidenceArtifactReference selecting one externally supplied 04D
  evidence artifact for lookahead, recursive replay stability, walk-forward
  folds, fee/slippage scenarios, reference/legacy/Nautilus comparison, and
  provenance.

The artifact root must be external to the checkout, owned by the current
runtime user, mode 0500, and non-symlink. The referenced artifact must be one
owned mode-0400 regular file. The resolver opens that selected file with
no-follow semantics, rechecks its inode and size, verifies the declared
SHA-256, requires canonical JSON, and only then parses the strict evidence
model. Arbitrary in-memory evidence cannot close WS-04.

The artifact body binds each research observation, fold, scenario, and
comparator to the exact four-input 04C digest. It also includes a canonical
analysis-output trace digest. The Nautilus comparator binds the validated
engine-event digest, while closure verifies the exact 04C result, data
manifest, source commit, and all request artifact hashes.

The closure verifies the existing 04C result boundary before evaluating the six
research gates. It separately binds the canonical dataset-row projection and
the hash-bound market-data artifact; the isolated 04C validator verifies their
row relationship. It then binds the catalog, strategy, configuration, full
evidence, result, report, and source commit into one deterministic closure
SHA-256.

This is immutable attestation evidence, not a computation engine or promotion
authority. Legacy evidence is a required comparator, never an authority: the
sole accepted evidence authority is reference-and-nautilus. Any missing,
non-canonical, mismatched, or failed evidence blocks closure.
