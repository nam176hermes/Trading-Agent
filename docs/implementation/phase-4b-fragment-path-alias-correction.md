# Phase 4B systemd FragmentPath alias correction

Captured: 2026-07-13 EDT.

## Reproduced evidence and root cause

The user manager reports the first matching lexical search path for the safety
state exporter, while the immutable installed-unit authority uses the
equivalent `/etc/systemd/user` path:

```text
systemctl --user show trading-safety-state-export.service --property=FragmentPath --value
/etc/xdg/systemd/user/trading-safety-state-export.service

readlink /etc/xdg/systemd/user
../../systemd/user

readlink -e -- /etc/xdg/systemd/user/trading-safety-state-export.service
/etc/systemd/user/trading-safety-state-export.service

readlink -e -- /etc/systemd/user/trading-safety-state-export.service
/etc/systemd/user/trading-safety-state-export.service
```

`/etc/xdg/systemd/user` is a root-owned symbolic link. Following both unit
paths with `stat` produced the same regular-file device and inode,
`2096:3958001`. The former lexical equality check therefore rejected two
names for the exact same installed file.

## Audit provenance

The audit snapshot was captured read-only at `2026-07-13T16:09:31-04:00`
from
`/home/thenam176/.local/share/codex-worktrees/trading-agent-phase4` at source
commit `f3e743f7ff874527b0e7b3a9b86b7c656ef3c920`. No environment values,
DSNs, tokens, credentials, or unrelated processes and listeners were read or
recorded.

Scoped `systemctl show` observations were:

| Manager | Unit | Load | Active/substate | Process |
| --- | --- | --- | --- | --- |
| user | `trading-safety-state-export.service` | loaded | inactive/dead | `MainPID=0`; no process |
| user | `trading-safety-state-export.timer` | loaded | inactive/dead | N/A; timer |
| user | `trading-control-api.service` | loaded | inactive/dead | `MainPID=0`; no process |
| user | `trading-job-api.service` | loaded | inactive/dead | `MainPID=0`; no process |
| user | `trading-job-worker.service` | loaded | inactive/dead | `MainPID=0`; no process |
| user | `trading-job-scheduler.service` | loaded | inactive/dead | `MainPID=0`; no process |
| user | `trading-job-scheduler.timer` | loaded | inactive/dead | N/A; timer |
| system | `trading-semantic-input-refresh.service` | loaded | inactive/dead | `MainPID=0`; no process |
| system | `trading-semantic-input-refresh.timer` | loaded | inactive/dead | N/A; timer |

The safety-state-export, job-scheduler, and semantic-input-refresh timers each
reported `disabled`; `systemctl is-enabled` uses exit `1` for that state. The
process provenance is therefore explicitly N/A beyond the observed
`MainPID=0`: none of the scoped services had a running process.

Listener queries were restricted to the two verifier-scoped ports. `ss`
returned no local address for either `sport = :8400` or `sport = :8401`, so
the exact listener and owning-process provenance for both ports is N/A. This
matches the inactive service snapshot; no unrelated listener was enumerated.

The verifier's protected and immutable data-root authority is:

- sealed input:
  `/home/thenam176/.cache/trading-agent-phase4b-stage-fdc085a0-41f055b4-sealed-final`;
- immutable application and backend releases:
  `/opt/trading-agent-phase4/releases/app-fdc085a05019d700ccbce59370941e2c97ef899a`
  and
  `/opt/trading-agent-phase4/releases/backend-41f055b48033714c660f44cc20498b7545366e75`;
- installed configuration and unit authority: `/etc/trading-agent`,
  `/etc/systemd/user`, and `/etc/systemd/system`;
- protected semantic input and authority:
  `/home/thenam176/.local/share/trading-agent/research-input` and
  `/etc/trading-agent/research-input-manifests`;
- protected output roots:
  `/home/thenam176/.local/share/trading-agent/job-artifacts` and
  `/home/thenam176/.local/share/trading-agent/research-output`, including its
  `reports` and `signals` children;
- protected runtime roots: `/home/thenam176/.local/run/trading-agent`, its
  `research-home` child, and `research-home/scratchpad`.

The read-only verifier attested requested/effective paper mode and both live
gates false without recording the contents of any environment file.

## Correction and path-identity security

`verify-installed.sh` now sends both user and system `FragmentPath` results
through one `same_canonical_path` helper. The helper requires exactly two
non-empty absolute paths without newline characters, canonicalizes both with
strict `readlink -e --`, and accepts only exact canonical-string equality.
Strict `-e` resolution also requires every path component and the final file
to exist.

This deliberately does not accept basename equality, prefix matching, missing
paths, relative paths, unresolved links, or a link that resolves to another
file. The expected installed unit must still be a non-symlink regular file
with exact root ownership, mode, and staging-metadata SHA-256. User-shadow
absence, empty `DropInPaths`, normalized `ExecStart`, disabled timers, and
`systemd-analyze` checks are unchanged.

## RED and GREEN evidence

Focused RED, before adding the helper or changing either call site:

```text
uv run pytest -q tests/runtime_release/test_provision_script.py -k fragment_path
FF
2 failed, 18 deselected in 0.23s
```

Both failures were normal assertions: the first proved the helper was absent,
and the second proved neither user nor system `FragmentPath` used it.

Focused GREEN after the minimal verifier change:

```text
uv run pytest -q tests/runtime_release/test_provision_script.py -k fragment_path
..
2 passed, 18 deselected in 0.34s
```

The focused behavior test executes the extracted shell helper against the
exact expected path, a symlinked-parent alias, a different existing file, and
relative, missing, empty, and newline-bearing inputs.

Required regression checks:

```text
uv run pytest -q tests/runtime_release/test_provision_script.py
20 passed in 13.00s

uv run pytest -q tests/runtime_release/test_provision_script.py tests/runtime_release/test_provisioning_generators.py tests/jobs/test_systemd_units.py
38 passed in 13.11s

bash -n ops/phase4b/verify-installed.sh
exit 0

shellcheck ops/phase4b/verify-installed.sh
exit 0

git diff --check
exit 0
```

## Installed-authority verifier outcome

The exact approved verifier was run read-only against the sealed stage:

```text
bash ops/phase4b/verify-installed.sh /home/thenam176/.cache/trading-agent-phase4b-stage-fdc085a0-41f055b4-sealed-final f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c
phase 4b installed authority verification passed
exit 0
```

## Runtime safety and rollback

This correction changed source, tests, and documentation only. It did not use
sudo; change ownership or modes; install files; mutate `/opt`, `/etc`, `/run`,
runtime data, systemd, services, timers, processes, databases, orders, or
trades; create the canonical root; edit a linked repository; or change paper
mode or either live-trading gate.

Rollback is a source revert of the commit containing this correction. No
runtime rollback is necessary because no installed or runtime state changed.
