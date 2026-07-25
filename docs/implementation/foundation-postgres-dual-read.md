# Foundation PostgreSQL dual-read evidence

Date: 2026-07-23

The first authorized run intentionally stopped before dual-read. The preceding
`make test-runtime-postgres` command found blocking restore owner and
structural-security drift, so package policy required a fail-closed stop.

The later exact authority bound to
`dd1463a80b5a492d6f12b89f9aa69f03ce77416b` superseded that checkpoint. After
restore semantic parity passed, historical controller output reported a
successful `make test-runtime-dual-read` command. No retained protected artifact
binds that command's exit status, so this report is not durable closure evidence.
Subsequent absence checks are documented separately.

The executable Package 2 gate remains `PENDING_APPROVAL`. The expired
disposable authority does not authorize the operator-managed database or a new
runtime operation.
