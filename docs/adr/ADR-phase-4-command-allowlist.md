# ADR: Phase 4 Command Allowlist and Research-Only Seam

## Decision

Use a code-owned `JobType -> CommandSpec` registry and a separately provisioned,
root-owned immutable release. The approved deployment root is
`/opt/trading-agent-research/releases/51de1cf06b3d595a336e19390230d0c09b608585`;
the approved external manifest is
`/etc/trading-agent/releases/51de1cf06b3d595a336e19390230d0c09b608585.json`.
The approved revision is the reviewed backend attribution commit. Task 12 must
provision both paths and replace the fail-closed, intentionally unprovisioned
manifest digest in worker code in the same reviewed release; this change does
not create either path.

The versioned `trading-agent-release-manifest/v1` contract has an exact,
canonically sorted entry for every filesystem object below the deployment
root. Each entry carries `path`, `type`, `mode`, `size`, and `sha256`.
Directories use size zero and SHA-256 of empty bytes. Task 12 must fix the
external manifest's reviewed SHA-256 in worker code; until then `None` blocks
attestation. Attestation performs a complete no-follow walk and rejects extra,
missing, changed, unsupported, or symlink entries. This
includes the venv interpreter, Python modules, native extensions, `.pth`
files, data, config, dot files, and ignored files; Git state is neither queried
nor trusted.

Every deployment ancestor and manifest ancestor must be root-owned, not
group/world writable, and have no setuid, setgid, or sticky bits. The manifest,
deployment root, every subdirectory, and every file must be root-owned,
read-only, and free of special mode bits. Every protected ancestor and artifact
must have an empty extended-attribute list; this also rejects POSIX ACL and
`security.capability` authority. The manifest must include the
fixed executable `.venv/bin/python` and `main.py`; the interpreter must be
executable. Worker argv starts with the exact interpreter plus `-I -B`, uses a
fixed prefix and typed payload mapper, and always has `shell=False`. Clients
cannot provide executable, module, argv, shell, cwd, environment, output path,
or timeout.

Successful attestation creates an opaque capability retained only in a weak
issued set. It expires against a short monotonic deadline and is consumed on
the first build attempt, including failed attempts. `build_command` then
re-attests the complete manifest and compares root, interpreter, and manifest
device/inode identities before returning. Expiry is checked before and after
that potentially costly full re-attestation.

Task 9 calls `prepare_immediate_spawn(job)`, which returns only an opaque,
short-deadline `PreparedSpawn` retained in a weak issued set. At the actual
`Popen` boundary it calls `consume_prepared_spawn(prepared)` exactly once to
obtain command fields. Delayed, forged, and second consumption fail. Task 7
does not spawn a process.

The attestation and `BuiltCommand` both carry the exact approved backend
revision. At the immediate process boundary the runner validates canonical
`job_<32 lowercase hex>` and `attempt_<32 lowercase hex>` identifiers, consumes
the opaque command, rebuilds the empty-start environment, and injects only
`TRADING_JOB_ID`, `TRADING_JOB_ATTEMPT_ID`, the attested
`TRADING_RESEARCH_BACKEND_COMMIT`, and the fixed
`TRADING_RESEARCH_SCRATCHPAD_ROOT`. The legacy `TRADING_ATTEMPT_ID` and all
source/client overrides for these worker-owned values are rejected.

Environment settings are opaque, weak-set-issued values. Both issuance and
every child-environment build validate the exact fixed roots, no symlinks,
owner, and mode `0700`. Output/scratch roots may be worker-owned `0700` because
they are writable result authorities, not executable authorities. Only the
fixed `TRADING_RESEARCH_*` credential names are captured; arbitrary constructor
credentials and paths are impossible. The child starts from an empty mapping,
uses fixed `PATH` and isolated `HOME`, and forces paper mode and all live gates
false. Environment revalidation occurs after command consumption and
immediately before `Popen`.

## Safety evidence

The Control API and worker share one canonical read-only kill-switch resolver.
Absence of the exact `<data root>/.kill_switch` means `INACTIVE`. A present,
private, owner-controlled regular file with exactly one valid ISO timestamp and
non-empty reason means `ACTIVE`. A malformed, unreadable, symlinked, or unsafe
present object means `UNKNOWN`. There is no invented inactive file format.
Missing or invalid `.mode` and missing or invalid live gates remain unknown and
block the worker. The Control API remains read-only and preserves its response
contract.

## Alternatives

- Git/subprocess attestation was rejected because it omits ignored files,
  environment hooks, venv content, native extensions, and data/config files.
- A manifest inside the deployment root was rejected because self-hashing
  cannot cover itself without a circular contract.
- Long-lived startup capabilities were rejected because they leave a TOCTOU
  window and can be replayed.
- Forwarding the worker environment or shared `.env` was rejected because it
  can transfer trading authority to a research child.

## Failure and rollback

Unknown safety evidence, invalid payload, missing deployment/manifest, digest
or exact-set mismatch, xattrs, special/unsafe mode, unsafe owner/type,
expired/reused capability or prepared token, or identity drift becomes
`BLOCKED` before Task 9 may spawn. Rollback stops or
disables Phase 4 worker units and does not restore dashboard spawning or
`run_status.json` operational truth.
