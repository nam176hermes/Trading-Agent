# Foundation PostgreSQL dual-read evidence

Date: 2026-07-23

`make test-runtime-dual-read` was intentionally not run. The preceding
`make test-runtime-postgres` command found blocking restore owner and
structural-security drift. Package policy requires stopping at that point.

No dual-read parity claim is made. Difference classification and counts remain
pending a separately reviewed source correction and new exact disposable
approval.
