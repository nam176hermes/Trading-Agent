# Foundation PostgreSQL dual-read evidence

Date: 2026-07-23

The first authorized run intentionally stopped before dual-read. The preceding
`make test-runtime-postgres` command found blocking restore owner and
structural-security drift, so package policy required a fail-closed stop.

The later exact authority bound to
`dd1463a80b5a492d6f12b89f9aa69f03ce77416b` superseded that checkpoint. After
restore semantic parity passed, `make test-runtime-dual-read` completed with
one passing test and exit status zero. All disposable fixtures were removed.

This result is limited to the expired disposable authority. It does not
authorize the operator-managed database or a new runtime operation.
