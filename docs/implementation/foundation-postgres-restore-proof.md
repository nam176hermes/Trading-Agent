# Foundation PostgreSQL restore proof

Date: 2026-07-23

## Result

`make test-runtime-postgres` exited `2`: `7 passed`, `1 failed`, with one
pre-existing Starlette/httpx deprecation warning.

The custom-format `--create` dump and `pg_restore --create --exit-on-error`
completed on separate source/target disposable clusters. The blocking
comparison then found:

```text
owner source   = 65806ce57ee6df7a20019f23a8726ad29f00e8eaa0e4670c222aeb8c205fdc39
owner restored = 464fe0f99549322f3b09bb5eb7d174049fbac368ffd7fac502e53552494a1892

structural_security source   = 2f9c28bc687399e3cc4e704961de32ce9f055597b747c463892db9384f4ffacd
structural_security restored = b9228c38fccc89923301c3ee182915f430783205c0b0b546c1c52d181fffa79c
```

Four other blocking semantic groups were equal: identity, effective ACL,
functions, and triggers/policies. Equality of those groups does not waive the
owner and structural blockers.

The sanitized success evidence file was not created because the blocking
assertion occurs before success publication. No closure matrix was changed to
PASS.

## Decision

`NO-GO — RESTORE SEMANTIC CATALOG PARITY FAILED`

The next investigation must identify exact differing owner and structural row
types without weakening the blocking comparisons. Any source change requires
a new commit-bound approval and Greenlight.
