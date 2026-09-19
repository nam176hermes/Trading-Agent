# P3 LOCAL official completion

The approved campaign remains A0–A3. A completed implementation, a successful
job, a qualified source and an economic P3 PASS are different outcomes.
`NONE_QUALIFIED` and economic FAIL keep P3 economic completion and P4 HELD.
This local execution does not authorize push, PR, merge or hosted publication.

## Checkpoint and source

Continue the standalone `codex/p3-official-completion` clone descended from
`ee1aa3f91f44497fd833a408c61e51b8e96ae42d`. Preserve the canonical checkout,
the earlier candidate and every historical receipt. The external execution
`STATE.json` records the current source, remaining tasks, expiry and disclosure
history. Unknown disclosure history blocks a real holdout.

Source must be committed and clean before invoking the existing
`scripts/prepare_p3_local_runtime.py`. Stage from the frozen root lock and pinned
Python/uv/native artifacts; missing artifacts are a blocker, not permission to
install different dependencies. Qualify each staged source before admitting
official work, including synthetic rehearsal. Source fixes require a new
checkpoint and qualification before a real disclosure.

## Research producer and admission

Use `scripts/seal_p3_dataset.py --acquisition-refs` for acquisition reconstruction,
or the same sealer through the new producer:

```bash
uv run python scripts/prepare_p3_local_research.py \
  --input /absolute/private/research-input.json \
  --identity /absolute/identity.json \
  --store /absolute/private/research-cas \
  --backup-store /absolute/private/research-backup \
  --output /absolute/evidence/research-proposal.json
```

The input contains `query`, `revision_inventory_ref` and `dataset_ref`. Add
`--acquisition-refs` to supply the sealer's `query`/`acquisition_refs` document
instead. Query cutoff must include actual materialization; backup cannot certify
a future cutoff. Retain the fixed 2,800 research days and the single monthly
exception, and keep the retrospective-current-archive label.

The producer reconstructs and reads back the complete backup before emitting
LOCAL backup, commitment and compact proof references. It creates proposals only.
Its store and backup must satisfy the existing caller-owned private CAS contract.
Holdout storage and credentials remain under the distinct custodian UID.

The issuer separately verifies the retained producer/readback evidence, seals
the research backup under root custody (root:root, directory `0500`, objects
read-only), and captures device/inode/mtime/ctime using
`local_research.protected_backup_identity`. Ancestors must be protected root
directories. Do not overwrite or repair a sealed backup in place; preserve the
failed execution and issue a new reviewed proposal when allowed.

After constructing InputSet, install its approved compact proof copies and
`LocalResearchProfile` at the issuer-derived path:

```text
/etc/trading-agent/local-research/<issuer>/research-inputs.json
/etc/trading-agent/local-qualification/<issuer>/P3_RESEARCH_INPUTS.json
```

The profile binds the exact InputSet, source, environment, canonical research
policy digest, commitment, backup receipt, producer proof and sealed backup root.
The qualification receipt must retain all three proof references. Inventory
objects and dataset row lists stay outside the compact proof store; reference
limits are unchanged. Admission checks root metadata without reading private
backup objects. It rechecks root custody and protected approval after semantic
readback. `P3_RESEARCH_INPUTS` is an operation admission gate, not an additional
fixed Pre-P3 prerequisite. Other gates retain their existing policy-file hash.

## Official launcher

Official execution is currently **HELD** by the accepted runtime transport.
The pinned CPython core contains 4,306 files. Mounting each sealed file requires
five arguments, exceeding bubblewrap 0.9.0's 9,000-argument limit before adding
dependencies. The actual host startup probe rejects this closure. Removing dev
dependencies fixes the separate argv byte limit but cannot fix the token limit;
`--args FD` is subject to the same counter. A reviewed immutable-transport plan
delta and fresh qualification are required. Do not change the pinned core,
weaken sealing, or treat the commands below as an operational PASS.

Use the existing `python -m services.job_worker` entry with
`TRADING_WORKER_PROFILE=p3-official-v1` and `P3_ISSUER_KIND=LOCAL`, the protected
request/profile, existing enqueue artifacts and the role's credential directory.
The launcher supplies `LocalRuntimeSafetyPreflight`; no second launcher or
execution engine is needed.

The safety source directory is derived from the authenticated run UUID:

```text
/var/lib/trading-agent-local/p3-official/<run-id>/safety
```

Its fingerprint must match the staged runtime authority. Static attestation
precedes the original exporter and activation refresh, followed by safety
validation with the unchanged six-second TTL. Startup preflight finishes before
database construction. Session owner/deadline guards remain separate; worker
composition never passes both runtime guard and explicit safety preflight.
The preflight retains the original startup approval guard in addition to its
own checks, including construction and both sides of dynamic refresh. A valid
replacement approval cannot silently replace the digest pinned at startup.
PARITY and PHASE_EXIT remain available only through the existing holdout session.

## Custodian producer and serving

`scripts/prepare_p3_local_custody.py prepare --configuration <private-json>` runs
under the declared custodian UID. Its configuration contains the LOCAL identity,
distinct custodian/research UIDs and names, private `store_root`/`backup_root`,
access-policy digest, approved GPG binary hash/version, output path, and
`holdout`/`buffer` sealer inputs (`partitions`, `query`, `schema`).

The existing sealer enforces 365 holdout rows and one OPEN_ONLY buffer. The
producer encrypts exactly those 366 canonical rows, verifies decryption and
backup readback, and creates the existing custodian attestation/CustodyRecord.
Keys and acquisition intermediates stay in private custodian storage; never
copy them into research CAS, public proof stores or Git.

GPG uses the pinned installed binary/version, a sealed key FD, bounded in-memory
output, integrity verification and a private temporary home. Its explicit S2K
work factor avoids agent calibration; autostart and key caching are disabled.
No key or plaintext enters argv or logs. Any failure prevents a custody proposal.

The issuer independently installs the proposed cipher profile at:

```text
/etc/trading-agent/local-qualification/<issuer>/custody-cipher.json
```

It pins source, LOCAL identity, custodian UID, private store/key reference and GPG
hash/version. The serving command consumes an inherited Unix seqpacket listener
prepared by the host controller at the exact protected endpoint:

```bash
python scripts/prepare_p3_local_custody.py serve \
  --profile /absolute/protected/profile.json \
  --profile-digest <sha256> --listener-fd <fd>
```

It accepts one connection and delegates to `serve_profiled_release`. Peer
validation, SQL one-use admission, fences and sealed memfd delivery remain owned
by the existing custodian/session path. It decrypts only after admission and
rechecks the protected cipher profile before returning bytes. Host-owned socket
and process cleanup must be recorded separately; a lost ACK is not permission
for another disclosure.

## Operational acceptance

Before real disclosure, complete a synthetic official Job API/SQL/worker/native/
custodian session with campaign-sized data, three native replicas per side,
fault injection and committed readback. Finish within 32 minutes of the fixed
37-minute deadline, retaining at least five minutes of margin.

Run B0–B4, register the fixed family, retain all 28 A0–A3 outcomes and evaluate
C01–C16 with the original validators. `NONE_QUALIFIED` skips official holdout.
For SELECTED, keep one session owner/view across HOLDOUT → PARITY → PHASE_EXIT.
Never change family, epoch, costs, thresholds, windows or native `1.231.0` after
disclosure. Recovery after a durable commit uses readback/export only.

Export the committed phase result or appropriate terminal observation, obtain
the protected LOCAL phase receipt through the existing issuer, and derive both
LOCAL and GITHUB status. Missing/expired/revoked authority remains HELD.
Only a valid phase receipt plus economic PASS establishes local P3 completion
at the observed time; published/hosted readiness remains HELD until publication.
