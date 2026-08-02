# Package 6 Native Custody Authority R11 Design

> **Superseded production authority:** This document remains the design record
> for the disposable test-only native custodian and its protocol. It does not
> authorize production activation. The production authority and closure source
> is `06c-package6-release-authority-v2-closure-plan.md`, which requires the
> Release Authority v2 system-manager contract and keeps activation unavailable.

## Purpose and status

R11 moves Package 6 target process, recovery, transcript, and publication
custody into the native C11 custodian. Phase B produced the independently
reviewed native source subset. Phase C adds the strict Python client and
controller adapter. Phase D binds that authority into Package 6 approval and
source closure.

These are source changes only. They do not install or activate a socket,
service, scheduler, PostgreSQL instance, cgroup, runtime, deployment, broker
connection, or live-trading path. Production socket activation remains
deferred and both live approvals remain false.

## Authority boundary

The C11 custodian owns:

- target process creation and lifecycle;
- target PID, pidfd, process-group, and cgroup authority;
- stdout and stderr descriptors and retained transcript bytes;
- durable operation journal and recovery state;
- final native evidence publication and rollback;
- completed operation state until explicit acknowledgement.

Python owns only its connected protocol socket. Python does not own a target
PID, pidfd, cgroup descriptor, target transcript descriptor, numeric signal or
wait authority, transcript unlink, or publication rollback unlink.

If Python times out, disconnects, raises `BaseException`, or restarts, the
native operation remains recoverable until `ACK`.

## Protocol

The endpoint is a pre-opened local `AF_UNIX` `SOCK_SEQPACKET` descriptor.
Protocol version 1 has a 36-byte header:

```text
u32 magic
u16 version
u16 message_type
u32 flags
u8  request_id[16]
u32 payload_length
u32 payload_crc32
```

Version 1 requires:

```text
magic = 0x50364341
flags = 0
maximum payload = 1,048,576 bytes
request ID = 16 nonzero bytes
operation ID = 16 nonzero bytes
SHA-256 = 32 bytes
```

The exact request set is:

| ID | Operation | Authority |
|---:|---|---|
| 1 | `HELLO` | Verify version and feature set |
| 2 | `START` | Start one exact approved target |
| 3 | `STATUS` | Read one authorized operation state |
| 4 | `STOP` | Complete bounded native cleanup |
| 5 | `RUN_ONCE` | Run and retain one completed result |
| 6 | `READ_TRANSCRIPT` | Read bounded retained transcript bytes |
| 7 | `PUBLISH_BUNDLE` | Commit the native evidence generation |
| 8 | `ACK` | Release an accepted completed generation |
| 9 | `RECOVER` | Enumerate ordered retained generations |

Responses use the request type with bit `0x8000`. Public error responses use
`0xffff` and only bounded public status and code fields. No errno text, private
path, argv, environment value, credential, or transcript content is public.

Unknown message types, flags, fields, enum values, reserved bytes, duplicate
fields, bad ordering, invalid lengths, excessive counts, checksum mismatch,
request-ID mismatch, response-ID mismatch, replay collision, peer mismatch,
timeout, disconnect, and truncation fail closed.

## Strict Python client

`services/paper_runtime/custodian_client.py` mirrors the native bytes without a
third-party dependency. Construction requires:

- an exact Unix sequenced-packet socket;
- a non-inheritable descriptor greater than standard I/O;
- exact peer PID, UID, and GID;
- helper binary and native source-set digests;
- protocol version and features;
- candidate commit and tree;
- stage and fixture digests;
- paper mode and both live approvals false.

The client bounds every vector, string, field, frame, read length, response,
and recovery count. It validates exact operation identity and recovery token
on every authorized call. Request IDs are nonzero and never reused within a
client.

`READ_TRANSCRIPT` verifies offsets, returned length, observed and retained
sizes, EOF, truncation, and full-content SHA-256 when a complete stream is read.
The bytes are kept private to controller verification and never enter public
controller artifacts.

`PUBLISH_BUNDLE` requires a committed summary whose publication digest exactly
matches the returned manifest digest. `ACK` requires the exact publication
digest and an acknowledged tombstone state.

## Controller adapter

`Package6Controller` accepts only a validator-issued Package 6 capability and
an exact `CustodianClient` whose attestation matches it. Missing or mismatched
native authority raises before any request or child creation.

The adapter:

- rechecks every source binding immediately before `START` or `RUN_ONCE`;
- derives candidate-bound native operation and publication identities;
- delegates `START`, `STATUS`, `STOP`, `RECOVER`, `RUN_ONCE`,
  `READ_TRANSCRIPT`, `PUBLISH_BUNDLE`, and `ACK`;
- retains only opaque native identity, recovery token, state, and public-safe
  metadata;
- publishes before acknowledgement;
- treats incomplete STOP, transcript, publication, or ACK evidence as a
  fail-closed error;
- has no destructor that performs target cleanup.

The existing Package 6 lifecycle surface remains available to callers, but PID,
process-group, start-tick, transcript-path, and Python descriptor custody are
not public contracts.

## Runtime aggregation

The runtime integration requires the caller to inject the attested client. No
default filesystem socket path or production connector exists.

For each component, cleanup ordering is:

```text
STOP
→ bounded READ_TRANSCRIPT verification
→ PUBLISH_BUNDLE
→ retain manifest receipt in aggregate evidence
→ ACK
```

Recovery uses `RECOVER` and matches the exact derived operation identity. ACK
retry does not spend another STOP attempt.

Aggregate evidence contains transcript digest, retained size, observed size,
truncation, and EOF metadata only. It contains the `job_api` and `worker`
native operation and publication-manifest digests. It does not open, bind,
unlink, or copy native target transcript files.

## Approval and source closure

Package 6 approval schema version 3 requires an exact
`custodian_authority` object with:

- helper binary SHA-256;
- ordered native source paths and per-file SHA-256 values;
- canonical source-set SHA-256;
- protocol version `"1"` as an exact decimal string and exact feature set;
- pre-opened descriptor endpoint authority;
- production socket activation false;
- the eight canonical operations;
- candidate commit and tree;
- stage digest;
- deterministic provider-free fixture digest and provenance;
- paper mode;
- both live approvals false.

The Python validator and JSON Schema have exact required/property parity.
Unknown or omitted fields reject. The validator also hashes every native source
file and the complete canonical source-set object.

The controller finalizer repeats these bindings and requires exact native
publication manifest digests from the runtime aggregate. Its record includes
the full source set, protocol authority, candidate/stage/fixture authority,
paper/live gates, and both component publication manifests.

## Source test coverage

Python client and adapter tests cover:

- canonical and malformed frames;
- exact IDs, message types, status enums, states, flags, reserved bytes,
  lengths, counts, and field order;
- duplicate and unknown fields;
- peer mismatch and re-attestation;
- timeout, disconnect, response mismatch, and replay;
- `START`, `STATUS`, `STOP`, `RECOVER`, `RUN_ONCE`,
  `READ_TRANSCRIPT`, `PUBLISH_BUNDLE`, and `ACK`;
- transcript digest, truncation, EOF, and size invariants;
- missing or mismatched attestation authority;
- source drift before a native request;
- publish-before-ACK and recovery behavior;
- absence of Python target custody primitives;
- approval/schema tamper and omission for every new authority field;
- closure tamper and omission for native authority and publication receipts.

The native test target covers the Phase B protocol, process, journal, recovery,
publication, descriptor, cgroup-model, fault-injection, and sanitizer cases.
Those tests remain source validation and are not host deployment evidence.

## Deferred deployment boundary

No production connector is implemented. A later separately reviewed package
must decide and verify:

- helper build and installation authority;
- socket activation, ownership, mode, peer policy, and restart behavior;
- descriptor transfer into the controller process;
- cgroup delegation and host kernel policy;
- durable journal and evidence roots;
- service sandboxing and resource bounds;
- helper upgrade/rollback and protocol compatibility;
- production-parity crash and recovery tests;
- Release Authority changes and operator cutover.

Until that work is approved and performed, runtime construction requires an
externally supplied pre-opened descriptor and independently known peer
identity. R11 source closure does not make Package 6 complete or approved.
