# Release Authority v2

**Status:** source implementation only; no candidate built, installed, or activated.

Release Authority v2 is the fail-closed provenance boundary for a future
canonical-monorepo release. It does not replace or reinterpret the Phase 4B/v1
authority. A v1 document is rejected by the v2 parser and verifier.

This document describes build and verification mechanics. It is not rollout
approval. PostgreSQL recovery/migration, root provisioning, service changes,
timer changes, and job enqueue remain separately approved operator actions.

## Two documents, two decisions

The static authority describes bytes that can be reviewed offline. It binds:

- one lowercase 40-character Git commit and its root tree, proven by the raw
  commit object plus reconstructed Git tree/blob object identities mapped to
  the exact moved stage paths;
- the exact `.` / `legacy/research-backend` / `apps/dashboard` source prefixes
  and tree IDs from that same commit;
- complete sealed `application`, stripped `backend`, and `dashboard` artifact
  sets;
- root/backend `uv.lock` and dashboard `package-lock.json` hashes;
- exact application/backend CPython identities and executable hashes;
- an absolute Node executable, identity, owner, mode, and hash;
- every committed generated contract path, size, and hash;
- the complete Alembic revision graph and migration-file hashes, with exactly
  one head `0006_job_transition_database_authority` descending through
  reviewed `0005_job_plane_role_split` to `0004_durable_research_jobs`;
- distinct `trading_job_api`, `trading_job_worker`, and
  `trading_job_scheduler` database-role names;
- one command only: `SNAPSHOT`, with exact immutable backend cwd/interpreter,
  `-I -B main.py --mode snapshot --research-only`, `shell=false`, and the
  empty-start allowlisted child-environment policy;
- disabled-by-default Job API and single-worker unit bytes, distinct service
  identities, distinct credential-file references, and normalized effective
  argv/cwd/DB role identities;
- no scheduler timer and no timer enablement links;
- the external standalone verifier identity/hash, stage path, owner/mode,
  seal version, complete file set, and reviewed prior-release digest.

The intended promotion document is separate and immutable: it will bind a
reviewed static-authority SHA-256 to fixed producer, protected-path, policy,
service-scope, and reviewed promotion-validity identities. The v2 activation
build/parse API is deliberately unavailable and always rejects until that
lifecycle receives a separate implementation and review. Absence is `NO_GO`;
`GO_LIVE_LIMITED` is unsupported. This source session creates no promotion
file. The fixed O_EXCL path is reserved for one future explicitly approved
`GO_PAPER_PRODUCTION` decision for this release, so a stored NO_GO placeholder
must not consume it.

Rotating safety and semantic evidence is not a process-lifetime promotion
credential. The runtime must independently reread and hash the current
root-owned documents before and after every startup, mutation, claim, and
spawn operation. Safety retains its exact six-second contract and semantic
evidence its code-owned freshness bound; their rotation does not mutate the
immutable promotion document or create a pointer ABA channel.

## Stage layout

The static builder produces three sibling outputs in a non-runtime cache path:

```text
<output>                       sealed stage, mode 0555
<output>.authority.json        canonical static authority, mode 0444
<output>.verify-stage.py       external stdlib verifier, mode 0555
```

The sealed stage has exactly four top-level artifact families:

```text
application/   canonical root export excluding the moved component subtrees
backend/       stripped legacy/research-backend subtree
dashboard/     stripped apps/dashboard subtree plus frozen build outputs
units/         Job API and one worker; no scheduler timer
```

Tracked timer templates may remain under `application/` as inert source bytes.
Only candidate `units/`, enablement links, and promotion scope are required to
contain no scheduler timer.

All stage directories are `0555`; regular files are `0444` or `0555`.
Symlinks, hardlinks, special files, extended attributes, mixed ownership,
unknown manifest keys, extra files, missing files, and writable modes reject
the stage. The verifier reads and hashes stage files but never imports or
executes them.

## Offline build gate

Do not run this command until Tasks 3 and 4 have landed, the canonical
worktree is clean at one reviewed commit, `0006` is the expected head, the
code-owned command policy is SNAPSHOT-only, and the prior authority digest has
been independently measured and reviewed.

```bash
bash ops/release-v2/build-stage.sh \
  --repo /home/thenam176/projects/trading-agent \
  --commit "$(git rev-parse HEAD)" \
  --output /var/tmp/trading-agent-release-v2/candidate \
  --prior-release-sha256 '<reviewed-sha256>' \
  --python '<reviewed-hermetic-python-3.11>' \
  --node /usr/bin/node \
  --npm /usr/bin/npm \
  --uv-cache '<absolute-reviewed-populated-uv-cache>' \
  --npm-cache '<absolute-reviewed-populated-npm-cache>'
```

The builder rejects a dirty index, dirty worktree, untracked files, a commit
other than exact `HEAD`, missing `0005` or `0006`, missing locks/contracts, a
preexisting output, an unsafe runtime Node, or absent/empty/writable offline caches. It
copies the operator-selected caches into its private build home before any
package operation, exports with `git archive`, builds only from frozen locks
in offline mode, and records identities before the final seal. After sealing,
an external composition process hashes the sealed bytes and the standalone
verifier is invoked twice through an empty environment plus the reviewed
Python `-I`; no staged Python, Node, shell, or application code runs. Python,
`uv`, and `npm` are build tools; the absolute Node executable is also runtime
authority and therefore must have a safe root-owned, non-writable ancestor
chain.

The current host has no reviewed root-owned/hermetic Python 3.11 runtime for
the dedicated service identities. A user-home UV Python and a venv whose
`pyvenv.cfg`, base stdlib, or `sys.path` escapes the sealed release must fail
preflight. This is a present build stop, not an invitation to substitute a
user-managed interpreter.

Independent verification requires the authority digest as an explicit
argument. No service environment variable supplies it:

```bash
/usr/bin/python3 -I '<output>.verify-stage.py' \
  '<output>' '<output>.authority.json' \
  --expected-authority-sha256 '<reviewed-authority-sha256>'
```

Expected output is exactly:

```text
release authority v2 stage verified
```

Any other result is `NO-GO`; do not provision or create an activation.

## Provisioning and rollback boundary

`provision-root.sh` is deliberately limited to isolated non-root fake-root
tests. It rejects root execution, `/`, and `--activate`; verifies the source
twice; copies to a create-only pending path; verifies the complete pending copy; seals and
atomically renames it, verifies the final installed-path bytes again, and then
publishes a digest-addressed authority copy. It never changes `current` or
`previous`, calls `systemctl`, writes PostgreSQL, starts a service, enables a
timer, or creates a release activation document. It is not a production-root
provisioning or cutover runbook.

The provisioning script uses its reviewed sibling verifier only after checking
an independently hard-coded SHA-256 pin. The candidate authority may bind the
same bytes, but cannot select different code for privileged verification. The
operator-supplied authority is copied into a private directory and checked
against the explicit reviewed digest before parsing. The candidate's mutable
cache-path verifier is never executed. Fake-root provisioning also exercises
create-only publication and re-attestation of the digest-bound stable verifier
at `/usr/libexec/trading-agent-v2/verify-stage.py`; it is not a real root
installation.

Because Task 5 never moves an active pointer, `rollback.sh` is a read-only
fake-root proof: it verifies that the prior authority is still current and
that both the prior release and create-only candidate remain preserved. A
future production provision/cutover/rollback runbook requires separate source,
tests, review, and exact operator approval.

## Current stop conditions

No real candidate may be built or provisioned while any of these is true:

- canonical Git status is dirty or Prompt 2 changes are not committed;
- PostgreSQL is unhealthy or not independently verified at reviewed head;
- migration `0005_job_plane_role_split` or
  `0006_job_transition_database_authority` is absent or unreviewed;
- any service would reuse shared database role `trading_jobs`;
- distinct Unix service identities cannot traverse service-owned protected
  safety/input/output/artifact roots without relying on the operator's private
  home; required root/ACL ownership policy is absent;
- static command authority exposes DEBATE, REPLAY, or BACKTEST;
- exact fresh safety or semantic evidence is absent/stale;
- external Node resolves through a user-managed shim rather than the reviewed
  absolute executable;
- the external verifier, stage path, unit bytes, effective commands, or prior
  digest differs from the reviewed evidence;
- exact operator approval for the provisioning/runbook action is absent;
- v2 activation/promotion lifecycle is deliberately unavailable and cannot
  authorize runtime startup;
- any code-owned runtime policy or command registry still pins the Phase 4B
  release commit/path instead of the reviewed v2 candidate;
- no reviewed relocatable/hermetic Python 3.11 runtime is available and bound;
- system-scope service accounts, `StateDirectory`/`RuntimeDirectory`, and exact
  read-only/read-write path policy are not provisioned and verified.

## Residual limitations requiring review

- Unit credential references use separate root-owned `EnvironmentFile` paths
  because current service composition reads environment variables. Moving to
  systemd credentials requires source support before those references can be
  changed; values must never enter the authority or logs.
- The static authority binds committed generated contract bytes. It does not
  claim that the current contract generator is self-contained: existing
  generation tooling must stop depending on an external dashboard checkout in
  a later source task.
- Unix service identities and root-owned install policy need the separately
  reviewed provisioning/cutover plan. Current protected runtime roots beneath
  `/home/thenam176` are not traversable by the candidate worker identity, so
  the unit is intentionally not runnable until service-owned roots/ACLs and
  source path bindings are reviewed. Passing fake-root tests is not runtime
  evidence.
- A static v2 reader and protected rotating-evidence plumbing do not constitute
  activation. Until the separately reviewed promotion lifecycle exists, a v2
  static document cannot authorize Job API or worker startup.
- Existing runtime constants/policies may still bind the old Phase 4B commit or
  release path. Every such pin must be inventoried and changed only as part of
  the reviewed v2 integration; static Task 5 tests do not resolve that drift.
- Dedicated service-owned safety, semantic-input, output, and artifact paths
  (or narrowly reviewed ACLs) are unresolved. Distinct service users must not
  fall back to a shared role or operator-home traversal merely to make units
  runnable.
- Candidate units with `User=`/`Group=` are system-manager units. They must
  never be treated as `systemctl --user` units; Task 5 installs and starts
  neither scope.
- A static candidate is not runtime authority. Without a separately published
  activation document, verified PostgreSQL role split, and approved service
  rollout, Job API/worker readiness must remain fail-closed.
