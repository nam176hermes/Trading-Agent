# Foundation PostgreSQL approval evidence

Date: 2026-07-23

## Decision

The first successor approval passed, but its restore-parity attempt did not
close Package 2. The original sections below preserve that historical
checkpoint. A later exact authority retained successful restore-semantic
evidence, but the retained records do not include an immutable transcript that
binds every final command result. It supersedes the failed restore-semantic
result, not the executable Package 2 status. Production PostgreSQL access and
live trading remain unauthorized.

## Successor authority

- Source commit: `48c2999ba386db51c5a0f22809dc9bc3d5653cc1`
- Source tree: `31d47719fbf079f86bfdd8dd96d58ed4e44f4731`
- Operator: `thenamnguyen`
- Independent reviewer: `hermes.agent`
- Reviewer result: `APPROVED`, no source blockers
- Approval record digest:
  `722d8cd458b44c5fbb2b19ba1fa3b203b43fdafba064c91f09f21857b989a28d`
- Fixture-plan digest:
  `f086c51dfef80f8b38410f972dfb9e63db50ffe0491b18418ef4e588613c7a70`
- Validity: `2026-07-23T18:51:26Z` through
  `2026-07-23T22:51:26Z`
- Protected external record:
  `/tmp/trading-agent-p02-approval-48c2999/disposable-postgres-approval.json`
- Protected external plan:
  `/tmp/trading-agent-p02-approval-48c2999/disposable-postgres-fixture-plan.json`
- Both protected files were regular, same-user files with mode `0600`.

The approval validator returned:

```text
VALID: disposable PostgreSQL authority record matches
```

The fixture validator returned nine exact slots and matched the successor
commit, tree, approval digest, validity window and Greenlight.

## Approved boundary

- Bind: `127.0.0.1`
- Ports: `56420` through `56428`
- PGDATA roots: `/tmp/phase4-postgres-p02-48c2999-01/data` through
  `/tmp/phase4-postgres-p02-48c2999-09/data`
- Database: `trading_agent_disposable_test`
- Lifecycle: `INITDB`, `START`, `RESTORE`, `STOP`, `DELETE`
- Forbidden ports: `3002`, `8401`, `55432`
- Runtime database settings: rejected if present

The approval and fixture plan are tied to the source above. They do not
authorize any later source commit.

## Later authority and retained proof

- Final source commit: `dd1463a80b5a492d6f12b89f9aa69f03ce77416b`
- Final source tree: `d8b1983fef30ef97439aa94cd7db2820a66353b6`
- Record ID: `DISPOSABLE_POSTGRES_TEST_20260724_P02_DD1463A`
- Validity: `2026-07-24T01:57:17Z` through
  `2026-07-24T05:57:17Z`
- Protected approval record:
  `/tmp/trading-agent-p02-approval-dd1463a/disposable-postgres-approval.json`
- Approval record digest:
  `d5de9aba700505127dab11eca1979bb16a760b3a97711bc11843f76cc86e74e8`
- Approval file SHA-256:
  `0c35a19176807f6f1e780bce306d5c910754004d010bedead30623e5c059d78d`
- Protected fixture plan:
  `/tmp/trading-agent-p02-approval-dd1463a/disposable-postgres-fixture-plan.json`
- Fixture-plan digest:
  `1ba8974706d6cbe642769144a136f8ff4a9d10d8a442d6f143582a8e51bc7482`
- Fixture-plan file SHA-256:
  `1837362d3b3c66ab006e522b7d5f18a117fa8d50356d08c802878172d909b9f8`
- Protected semantic evidence:
  `/tmp/foundation-postgres-evidence-dd1463a/catalog-restore-semantic-evidence.json`
- Semantic-evidence file SHA-256:
  `22dd5c215741036a3a4b1db97790f6bc0a6fcfaefe581ace01c7e227399283db`
- Operator: `thenamnguyen`
- Reviewer: `hermes.agent`
- Approved lifecycle: `INITDB`, `START`, `RESTORE`, `STOP`, `DELETE`
- Greenlight decision: `APPROVED` at `2026-07-24T01:57:17Z` for that exact
  lifecycle and authority only
- Bound interface: `127.0.0.1`, ports `56420` through `56428`
- Forbidden ports: `3002`, `8401`, `55432`
- Runtime settings: rejected if present
- Current-tree binding audit: all ten source-bound files are byte-identical
- All three retained files were regular same-user files with mode `0600` when
  re-audited on 2026-07-25.

The authority binds eight approved operation IDs to exact test paths, and the
fixture Greenlight binds the complete lifecycle above. Historical controller
output reported successful event-ledger, runtime and dual-read commands, but no
retained protected artifact contains their exit statuses or complete command
transcript. The previously reported `5`, `8`, and `1` passing-test counts are
therefore not used as durable closure evidence.

The retained semantic evidence independently records
`semantic_groups_equal=true`, `row_counts_equal=true`, and no differing
semantic subgroup. The cleanup document records subsequent absence checks, but
those observations do not replace a bound lifecycle transcript.

```text
PENDING_APPROVAL - DISPOSABLE RESTORE SEMANTIC PROOF RETAINED;
CANONICAL RUNTIME_POSTGRES_PARITY NOT CLOSED
```

This authority was source-bound and expired after the run. It cannot authorize
a retry, a different commit, the operator-managed database, or any production
mutation.
