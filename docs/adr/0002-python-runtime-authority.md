# ADR 0002: Python Runtime Authority

- Status: Accepted for source policy
- Operational status: Not activated
- Date: 2026-07-20
- Owners: Trading Agent source and release maintainers

## Context

The repository contains three separate Python concerns:

1. the root control-plane project;
2. the preserved `legacy/research-backend` project;
3. offline release build and verification tooling.

A source checkout, developer virtual environment, user-home Python, `PATH`, or ambient activation must not become production runtime authority. The worker executes an isolated research backend and therefore needs two separately attested interpreter identities: the application interpreter already running the worker and the backend interpreter used for the child process.

Release Authority v2 source exists, but activation and production provisioning remain deliberately unavailable. This ADR defines the authority boundary without authorizing runtime deployment.

## Decision

Production Python authority is selected only by a reviewed, root-owned, immutable release authority document and its sealed release manifest.

The authority must bind:

- one absolute application interpreter path;
- one absolute backend interpreter path;
- exact CPython 3.11 identities;
- executable hashes and complete release-manifest hashes;
- the source commit and source tree;
- application and backend release roots;
- the command manifest and fixed backend working directory.

The running application must satisfy:

```text
Path(sys.executable) == authority.application_python
```

The research child command must use only:

```text
authority.backend_python -I -B main.py --mode snapshot --research-only
```

with the attested backend root as `cwd`, `shell=false`, and the empty-start allowlisted environment.

The following are never runtime authority:

- `.venv` from a source checkout;
- a user-home UV or pyenv interpreter;
- `/usr/bin/env python`;
- `PATH` lookup;
- an environment-provided interpreter override;
- the interpreter used only to run an external verifier;
- a successful source build or test result.

Build-tool Python and runtime Python are separate. Offline build tooling may use an explicitly selected reviewed hermetic Python, but the resulting sealed authority must independently bind the runtime interpreters by path, identity and hash.

## Fail-closed behavior

Startup, worker claim and command preparation must reject when:

- the protected authority is absent, malformed or writable;
- an authority path contains a symlink or unsafe ancestor;
- `sys.executable` differs from the attested application interpreter;
- the backend interpreter, release tree or manifest differs from the authority;
- the authority changes during attestation;
- Runtime Authority v2 activation is unavailable.

No fallback interpreter is permitted.

## Source and operational states

The source policy is accepted and testable now. Runtime Authority v2 remains dormant because `load_runtime_authority_v2()` and the activation lifecycle intentionally fail closed until a separate reviewed implementation and operator approval exist.

This source decision does not authorize:

- creating `/etc/trading-agent-v2/release-authority-v2.json`;
- installing under a production root;
- changing systemd units or service identities;
- starting or restarting services;
- PostgreSQL recovery or migration;
- paper-production promotion;
- live trading.

## Consequences

- Developer convenience cannot select a production interpreter.
- Application and research backend dependency graphs remain separate.
- A release candidate is unusable until its exact interpreters and manifests are independently verified.
- Source tests can prove fail-closed policy without creating runtime authority.
- Production activation requires a later approval-gated ADR or runbook with exact paths, identities, digests and rollback evidence.

## Verification

Source verification must include:

```bash
uv run pytest -q tests/jobs/test_command_registry.py \
  tests/runtime_release/test_v2.py \
  tests/runtime_release/test_v2_runtime_config.py
make audit
```

Operational verification is intentionally out of scope until activation is separately implemented and approved.
