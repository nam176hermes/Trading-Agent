# Package 6 - Paper Runtime Foundation Validation

## Status

R11 Phase C and D implement the source-side native-custody boundary. This is a
source candidate for independent review, not a runtime activation, deployment,
or Package 6 approval.

Production socket activation, service installation, delegated-cgroup
validation, PostgreSQL runtime execution, and production cutover remain
separate operator-reviewed work. Both live approvals remain false.

## Entry gate

Do not run the Package 6 runtime until all prerequisite packages, the exact
candidate approval, the independently built native helper, the pre-opened
protocol endpoint, and a separate runtime Greenlight have been reviewed.

The Package 6 runtime remains:

- paper-only;
- loopback-only;
- provider-free;
- bounded to one Job API, one worker, and one controlled `SNAPSHOT`;
- isolated from systemd, schedulers, brokers, exchanges, account endpoints,
  order endpoints, and live trading.

## Implemented source authority

`services/paper_runtime/custodian_client.py` is the only Python protocol client
for target lifecycle custody. It mirrors native protocol version 1 and accepts
only an already-open `AF_UNIX` `SOCK_SEQPACKET` descriptor.

The client:

- verifies the endpoint type, close-on-exec state, and exact peer credentials;
- sends bounded frames with exact magic, version, flags, message type, request
  ID, payload length, and CRC32;
- requires the exact response message type and request ID;
- validates all fixed layouts, enums, flags, reserved bytes, field order,
  lengths, counts, identities, digests, and status mappings;
- rejects malformed, duplicate, unknown, replayed, timed-out, truncated, or
  disconnected exchanges;
- exposes only bounded public status codes and never exposes command
  arguments, environment values, credentials, private paths, errno text, or
  transcript bytes in public errors.

The native operation authority is exactly:

```text
START
STOP
STATUS
RECOVER
RUN_ONCE
READ_TRANSCRIPT
PUBLISH_BUNDLE
ACK
```

`Package6Controller` requires an injected, attested `CustodianClient`. Without
it, controller construction fails closed before a native request and before
child creation. The controller does not import `ctypes` or `subprocess` and
does not own a target PID, pidfd, process group, cgroup descriptor, transcript
descriptor, wait operation, signal operation, transcript unlink, or
publication rollback unlink.

## Lifecycle behavior

The controller derives one candidate-bound native operation identity for each
component. Immediately before `START` or `RUN_ONCE`, it revalidates the sealed
source bindings and builds the exact approved executable, argv, and paper-only
environment request.

Lifecycle calls are delegated as follows:

- `start()` uses native `START` and retains only the opaque operation identity
  and recovery token returned by the custodian.
- `status()` uses native `STATUS`.
- `stop()` uses native `STOP`, then reads each retained stream through bounded
  `READ_TRANSCRIPT` chunks to verify native digest and size metadata.
- `recover_completed_stop()` uses native `RECOVER`, matches the exact derived
  operation identity, and reconstructs metadata-only STOP evidence.
- `publish_evidence()` uses native `PUBLISH_BUNDLE` and retains the manifest
  digest receipt.
- `acknowledge_stop()` uses native `ACK` only after an exact publication
  receipt exists.
- `run_once()` uses native `RUN_ONCE`, `READ_TRANSCRIPT`,
  `PUBLISH_BUNDLE`, and `ACK`; it has no alternate Python spawn path.

Completed STOP authority is not acknowledged before native evidence
publication. A Python timeout, disconnect, exception, or restart cannot turn
an incomplete publication or cleanup proof into success; native `RECOVER`
remains the recovery boundary.

## Transcript and evidence boundary

Target transcript content remains inside native custody. Public controller and
aggregate runtime artifacts contain only:

- approved operation and component identifiers;
- opaque native operation identity;
- native lifecycle state and exit status;
- cleanup proof;
- retained and observed sizes;
- SHA-256;
- truncation and EOF facts;
- native publication manifest digests.

Public artifacts contain no target transcript path or transcript content.
Python aggregate evidence does not open, bind, unlink, or take descriptor
authority over target transcripts.

The runtime chain publishes each completed component through native
`PUBLISH_BUNDLE` before `ACK`. The aggregate record binds both native
publication receipts. Aggregate PostgreSQL and source evidence handling remains
separate from native target custody.

## Approval authority

Package 6 approval schema version 3 and its Python validator require exact
field parity. The approval binds:

- the ordered native C11 source set and each file SHA-256;
- the canonical native source-set SHA-256;
- the independently supplied helper binary SHA-256;
- protocol version 1 and the exact empty feature set;
- `PREOPENED_UNIX_SEQPACKET_DESCRIPTOR`;
- `production_socket_activation = false`;
- all eight native operations in canonical order;
- candidate commit and tree;
- stage digest;
- deterministic provider-free fixture identity and provenance;
- `mode = PAPER`;
- `live_execution_approved = false`;
- `live_trading_approved = false`.

Every field is exact. Omission, addition, reordering, tampering, source drift,
candidate drift, stage drift, fixture drift, helper drift, endpoint drift, or
live-mode drift rejects the approval.

## Controller closure authority

The source-only controller finalizer requires the same helper, source-set,
protocol, feature, endpoint, operation, candidate, stage, fixture, paper, and
live-gate authority from the validated approval. It also requires exact
`job_api` and `worker` native publication manifest digests and compares them
with the runtime aggregate.

The final decision record embeds the full ordered native source set and both
publication manifests. This binding does not itself authorize a runtime,
deployment, production activation, or live trading.

## Source-safe validation

The source candidate may be checked without PostgreSQL, services, listeners,
or real cgroups:

```bash
python3 -m compileall -q services/paper_runtime \
  scripts/finalize_package6_controller_evidence.py \
  scripts/validate_package6_runtime_approval.py
uv run --frozen pytest -q tests/native/test_package6_custodian.py
make test-package6-custodian-native
uv run --frozen pytest -q \
  tests/foundation/test_package6_custodian_contract.py
uv run --frozen pytest -q \
  tests/foundation/test_package6_runtime_controller.py \
  tests/foundation/test_package6_controller_closure.py \
  -m 'not runtime_postgres and not host_coupled'
uv run --frozen pytest -q \
  tests/foundation/test_package6_runtime_integration.py \
  tests/foundation/test_package6_runtime_approval.py \
  -m 'not runtime_postgres and not host_coupled'
```

Keep `P6C_DELEGATED_CGROUP_TEST_ROOT` unset for this source-safe set.

## Deferred boundary

Before any runtime attempt, a later reviewed package must provide:

- an independently built helper whose digest matches the approval;
- a pre-opened descriptor and independently known peer PID, UID, and GID;
- reviewed socket/service activation and lifecycle ownership;
- host-parity cgroup and crash-recovery evidence;
- fresh candidate, staging, fixture, PostgreSQL, and Greenlight authority;
- cleanup and rollback evidence outside the disposable roots.

No source statement in this document is deployment evidence. Package 6 remains
unapproved until those boundaries and final candidate review are closed.
