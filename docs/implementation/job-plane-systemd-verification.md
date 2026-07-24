# Job Plane Release v2 Systemd Verification Evidence

**Evidence date:** 2026-07-16
**Decision:** `NOT_VERIFIED` — `systemd-analyze` PASS is not claimed for
Release Authority v2 candidate units.

This was a read-only unit-rendering and host-prerequisite review. No v2 unit
was installed, loaded, started, or enabled.

## Candidate source identity

The clean isolated source input observed for this review was branch
`codex/job-plane-recovery-candidate` at initial cherry-pick commit
`e7141221423cc8d4fb3acfd757275e6d9eb69140` (tree
`b81625a58f307b7ae5503f6d56f87e21d5f1776b`). No Release Authority v2 stage
or rendered unit set was built from it.

The later `0006` source is frozen but uncommitted at 27 modified and 5
untracked paths because its full candidate test gate has three unresolved
canonical AJV dependency failures. It is therefore not a clean candidate
identity and cannot replace the initial commit in `WorkingDirectory`,
`ExecStart`, or authority metadata. No v2 unit was rendered from that dirty
state.

## Static properties present in the renderer

The v2 renderer currently emits only:

- `trading-job-api.service`;
- `trading-job-worker.service`.

It emits no scheduler service or timer into the candidate unit set. Static
policy fixes worker concurrency to one and command authority to SNAPSHOT only.
Both services set paper mode and the two live gates false. The API host/port
are fixed to `127.0.0.1:8401`, and API/worker use distinct database role names.
These are source properties only, not installed or runtime evidence.

## Verification blockers

### 1. Manager-scope conflict

Rendered v2 units contain distinct `User=` and `Group=` directives. The v2
release documentation therefore correctly classifies them as system-manager
units. Treating them as user-manager units would not provide the intended Unix
identity separation.

The requested `systemd-analyze --user verify` gate conflicts with that design.
The scope must be decided explicitly:

- retain distinct service identities and verify/install as system units with
  `systemd-analyze --system verify`; or
- redesign the identity/credential model before using a user manager.

The review does not reinterpret a successful parse of another unit scope as
approval for v2.

### 2. Staging path and exact ExecStart path do not coincide

The static builder can write a candidate to an arbitrary private staging path,
but the v2 renderer hard-codes:

```text
/opt/trading-agent-v2/releases/<exact-commit>/application/.venv/bin/python3.11
```

for service `WorkingDirectory` and `ExecStart`. `/opt/trading-agent-v2` is
currently absent, and this session has no permission to install there.
Therefore direct host verification cannot prove that the exact v2 executable
exists.

A safe pre-install test would require a reviewed alternate-root image that
places the sealed candidate at the exact `/opt/.../<commit>` path inside that
image, contains the required account metadata, and invokes the correct system
manager scope. The current builder/provisioner does not provide that evidence.
Replacing ExecStart with `/bin/true` or a mutable staging interpreter would be
syntax-only testing and cannot satisfy exact-path authority.

### 3. Service identities and credential-file permissions are not provisioned

Metadata-only `getent` checks found no Unix users or groups named:

- `trading-job-api`;
- `trading-job-worker`;
- `trading-job-scheduler`.

The existing Job API, worker, and scheduler environment files are UID/GID
`1000:1000`, mode `0600`, because they belong to the old user-manager model.
Future distinct system service identities cannot read those files. No value
was read or printed.

Before system-unit verification/rollout, separate protected credential files
must be created with narrowly reviewed owner/group access for each service;
they must not reuse a shared database role and must not enter the release
stage or authority document.

### 4. Protected v2 authority/evidence paths are absent

Metadata-only checks found these paths absent:

```text
/etc/trading-agent-v2/release-authority-v2.json
/etc/trading-agent-v2/release-activation-v2.json
/run/trading-agent-v2/safety-state.json
/etc/trading-agent-v2/research-input-manifests/active.json
```

The runtime v2 loader and installed-tree attestor remain deliberately
fail-closed. API and worker therefore could not pass startup authority even if
their executable paths existed.

### 5. Filesystem and process hardening is incomplete

The rendered v2 units include useful baseline controls such as
`NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`, `ProtectSystem=strict`,
empty capability sets, and several kernel/namespace restrictions. They still
lack required least-privilege bindings:

- no `ProtectHome` or equivalent masking, so absence of an explicit legacy
  bind is not proof that the complete legacy/operator home is inaccessible;
- no explicit read-only bindings for static authority, activation, current
  safety evidence, semantic authority, semantic input, or the sealed release;
- no `StateDirectory`, `RuntimeDirectory`, `ReadWritePaths`, or narrowly scoped
  writable binds for worker heartbeat, artifacts, reports, signals, and
  scratch paths;
- with `ProtectSystem=strict`, the worker's `/var/lib/trading-agent-v2/...`
  outputs are not writable as currently rendered;
- no explicit `RestrictAddressFamilies` policy for loopback API versus the
  research-only child/network boundary;
- no `SystemCallArchitectures=native` or equivalent reviewed syscall policy;
- no explicit dependency/order binding to the required safety/semantic
  producers.

The worker must not receive a full legacy-root bind or any broker/exchange
credential-bearing path. Required read/write paths need exact allowlists owned
by the distinct service identity.

### 6. Manifest digest configuration is not available

The static authority calculates a command-manifest digest and semantic policy
digest, but current Job API/worker composition deliberately forbids supplying
authority digests through environment variables. That is the correct
anti-override direction: units must not add digest environment variables merely
to satisfy a text check.

The missing configuration must instead be solved by the protected, root-owned
static authority and promotion/activation lifecycle plus installed-tree
attestation. Those paths and loaders are currently unavailable. Consequently
no exact release, command, semantic, or promotion digest is configured for a
v2 service.

## Read-only command evidence

The focused review ran:

```text
systemd-analyze --version
systemd-analyze verify --help
systemd-analyze --user verify \
  ops/systemd/trading-job-api.service \
  ops/systemd/trading-job-worker.service
systemd-analyze --system verify \
  ops/systemd/trading-job-api.service \
  ops/systemd/trading-job-worker.service
stat/realpath metadata checks for current unit, interpreter, credential, and authority paths
getent passwd/group metadata checks for proposed service identities
rg/sed/nl inspection of the v2 renderer and its tests
```

The two `systemd-analyze` invocations returned exit code zero only for the
tracked Phase 4B input units, whose existing Phase 4 `/opt` interpreter paths
are present. They did **not** inspect v2-rendered candidate units and are not a
v2 PASS. No v2 unit file existed to verify, and no alternate-root verification
was performed.

No unit test or service command was run by this focused review. No
`daemon-reload`, install, start, stop, restart, enable, disable, or timer action
occurred. Port 8401 was not opened and no job was inserted.

## Required PASS gate

Systemd verification may be recorded as PASS only after all of the following
are true:

1. a sealed candidate from an exact clean commit exists;
2. its hermetic interpreter exists at the exact path represented to the
   verifier, without production installation unless separately approved;
3. manager scope and distinct Unix service identities are consistent;
4. protected per-service credential-file metadata is valid and database roles
   are distinct;
5. static authority, promotion, fresh safety, and fresh semantic paths/digests
   are bound through reviewed protected-file loaders;
6. worker read/write roots and home/legacy/credential denials are explicit;
7. candidate API and worker units pass exact-path `systemd-analyze` validation
   in the selected scope;
8. the scheduler service/timer is absent from the candidate and remains
   disabled.

Until then, the systemd result remains `NOT_VERIFIED`; it does not authorize
installation or service rollout.
