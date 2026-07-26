# Foundation event-ledger PostgreSQL runtime evidence

Date: 2026-07-26

```text
make test-event-ledger-runtime-postgres
exit=0
5 passed in 2.21s
output_sha256=0c0d01c6791f72b842d2b91f808869253f5c76ff750ad7a6d35c4087f14b00aa
```

The disposable run covered canonical vectors, forged-snapshot rejection,
idempotent retry, publication retention, permanent inbox claims, append-only
events, role denials, atomic failure and deterministic replay. The required
runner rejected skips.

The operation used the approved slot on port `56528`. Its root, listener and
PostgreSQL process were absent after execution. The output is sealed in
transcript SHA-256
`e9448aa32d4f620e7f59fe3f4bb7a5cba42ff40dadcdbff6ab7d7506f410128c`.

```text
PASS - EVENT LEDGER POSTGRESQL RUNTIME VERIFIED
```
