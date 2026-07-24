# Phase 4B systemd authority

These files are installation inputs, not active units. The global user units
are installed under `/etc/systemd/user`; the semantic refresh service and timer
are root system units under `/etc/systemd/system`. Provisioning never runs
`systemctl`, creates a wants link, or starts/enables/restarts any service.

The exact application authority is
`fdc085a05019d700ccbce59370941e2c97ef899a`; the exact backend authority is
`41f055b48033714c660f44cc20498b7545366e75`. Every `ExecStart` uses the fixed
application interpreter under `/opt/trading-agent-phase4/releases/app-<commit>`.
There is no `current` alias, host Python, shell, arbitrary argv, active-agent
dependency, dashboard dependency, port 3002 change, or Cloudflare route.

## Safety boundary

`trading-safety-state-export.service` is a fixed `--once` user service. Each
two-second non-persistent timer invocation creates a new mount namespace and
binds only the required `.mode` and optional `.kill_switch` file into
`/run/user/1000/trading-agent/safety-sources`. Thus an atomic source inode
replacement is observed at the next invocation without exposing the legacy
root. `RuntimeDirectoryPreserve=yes` retains the private `0700` directory and
last atomic snapshot between oneshot invocations. The worker binds the whole
safety runtime directory read-only, never the snapshot file inode, so later
atomic snapshot replacement remains visible.

Both services use `ProtectHome=tmpfs` with explicit narrow binds. The worker
also sees only the two immutable releases, manifests, protected authority,
semantic-input authority/tree, and its four exact writable artifact/output
roots. It does not see the active legacy root, `.env`, `.keys.enc`, dashboard
token, database owner credential, or exchange credential tree.

## Semantic refresh exception

The root semantic refresher binds only legacy `reports` and `memory/macro`
read-only. Its only writable binds are the protected semantic input and
authority directories. It has no IP address family. `CAP_CHOWN`, `CAP_FOWNER`
and `CAP_DAC_READ_SEARCH` are the documented narrow exception: the root process
must read UID 1000 private structured sources, then chown/chmod newly published
files to the runtime identity. All other units have empty capability sets.

The semantic timer is installed but remains disabled. Current sources exceed
the code-owned two-hour freshness window, so refresh fails closed and no active
semantic authority is minted.

## Environment and verification

User-manager environment files are UID/GID `1000:1000`, mode `0600`, because a
user manager cannot read root-owned `0600` files. Release manifests, command
manifest and runtime authority remain root-owned `0444`. The Job API token is
generated without output only in `job-api.env`; it is absent from worker,
scheduler and Control API environments. Provisioning reads two separately
protected database inputs: `postgres-jobs.env` must contain the non-owner
`trading_jobs` role, while `postgres-reader.env` must contain the read-only
`trading_reader` role used by `control-api.env`.

Run the focused tests and syntax verifier before installation. Direct
`systemd-analyze verify` of the exact files reports the intentionally absent
`/opt` interpreter before provisioning. The evidence procedure therefore also
verifies an unchanged staged copy with only `ExecStart`'s executable replaced
by `/bin/true`; exact production paths are asserted separately by tests. After
root installation, direct verification must pass against the real interpreter.

`verify-installed.sh` must run as UID 1000 after that user's manager has been
reloaded. It requires every `FragmentPath` to equal `/etc/systemd/user/<unit>`,
rejects `~/.config/systemd/user` shadows, requires timers disabled, and accepts
only loopback `127.0.0.1:8400` and `127.0.0.1:8401` listeners if either exists.
It preserves append-only runtime contents and validates their ownership, modes,
types and link policy. Pass `--require-semantic` during semantic acceptance to
run the independently attested application validator over the rotating active
authority and six structured inputs.

Effective `ExecStart` comparison uses only normalized configured `path` and
`argv[]`. Dynamic fields populated after execution—PID, timestamps, status and
exit code—are deliberately ignored. Unit SHA-256, global fragment path and
empty drop-ins still bind the effective configuration and altered path/argv is
rejected.

The final stage is
`/home/thenam176/.cache/trading-agent-phase4b-stage-fdc085a0-41f055b4-sealed-final`;
its approved metadata SHA-256 is
`f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c`.
The standalone verifier runs through `/usr/bin/python3 -I` before any staged
interpreter or helper and is pinned by installer code and staging metadata. The
former `a06a1160...`, `7aeaa300...`, `75f80edf...`, `27a83612...`, and
`1435864c...` metadata authorities are revoked. The last one represented
unavailable current order/trade counts as zero.
The signed metadata includes seal version and exact absolute stage path. After
the staging script prints the digest, do not run its interpreter or any helper;
post-seal audit uses only the system standalone verifier and read-only file/hash
tools.
