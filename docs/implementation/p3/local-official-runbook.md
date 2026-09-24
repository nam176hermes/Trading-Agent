# P3 LOCAL official completion

The approved campaign remains A0–A3. A completed implementation, a successful
job, a qualified source and an economic P3 PASS are different outcomes.
`NONE_QUALIFIED` and economic FAIL keep P3 economic completion and P4 HELD.
This local execution does not authorize push, PR, merge or hosted publication.

## Checkpoint and source

Continue the standalone candidate identified in the external execution ledger;
the R06–R11 follow-up starts from `4a1b7226e96a966cfcbf9fe1276d8cd22eb4e5b7`.
Preserve the canonical checkout, the earlier candidate and every historical receipt. The external execution
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

The bounded controller reuses these producers and validators:

```bash
uv run python scripts/p3_local_controller.py prepare-inputs \
  --request /absolute/evidence/input-refs.json \
  --store /absolute/private/research-cas --output /absolute/evidence/input-set-ref.json
```

`input-refs.json` has exactly `dataset_ref`, `commitment_ref`, `pit_suite_ref`,
`environment_ref` and `integration_receipt_ref`. All are retained ArtifactRefV1
objects from the same candidate. The producer enforces the ordered 2,800-day
window, builds the frozen folds and computes regimes from training data only.
Synthetic receipts in tests grant no authority to this command.

After the existing root custody step, place the exact proposed
`LocalResearchProfile`, compact proof copies, independent `ReviewApproval` and
its retained evidence under protected root directories. The review subjects
must be exactly the InputSet digest and LocalResearchProof digest. Then the
authorized root operator uses the frozen interpreter/controller:

```bash
python scripts/p3_local_controller.py install-research \
  --profile /absolute/protected/research-profile-proposal.json \
  --review /absolute/protected/research-review.json --minutes 60
```

This command does not seal caller-owned directories. It rejects unprotected
ancestors, validates the compact graph before replacing active authority,
publishes under the existing issuer lock and reads back through the admission
consumer. Validity is at most two hours and cannot exceed the review. The
receipt remains subject to independent producer/full-backup-readback review.

## Official launcher

LOCAL v2 uses the accepted SquashFS transport implemented in
`scripts/p3_local_transport.py`; the earlier per-file bubblewrap argument limit
is historical. The controller owns image construction, loop/mount identity,
generation checks and cleanup. The current external ledger, exact-candidate
qualification and actual rehearsal determine operational readiness. Source or
unit-test completion alone does not establish an operational PASS.

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

## Reviewed operation and session sequence

Run research operations in this order: `p3-baselines-v1`,
`p3-register-family-v1`, `p3-oos-a0-v1` through `p3-oos-a3-v1`, then
`p3-select-primary-v1`. Reuse existing manifest/registry producers and committed
result refs for the next operation; never substitute fixture publication entries.

1. `p3_local_controller.py propose-operation --request <proposal> --store <cas>
   --output <new-intent>` accepts `workflow_operation`, `input_set_ref`, typed
   `body`, and `primary_alpha_id` only for primary stages. Body references must
   already exist. This grants no execution authority.
2. Obtain independent review of those exact bytes and retain its report. Run
   `prepare-authorization` with the same CLI flags; its request contains `intent`,
   `identity`, `review_ref` and `expires_at`. It emits a bounded proposal with a
   fresh nonce. Never invent reviewer identities or preapprove future output.
3. The root operator stages a protected `p3-local-authority-request-v1` containing
   `execution_source`, `authorization` and `operation_input`, then runs
   `install-reviewed --request <protected-proposal> --review <protected-review>
   --proof-root <protected-compact-cas>`. The command checks payload scope,
   current source/research qualification and independent review before installing
   `/etc/trading-agent/local-qualification/<issuer>/operations/<nonce>.json`.
4. Stage the exact installed request with `stage-operation --request
   <installed-request> --review <protected-review>`. It uses the qualified runtime
   UID and its primary group, publishes canonical manifest/review bytes as
   root:worker-group `0440`, and returns their immutable nonce-specific paths.
   Partial staging is retained and cannot be replayed. Its parent run directory
   and `host-inputs.json` must already be protected by the existing root protocol.
5. As the configured worker UID, reuse `scripts/p3_authority.py enqueue` with
   the returned request/manifest/review/preflight paths and existing acceptance
   credentials. Enqueue creates the new `0700` preflight directory and retains
   its `0600` canonical response. Each nonce has a different directory; do not
   overwrite old enqueue evidence or use a root-owned preflight copy for the worker.
6. The authorized root controller invokes `run-operation --transport-request
   REQUEST_NAME.json`; this delegates to the existing transport, requiring its
   request operation to be `run`. It does not enqueue, retry or reset a database.
   Use `scripts/p3_authority.py wait` for the original enqueue's terminal readback.

`REQUEST_NAME.json` is a **bare filename** within the transport's protected
`REQUEST_PARENT`, not an absolute path. Pin the exact returned job ID, current
source, environment, worker UID/GID and tools in that reviewed transport request.

The root operator installs `host-inputs.json` (root:root `0444`, protected
ancestors) in `local_run_directory(identity)`. Its strict schema is
`p3-local-host-inputs-v1`, with `store_root`, `output_root`,
`worker_credentials_directory`, `job_api_token_file`,
`qualification_job_detail_ref`, `postgres_binary_sha256` and optional
`custodian_endpoint`. Values must be actual configured paths and retained
qualification references. The token file contains the existing Job API token,
never an inline token in JSON. Store/output are distinct worker-owned private
directories. Custody endpoint is consumed only for HOLDOUT.

`scripts/p3_local_host.py` checks the exact enqueue under that worker UID using
the qualified application's interpreter and source, then authenticates a fresh
read-only Job API observation as root. After mounting, it derives the profile
from the actual mounted closure, retains the separate host inventory in the
worker CAS, and publishes `LocalOfficialHostProfileV2`. Full consumer readback
runs in the same mount namespace before the existing worker starts. Image
inventory and host inventory are distinct references. All helper work spends
the original deadline. A per-run lock excludes concurrent research transports;
replacement requires the next operation and authenticated success of its predecessor.
`p3_local_publish_runtime.py` publishes only `runtime.json`; it does not produce
these per-job profiles.

For HOLDOUT, keep the transport running while the root issuer completes the
handshake. Root records the actual fork child and the original pre-image deadline
in `session-launch.json`. After exec, the worker writes a private observation
before opening SQL and waits for protected admission. Run:

```bash
python scripts/p3_local_controller.py install-session \
  --request /absolute/protected/installed-operation.json \
  --proof-root /absolute/protected/compact-cas
python scripts/p3_local_controller.py install-stage \
  --request /absolute/protected/installed-operation.json \
  --owner-credentials /absolute/protected/owner-credentials
```

`install-session` verifies PID, start ticks, process fingerprint, UID, parent,
host profile and deadline against `/proc` and the root launch record. It derives
the session's primary/custody refs from the admitted HOLDOUT intent. It refuses
an existing session; there is no automatic replay. `install-stage` must run
after the worker's actual claim is visible. It uses a read-only `trading_owner`
connection to the same database, hashes the current lease token in SQL, and
publishes only the next exact claim. Never use worker credentials for owner
observation or print lease tokens.

After each committed result, review/admit and `stage-operation` the next exact
PARITY or PHASE_EXIT intent, then enqueue with the returned paths. Run
`install-next-host --request <installed-request> --preflight <returned-preflight>
--manifest <returned-manifest> --review <returned-review>` before installing the
actual stage claim. It requires the original live session, previous stage record,
authenticated prior success and next queued job; it changes only job ID, payload
and custody endpoint. The continuing worker rechecks the mounted profile before
claiming. Keep the original worker and released
view; do not start another worker or another transport invocation for these stages.
The deadline never restarts. Do not retry a failed admission/claim automatically;
inspect authority and durable SQL state first. A session death or uncertain
disclosure cannot authorize a second holdout.

`cleanup --transport-request CLEANUP_REQUEST_NAME.json` delegates to the
existing transport cleanup; the request must explicitly specify `cleanup`.
Retain the original failure, terminal SQL/readback, cleanup result, source,
digests, identities, commands, timings and expiry in the external ledger.

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
