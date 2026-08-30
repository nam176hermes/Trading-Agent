# ADR: P1-B finite local paper session

Status: Accepted with P1-27 protocol-v2 amendment

## Decision

P1-B uses the existing engine-neutral `StartPaperEngine`,
`SubmitTargetPortfolio`, `StopPaperEngine`, `InspectEngineRun`, and
`RequestExecutionReconciliation` commands. The independently versioned
`nautilus-paper-session-v2` protocol wraps those commands and the existing P1
event vocabulary in canonical, size-bounded frames.

One fixed owner controls one session. Command request IDs are derived from the
session and sequence, so replay safety survives restart without an unbounded
request registry. Commands, acknowledgements, events, payload digests, and
exact canonical bytes are checked before state changes. A sequence ambiguity moves the session to
`RECONCILIATION_REQUIRED`; terminal states accept no further commands.

Only one command may await acknowledgement. On recovery, the durable ledger
supplies the canonical P1 event prefix; its count, final event digest, and full
prefix digest must match the checkpoint before the journal can resume.

An acknowledgement must correlate to an accepted request, sequence, command
digest, and command-derived state. The checkpoint binds the acknowledgement
prefix. Control-channel EOF closes cleanly only after the exact stop command is
acknowledged and the accumulated events pass the existing P1 stream validator;
any earlier or incomplete EOF requires reconciliation.

Stop before the first accepted target is rejected because the shared P1 event
contract cannot fabricate a zero-target completion. After work is accepted,
Stop settles the next sealed zero target, cancels later re-entry targets inside
the child, emits the shared final observations, and then closes cleanly.

The durable checkpoint binds the accepted command prefix, emitted event
prefix, semantic state, child identity, schema-8 closure, and portfolio state.
Restart is permitted only when all recorded authority matches exactly.

## Boundaries

- Input is deterministic local market data or replay data only.
- No credential, socket, DNS, HTTP, WebSocket, broker, account, or exchange
  authority exists in the protocol.
- Runtime-specific Nautilus/Cython objects remain behind the runtime adapter.
- P1-A events, projector, ledger, and deterministic accounting remain the
  shared source of business truth.
- Live, network, production, and provider authority remain false.

## Consequences

P1-27 may implement the native loop without adding commands or a second event
schema. P1-28 must attach that loop to the existing controller/custodian rather
than creating another lifecycle owner. Any mismatched checkpoint, child,
closure, event prefix, or portfolio state requires reconciliation instead of
automatic continuation.
