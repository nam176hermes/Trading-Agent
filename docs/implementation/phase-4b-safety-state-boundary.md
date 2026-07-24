# Phase 4B dynamic safety-state boundary

The boundary is implemented but not installed or started.

- A non-persistent two-second user timer invokes the exporter once.
- Each invocation binds only the exact required `.mode` file and optional
  `.kill_switch` file into a private `0700` runtime source directory.
- The two live gates are fixed `false/false` in the unit.
- `ProtectHome=tmpfs` prevents visibility of the legacy home tree; exact file
  binds are the only exception.
- `RuntimeDirectoryPreserve=yes` retains the last atomic snapshot between
  oneshot invocations.
- Every invocation gets a new mount namespace, so atomic replacement of either
  source inode is observed on the next refresh.
- The worker binds the entire safety runtime directory read-only. It does not
  bind the snapshot file inode, so exporter `rename(2)` replacements remain
  visible during heartbeat checks.
- The loopback read-only Control API binds the same protected runtime directory.
  Every operational-status read authenticates the protected authority before
  and after reading current safety evidence; it does not apply the worker-only
  spawn policy. Missing, stale, malformed, changed or unsafe evidence is shown
  as paper/non-live with an unknown kill-switch state instead of falling back to
  legacy dashboard files or claiming live authority.

The worker still validates schema, owner/mode, exporter commit, source
fingerprint, generated/expiry times, paper/paper, false/false and INACTIVE.
Missing, stale, malformed, wrong-owner or unsafe evidence blocks before spawn
and during heartbeat. No real mode, live gate or kill switch was changed for
testing.

Because the root install gate has not run, no safety snapshot exists and the
worker and Control API remain stopped/fail-closed. The Control API unit may
activate the safety timer through `Wants=` when it is later started, but
provisioning does not enable that timer. The scheduler and semantic refresh
timers also remain disabled.
