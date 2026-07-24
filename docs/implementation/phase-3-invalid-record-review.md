# Phase 3 Invalid Record Review

## Invalid market reports

All 86 invalid `report_*.json` files are valid JSON report-shaped objects but
contain at least one asset missing a required symbol or numeric
`current_price`. They are classified as:

```text
SCHEMA_VALIDATION_FAILED / MISSING_ASSET_REQUIRED_FIELD: 86
```

Representative sanitized references:

| Relative source | Payload SHA-256 | Explanation |
|---|---|---|
| `reports/report_20260513_183659.json` | `d36f7d344e971944770d9ae4caed0bfefb22d246d30452220d424f53613f5948` | an asset is missing symbol or numeric current price |
| `reports/report_20260513_190850.json` | `54fe175a5c20f6f6a7dc7d7ef2e92d38f918394bfc6680d330fab08f80f3c009` | an asset is missing symbol or numeric current price |

No payload body or sensitive field is included. The importer did not repair or
rewrite these sources.

## Invalid decisions under strict Phase 3 policy

The decision JSONL is syntactically valid. Strict normalization rejects 136
records with `INVALID_ENUM`:

```text
WATCH: 122
WATCH FOR EXIT: 14
```

Representative sanitized record evidence is retained in the dry-run JSON as a
one-based record index, payload SHA-256, relative source path, and the message
`decision action is not canonical`. Full decision payloads are not retained.

These values were previously silently mapped to `NO_SIGNAL` by the Phase 2
adapter. The Phase 3 ADR forbids that behavior. They cannot be made valid until
an explicit alias/quarantine decision is approved.
