# Job Plane Systemd v2 Final Verification

**Evidence date:** 2026-07-16
**Status:** `NOT VERIFIED — NO CLEAN COMMIT OR MATERIALIZED RELEASE`

No final v2 unit set was rendered, installed, copied into a manager search
path, daemon-reloaded, started, or enabled. `systemd-analyze --user verify`
against final candidate paths was not run because the prerequisite clean
commit and hermetic release paths do not exist. Substituting `/bin/true`, an
operator worktree, or the old Phase 4 release would be syntax-only and would
not satisfy exact-path authority.

The source renderer intends only Job API and one worker, with paper mode,
false/false live gates, loopback `127.0.0.1:8401`, worker concurrency one,
separate DB role names, and no scheduler/timer output. Those are static source
properties, not final materialized-unit evidence.

Open exact-path blockers remain:

- no candidate interpreter, application module, backend command tree, or
  manifest/promotion path;
- no final candidate commit to bind in `WorkingDirectory`/`ExecStart`;
- current `/opt` paths are absent and production installation is outside this
  session;
- distinct service users and protected per-role environment files are not
  provisioned;
- current v2 renderer uses system-unit `User=`/`Group=` separation while the
  requested command selects the user-manager verifier scope;
- protected v2 authority, activation, current safety, and semantic evidence
  paths are absent;
- worker write roots and complete home/legacy/credential masking remain
  incomplete.

Runtime Job units stayed inactive and uninstalled by Part 2. The scheduler
timer stayed disabled. Port 8401 stayed closed.
