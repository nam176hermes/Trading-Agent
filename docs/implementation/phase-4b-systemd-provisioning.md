# Phase 4B systemd and root provisioning evidence

Provisioning is create-only and has not been run as root. The reviewed script:

- requires EUID 0 and never invokes sudo, systemctl or a staged executable;
- copies the exact user-owned stage and standalone verifier into one private
  root-owned snapshot, then performs every verification and publication read
  from that snapshot;
- rejects missing, extra, tampered, linked, special, misowned or writable
  objects before installation;
- independently verifies complete release sets before and after root-owned copy;
- publishes new files through same-directory temporary files and atomic rename,
  and removes every target created by a failed provisioning transaction;
- installs manifests/authority `0444`, units `0644`, service environments
  UID/GID 1000 `0600`, runtime output and private scratch directories `0700`;
- requires separate existing non-owner credentials for
  `TRADING_DATABASE_USER=trading_jobs` and read-only
  `TRADING_DATABASE_USER=trading_reader`, copied to protected snapshots before
  validation without printing values;
- creates no current alias, wants link, daemon reload, service start, timer
  enablement, database write or public listener.

Fresh evidence:

```text
uv run pytest -q
668 passed, 1 warning

uv run pytest -q tests/jobs/test_systemd_units.py \
  tests/runtime_release/test_provision_script.py \
  tests/runtime_release/test_standalone_verifier.py
37 passed

shellcheck ops/phase4b/*.sh
exit 0

bash -n ops/phase4b/*.sh
exit 0
```

Dynamic tests cover idempotency, secret-safe output, duplicate DB user/password,
missing/extra/tamper, interpreter replacement, mode, symlink, hardlink, FIFO,
existing-content collision, post-validation staged-unit inode swap and partial
retry. The unit-swap regression proves unverified bytes cannot be published
after the original user-owned stage changes. Copied releases are independently
attested while still at the private `.installing` path; a copy-tamper test proves
that no invalid target is published. An earlier full-size harness established
first-install/rerun preservation across 29,246 entries before that old staging
tree was later revoked. The new sealed authority was deliberately not executed
or copied after seal; its post-seal evidence is standalone read-only attestation.

The database environment parser requires one canonical instance of every
required key, rejects whitespace/export syntax, duplicate or unknown keys,
pins the loopback host and the expected non-owner role (`trading_jobs` or
`trading_reader`), and rewrites protected canonical snapshots for service env
generation. No input value is printed. The Job API token remains exclusive to
`job-api.env`; `control-api.env` receives only its reader database settings.

An unchanged staged unit set with only the unavailable pre-install ExecStart
replaced by `/bin/true` passed `systemd-analyze --user verify`. Exact unit paths,
hashes, bind directions, hardening and timer policy are asserted by tests.
Direct verification against `/opt` remains a mandatory post-install gate.
Effective `ExecStart` attestation normalizes only the stable configured `path`
and `argv[]`. Unit file hash, global `FragmentPath` and empty `DropInPaths`
remain independently required; dynamic systemd execution fields such as PID,
timestamps, exit code and status cannot create a false failure after a oneshot
has run. Tests prove pre/post execution serialization normalizes identically,
while altered executable or argv is rejected.

## Operator gate

The only approved root installation command is:

```text
sudo /home/thenam176/.local/share/codex-worktrees/trading-agent-phase4/ops/phase4b/provision-root.sh \
  /home/thenam176/.cache/trading-agent-phase4b-stage-fdc085a0-41f055b4-sealed-final \
  f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c
```

Codex did not run it and must not request a password. Stop immediately after
this command for installed verification and independent review. Do not reload,
start or enable any unit yet. The semantic source-age blocker remains active;
both semantic and scheduler timers must remain disabled.

After a separately approved daemon reload, UID 1000—not root—must run
`verify-installed.sh <stage> <metadata-sha256>` to prove effective global unit
`FragmentPath`, empty drop-ins, exact ExecStart, disabled timers and loopback
listener policy. Runtime artifact, output, scratch and rotating semantic trees
may contain append-only evidence; the verifier validates ownership, exact modes,
file types and link policy without requiring them to be empty. During semantic
acceptance, add `--require-semantic` to invoke the independently attested app
validator against the current manifest and six inputs. The revoked
`a06a1160...`, `7aeaa300...`, `75f80edf...`, `27a83612...`, and `1435864c...`
authorities must not be used.
