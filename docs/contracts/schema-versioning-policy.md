# Control API Schema Versioning Policy

Current public schema version: `2.0.0`. The pre-deployment `1.0.0` candidate
represented unavailable current order/trade counts as zero. Version `2.0.0`
makes those fields nullable so historical PostgreSQL observations cannot be
presented as current truth. No `1.0.0` Control API was installed or had a live
consumer, so there is no deployed compatibility window to preserve.

- A backward-compatible optional field addition increments the minor version. Consumers must ignore unknown fields.
- A required field removal/rename, meaning change, type narrowing, or incompatible enum change increments the major version and requires a parallel compatibility window.
- New enum members are treated as a minor change only when consumers have an explicit unknown-state behavior; otherwise they are breaking.
- Patch versions clarify descriptions or constraints without changing accepted/returned values.
- Deprecated fields remain in OpenAPI for at least one minor line and carry a replacement description before removal in a major version.
- Every top-level success/error response includes `schema_version`, `trace_id`, and `generated_at`.
- Legacy spellings such as `STRONG SELL` are normalized only inside a legacy adapter. They never enter the canonical enum.
