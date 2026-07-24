# Phase 4B Task 5 report: immutable provisioning and systemd authority

## Status

DONE_WITH_CONCERNS. Final offline staging, trust-chain repair, static/dynamic
tests and evidence are complete. Root provisioning was deliberately not run;
no service, timer, database, listener, active runtime, port 3002 or Cloudflare
state changed.

Frozen authorities:

- app `527f5306e77c846744bd67c711179e3f43cb3612`;
- backend `0762181487e32ba3270983e87749f6a4558712b8`.

## Trust-chain repair

- Added a standard-library standalone verifier executed by `/usr/bin/python3
  -I` before any staged code. It validates ancestors, exact complete
  path/type/mode/size/hash sets, raw and canonical manifest digests, commit,
  release type, Python identity, interpreter mode, ownership and link policy.
- Bootstrapping comes only from exact Git archive objects; the mutable checkout
  and staged interpreter are outside the initial trust base.
- Root install verifies releases before copy, copies create-only, then verifies
  root-owned releases again. Metadata pins verifier, manifests,
  command/authority and every unit digest.
- Database environment input is snapshotted without following links and checked
  for owner, mode, allowlisted keys and the non-owner `trading_jobs` role.
- Worker output and backend scratch are distinct private `0700` leaves. No
  legacy root or credential-bearing file is bound to the worker.

## Verification evidence

```text
uv run pytest -q tests/runtime_release/test_standalone_verifier.py \
  tests/runtime_release/test_provision_script.py tests/jobs/test_systemd_units.py
35 passed

shellcheck ops/phase4b/*.sh
exit 0

bash -n ops/phase4b/*.sh
exit 0
```

An earlier full-size stage passed the installer twice in an isolated user
namespace: an append-only artifact was preserved and 29,246 installed entries
were path/type/mode/size/mtime-identical on rerun. That stage was later revoked
after a review import wrote bytecode. The new sealed stage was not executed or
copied after seal; system-only standalone verification passed. Copied-release
tamper and duplicate DB user/password keys are rejected without publication or
secret output. Unit syntax passed with only the unavailable pre-install
ExecStart replaced by `/bin/true`; exact production values are asserted
separately.

## Final staging authority

```text
stage /home/thenam176/.cache/trading-agent-phase4b-stage-527f5306-07621814-sealed-final-v2
metadata 75f80edf66354a9c5cab67a4bbdaa781fc4747aba93cea375804c0f8428c9242
app canonical 0b37888e18c32f01eb034c95148958536de3ba7505c68079906d14ecf425b8b6
app raw 0ed18317ddbc96ab57d2a466d2767b9c508bfd83c03aa0d73ccaf78871a662c4
backend canonical 7be4c7d310523a4a3b57232fea67ff63c3ae76b324a79160e315eec8bbb49d4b
backend raw 070aa58dbf9460a4ee7bbcf69481bd272e700124703cdc69679163ffe7386807
command ec5efc82664d97866c0969f99df65587ef938b0d810981816702a661c2403465
authority 53311fba7731f53d658c84cb2d4ee6e4b19056b0c9926b44d21ccb8a21813932
verifier 8f7cf1bc3161f64e2f9814547c4ccd8a30d67a9bade1268e79767d2e965ca5d5
```

The stage has zero symlinks, special files, hard-linked regular files or
bytecode/cache entries. Post-seal audit used only the system standalone
verifier and read-only file/hash tools; no staged executable ran. Older
`a06a1160...` and `7aeaa300...` authorities are revoked.

## Stop gate

All six semantic sources remain older than the code-owned two-hour maximum.
No semantic input or authority was published and no policy/timestamp was
relaxed. Root install is an explicit operator step recorded in
`phase-4b-systemd-provisioning.md`; stop afterward for verification and review.
Do not enable either timer or start services.

## Commit history

- `2563c44` — `ops: add phase 4b immutable provisioning`;
- `4090276` — `docs: record phase 4b provisioning evidence`;
- `527f530` — final application runtime authority;
- `0762181` — final backend runtime authority;
- `9107e42` — standalone trust-chain and root provisioning hardening;
- `bebd8de` — atomic publication, canonical DB env and runtime evidence repair;
- `ed22b12` — stable systemd ExecStart path/argv attestation;
- `db28236` — post-helper release seal and bytecode rejection;
- `98cf2ca` — signed seal version and absolute stage-path binding;
- refreshed-evidence commit follows this report.

Rollback before root installation is revert-only. After installation preserve
releases, jobs, events and artifacts; services and timers remain stopped until
the reviewed Phase 4B rollout authorizes each stage.
