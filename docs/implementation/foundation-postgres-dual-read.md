# Foundation PostgreSQL dual-read evidence

Date: 2026-07-26

```text
make test-runtime-dual-read
exit=0
1 passed in 2.10s
output_sha256=6d3df4651c81643219bf44822526de9bee8ca9e105a88658ca597bf747c1fff2
```

This command ran only after runtime PostgreSQL and restore parity returned exit
`0`. It compared canonical PostgreSQL reads with reviewed legacy fixtures after
removing approved nondeterministic envelope fields. No unexplained difference
remained, and the required runner rejected skips.

The approved slot used port `56523`. Post-command reconciliation found no root,
listener or PostgreSQL process. Exact argv, output digest, exit code and cleanup
state are bound by the archived manifest.

```text
PASS - POSTGRESQL DUAL READ PARITY VERIFIED
```
