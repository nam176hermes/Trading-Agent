# ADR 0003: Event ledger delivery durability

- Status: Accepted for source policy
- Operational status: Runtime Authority not activated
- Date: 2026-07-22
- Owners: Trading Agent source and release maintainers

## Context

Revision `0008_trading_domain_ledger` creates one immutable domain event and one pending outbox row in the same transaction. Its original exact-retry query joins `domain_events` to `event_outbox`. That makes append identity depend on pending delivery state. A publisher that removes the outbox row after successful delivery would make the same append request look conflicting instead of idempotent.

The inbox primary key prevents a duplicate only while the row exists. Deleting an inbox row would reopen the claim. Runtime Authority must not be activated while either identity can disappear or while role ownership is ambiguous.

## Decision

### Outbox lifecycle

The outbox uses the **retention/delete model**.

`event_outbox` contains pending publication work only. Its rows are never updated. A delete trigger requires a matching durable publication receipt, so direct deletion cannot retire pending work. A successful publisher acknowledgement performs one atomic database operation:

1. insert the immutable `event_publications(event_id, published_at)` receipt;
2. delete the matching pending `event_outbox` row.

The acknowledgement is idempotent. A repeated acknowledgement returns false and does not recreate or alter either record. Missing pending work without a publication receipt is an error.

The external publish request must use `event_id` as its idempotency identity. If a process crashes before acknowledgement, the pending row remains and publication can be retried with the same identity. If it crashes after acknowledgement commits, the durable receipt proves completion and the pending row is absent.

### Exact append retry

`event_append_idempotency(event_id, request_digest)` is immutable and retained for the lifetime of its referenced domain event. The request digest covers every append argument, including canonical event bytes, topic and canonical payload bytes.

`append_domain_event` checks this authority before consulting delivery state. Therefore:

- exact retry returns false while the event is pending;
- exact retry returns false after publication and outbox deletion;
- any changed append argument for the same `event_id` is rejected;
- retry never recreates retired outbox work.

`event_outbox` and `event_publications` are delivery state. Neither is append identity authority.

### Inbox retention

`consumer_inbox(consumer, event_id, claimed_at)` is append-only. Claims are retained for the same lifetime as `domain_events`. Revision 0008 defines no inbox cleanup, expiry or release operation.

The `(consumer, event_id)` primary key is permanent. Exact repeated claims return false. Update, delete and truncate are rejected by immutability triggers, including owner-issued ordinary DML. Runtime roles cannot disable triggers, alter or drop retained tables, or bypass the fixed claim contract. Owner repair authority remains migration-only and outside Runtime Authority. A future retention policy requires a new forward-only ADR and migration that introduces a separate durable anti-claim tombstone before deleting any inbox row.

## ACL matrix

Revision 0008 records this matrix but grants no Runtime Authority. Activation requires a separately reviewed migration, exact database roles and disposable PostgreSQL privilege proof.

| Principal | Allowed later | Explicitly denied |
|---|---|---|
| Owner | Migration, DDL, reviewed repair and retention administration | Application traffic and routine publication or consumption |
| Writer | Execute canonical append and snapshot functions; minimum SELECT/INSERT needed by SECURITY INVOKER functions | UPDATE/DELETE/TRUNCATE on ledger, idempotency, outbox, publication, inbox and snapshot tables |
| Publisher | SELECT pending outbox rows; execute the fixed publication acknowledgement function | Direct INSERT/UPDATE/DELETE on outbox, publication, event, idempotency and inbox tables |
| Consumer | SELECT immutable events needed for processing; insert claims through the fixed claim contract | UPDATE/DELETE/TRUNCATE on inbox; outbox acknowledgement; event or idempotency mutation |

`PUBLIC`, reader roles and unrelated job-plane roles receive no table mutation or function execution authority.

## Immutability and retention boundaries

- `domain_events`: append-only, indefinite.
- `event_append_idempotency`: append-only, same lifetime as `domain_events`.
- `event_outbox`: immutable pending work, deleted only by fixed acknowledgement after durable receipt insertion.
- `event_publications`: append-only, same lifetime as `domain_events`.
- `consumer_inbox`: append-only, same lifetime as `domain_events`.
- `aggregate_snapshots`: immutable and content-addressed.

## Consequences

Pending work can be compacted after publication without weakening append idempotency. Inbox storage grows with distinct consumer-event pairs, which is intentional because no safe finite retention boundary exists while domain events remain authoritative indefinitely.

Source policy and tests do not grant roles, migrate an operator database or activate a publisher or consumer. Runtime proof requires a clean committed candidate, a commit-bound disposable PostgreSQL approval record, exact revision 0008, golden retry and retention vectors, ACL denial probes, and complete disposable cleanup.
