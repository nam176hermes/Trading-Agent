# PostgreSQL Job-Plane Role Split (`0004` → `0005`) Runbook

**Status:** REVIEWED PROCEDURE — NOT EXECUTION APPROVAL

This document is a forward-only B1 database sub-gate. It does not authorize a
PostgreSQL recovery or start, a service/timer change, a release activation, an
enqueue, a research child, a provider call, or live trading. Every mutating
step below requires an exact, time-bounded operator and independent-reviewer
approval for this runbook hash, source identity, database identity, and backup
destinations. Without that record, stop after read-only inspection.

This runbook ends with PostgreSQL at `0005_job_plane_role_split` and every Job
API/worker/scheduler unit still inactive. A later, separately approved rollout
may start the Job API and one worker. The scheduler service and timer remain
disabled. No command in this runbook was executed while authoring it.

## 1. Scope and dependency boundary

The only authorized state change is:

1. provision three independent LOGIN roles with three distinct protected
   credentials and make `trading_jobs` NOLOGIN;
2. apply the reviewed, transactional
   `0005_job_plane_role_split` migration to the exact recovered PostgreSQL 16
   cluster at authenticated head `0004_durable_research_jobs`;
3. verify unchanged rows/integrity, exact ACLs, RLS, namespace policy, role
   separation, a pre/post logical backup, and a secret-free evidence bundle;
4. stop before any service or timer activation.

Dependencies that must already be independently accepted:

- the original-cluster recovery procedure in
  [`postgresql-preserve-recover.md`](./postgresql-preserve-recover.md) has
  produced reviewed evidence for the original PostgreSQL 16 system identity;
- PostgreSQL is already healthy and listening on the approved loopback target;
  this runbook never starts or recovers it;
- authenticated database head is exactly `0004_durable_research_jobs`;
- the reviewed `0004` logical dump and preservation evidence are readable and
  hash-valid; the historical `0003_contract_lineage_repair` dump is fallback
  evidence only and is never an input to this operation;
- Release Authority v2 static evidence binds the exact source commit/tree,
  migration hash, provisioning-script hash, generated contracts, SNAPSHOT-only
  command manifest, units, and expected Alembic `0005` head;
- requested/effective mode is `paper/paper`, both live gates are false, and the
  current safety and semantic evidence are valid. These facts do not authorize
  this runbook; they are additional stop gates.

If PostgreSQL is down, unauthenticated, unhealthy, in recovery, at `0003`, at
multiple heads, or already at an unknown revision, stop. Return to the frozen
recovery/change-control process. Do not start PostgreSQL or improvise a
migration here.

## 2. Immutable targets and expected identities

| Field | Required value |
|---|---|
| PostgreSQL major | 16 |
| Host / port | `127.0.0.1:55432` |
| Database | `trading_agent` |
| Pre-head | `0004_durable_research_jobs` |
| Post-head | `0005_job_plane_role_split` |
| Migration executor | `trading_owner` |
| Retired shared role | `trading_jobs`, exact `NOLOGIN` |
| API role | `trading_job_api`, independent `LOGIN` |
| Worker role | `trading_job_worker`, independent `LOGIN` |
| Scheduler role | `trading_job_scheduler`, independent `LOGIN` |
| Runtime job types after this gate | `SNAPSHOT` only through RLS |
| Scheduler state | service inactive, timer disabled/inactive |

The three runtime roles must be `NOSUPERUSER NOCREATEDB NOCREATEROLE
NOINHERIT NOREPLICATION NOBYPASSRLS`, have no memberships in either direction,
and use pairwise-distinct credentials. A credential is recorded only as a
protected input identity (owner/mode/inode), never as content, hash, prefix,
length, DSN, URI, or environment dump.

## 3. Absolute prohibitions and stop conditions

Stop before the next mutation, retain evidence, and keep all job units inactive
on any mismatch.

- Do not recover, start, stop, restart, reinitialize, restore over, or alter
  PostgreSQL outside the exact approval record.
- Do not start/restart/enable/disable any application service or timer under
  this database runbook. An active consumer is a stop condition, not permission
  to stop it.
- Do not run `DEBATE`, `REPLAY`, `BACKTEST`, SNAPSHOT, enqueue, cancellation,
  dashboard command, child process, broker, exchange, provider, or credential
  probe.
- Do not source/eval credential files; print an environment; place a password,
  DSN, or URI on a command line; enable shell tracing; retain raw error output
  before a secret scan; or query `rolpassword` values.
- Do not reuse `trading_jobs`, copy one credential to another role, grant role
  memberships, grant `BYPASSRLS`, or make a runtime role an owner.
- Do not use `GRANT ALL`, default grants to a runtime role, table-wide UPDATE
  for the API/scheduler, SECURITY DEFINER helpers, free-form SQL/argv, or an
  application-supplied role name.
- Do not edit `alembic_version`, rerun a failed migration, run `downgrade`,
  delete rows/events/artifacts, truncate, drop, or repair ACLs interactively.
- Stop on a dirty or mismatched source tree, wrong release/static-authority
  digest, stale/absent safety or semantic evidence, wrong database/system
  identity, non-16 server, recovery mode, wrong/multiple head, invalid
  constraint/index/trigger, orphan, unexpected count drift, unreadable backup,
  role membership, target-role session, ACL/default-ACL difference, RLS/policy
  difference, or failed cross-role denial.
- Stop if any final dump/evidence path or `.partial` sibling already exists, is
  a symlink, has the wrong owner/mode/link count, overlaps the data directory,
  or is not on the approved independent storage.

The migration is atomic, but cluster-global role provisioning is not rolled
back by Alembic. If provisioning succeeds and any later gate fails, leave the
new roles closed by zero object privileges, keep `trading_jobs` NOLOGIN, retain
all evidence, and use a separately reviewed forward repair.

## 4. Exact execution approval

Before any password input, role change, backup, migration, or rolled-back
permission probe, require an access-controlled record with exactly these
non-secret fields. The change-control system authenticates the operator and
reviewer; this file does not.

~~~text
DECISION	APPROVED_JOB_PLANE_0005_ROLE_SPLIT
CHANGE_ID	<approved identifier>
INCIDENT_ID	<approved database recovery/evidence identifier>
APPROVED_AT_UTC	<YYYY-MM-DDTHH:MM:SSZ>
EXPIRES_AT_UTC	<no more than four hours later>
OPERATOR_NAME	<named operator>
REVIEWER_NAME	<different named reviewer>
OPERATOR_ATTESTATION	I_APPROVE_THIS_EXACT_0005_ROLE_SPLIT
REVIEWER_ATTESTATION	I_INDEPENDENTLY_REVIEWED_THIS_EXACT_0005_ROLE_SPLIT
RUN_ID	<safe unique identifier>
RUNBOOK_SHA256	<64 lowercase hex>
SOURCE_COMMIT	<40 lowercase hex>
SOURCE_TREE	<40 lowercase hex>
RELEASE_AUTHORITY_V2_SHA256	<64 lowercase hex>
SEALED_STAGE_ROOT	<approved canonical sealed Release Authority v2 stage>
STATIC_AUTHORITY_PATH	<approved canonical static-authority document>
EXTERNAL_VERIFIER_PATH	<approved canonical external verifier>
EXTERNAL_VERIFIER_SHA256	<64 lowercase hex>
SEALED_APPLICATION_PYTHON_SHA256	<64 lowercase hex>
HOST_VERIFIER_PYTHON	<approved root-owned hermetic CPython>
HOST_VERIFIER_PYTHON_SHA256	<64 lowercase hex>
SYSTEMD_MANAGER_SCOPE	system
SAFETY_EVIDENCE_SHA256	<64 lowercase hex>
SEMANTIC_EVIDENCE_SHA256	<64 lowercase hex>
MIGRATION_0005_SHA256	<64 lowercase hex>
PROVISION_JOB_ROLES_SHA256	<64 lowercase hex>
RECOVERY_EVIDENCE_ID	<reviewed original-cluster evidence bundle>
RECOVERY_EVIDENCE_SHA256	<64 lowercase hex>
PREVIOUS_0004_DUMP_SHA256	<64 lowercase hex>
ORIGINAL_SYSTEM_ID	<reviewed decimal PostgreSQL system identifier>
EXPECTED_PRE_HEAD	0004_durable_research_jobs
EXPECTED_POST_HEAD	0005_job_plane_role_split
EXPECTED_DATABASE	trading_agent
EXPECTED_HOST	127.0.0.1
EXPECTED_PORT	55432
EVIDENCE_PARENT	<existing canonical private 0700 independent-storage directory>
BACKUP_PARENT	<existing canonical private 0700 independent-storage directory>
SECRET_PARENT	<existing canonical private 0700 runtime directory>
ADMIN_PGPASSFILE	<canonical protected PostgreSQL-admin passfile>
OWNER_PGPASSFILE	<canonical protected trading_owner passfile>
OWNER_DATABASE_ENV_FILE	<canonical protected exact five-key owner environment>
API_PASSWORD_FILE	<canonical protected one-line input>
WORKER_PASSWORD_FILE	<canonical protected one-line input>
SCHEDULER_PASSWORD_FILE	<canonical protected one-line input>
ALLOW_PRE_0005_LOGICAL_BACKUP	YES
ALLOW_PROVISION_EXACT_JOB_ROLES	YES
ALLOW_APPLY_EXACT_0005	YES
ALLOW_ROLLED_BACK_PERMISSION_PROBES	YES
ALLOW_POST_0005_LOGICAL_BACKUP	YES
KEEP_ALL_JOB_SERVICES_INACTIVE	YES
KEEP_SCHEDULER_DISABLED	YES
~~~

Reject
blank, duplicate, extra, placeholder, expired, malformed, secret-bearing, or
same-person operator/reviewer fields. The reviewer independently verifies every
hash and target before approval. The approval record never contains a password
or DSN.

## 5. Protected command wrapper

The following is a documented pattern, not a command authorized by this file.
Run it only after the exact approval above is validated. Store the transcript
under the approved private evidence directory with `set +x` and `umask 077`.

~~~bash
set -euo pipefail
set +x
umask 077

readonly PG_BIN=/usr/lib/postgresql/16/bin
readonly DB_HOST=127.0.0.1
readonly DB_PORT=55432
readonly DB_NAME=trading_agent
readonly PRE_HEAD=0004_durable_research_jobs
readonly POST_HEAD=0005_job_plane_role_split
readonly SEALED_STAGE_ROOT="<approved-sealed-stage-root>"
readonly STATIC_AUTHORITY="<approved-static-authority-path>"
readonly STATIC_AUTHORITY_SHA256="<approved-static-authority-sha256>"
readonly EXTERNAL_VERIFIER="<approved-external-verifier-path>"
readonly EXTERNAL_VERIFIER_SHA256="<approved-external-verifier-sha256>"
readonly HOST_VERIFIER_PYTHON="<approved-root-owned-verifier-python>"
readonly HOST_VERIFIER_PYTHON_SHA256="<approved-verifier-python-sha256>"
readonly SYSTEMD_MANAGER_SCOPE=system
readonly APPLICATION_ROOT="$SEALED_STAGE_ROOT/application"
readonly SEALED_PYTHON="$APPLICATION_ROOT/.venv/bin/python3.11"
readonly MIGRATION="$APPLICATION_ROOT/alembic/versions/0005_job_plane_role_split.py"
readonly PROVISION="$APPLICATION_ROOT/ops/postgres/provision-job-roles.sql"
readonly RUNBOOK="$APPLICATION_ROOT/docs/production/runbooks/job-plane-role-split-rollout.md"

# Bind these only from the already-validated approval record. Do not source it.
readonly EVIDENCE_DIR="<approved-new-evidence-directory>"
readonly BACKUP_DIR="<approved-new-backup-directory>"
readonly SECRET_DIR="<approved-new-secret-directory>"
readonly ADMIN_PGPASSFILE="<approved-admin-passfile>"
readonly OWNER_PGPASSFILE="<approved-owner-passfile>"
readonly OWNER_DATABASE_ENV_FILE="<approved-owner-database-env-file>"
readonly API_PASSWORD_FILE="<approved-api-password-file>"
readonly WORKER_PASSWORD_FILE="<approved-worker-password-file>"
readonly SCHEDULER_PASSWORD_FILE="<approved-scheduler-password-file>"

unset PGAPPNAME PGDATABASE PGHOST PGHOSTADDR PGOPTIONS PGPASSFILE PGPASSWORD
unset PGPORT PGSERVICE PGSERVICEFILE PGSSLMODE PGTARGETSESSIONATTRS PGUSER
unset DATABASE_URL TRADING_DATABASE_URL

run_psql() {
  local passfile="$1" user="$2" application="$3"
  shift 3
  env -i LC_ALL=C PGAPPNAME="$application" PGCONNECT_TIMEOUT=10 \
    PGPASSFILE="$passfile" PGSSLMODE=disable \
    PGTARGETSESSIONATTRS=primary \
    "$PG_BIN/psql" -X -q -w --set=ON_ERROR_STOP=1 \
      --host "$DB_HOST" --port "$DB_PORT" \
      --username "$user" --dbname "$DB_NAME" "$@"
}

owner_psql() {
  run_psql "$OWNER_PGPASSFILE" trading_owner job-role-split-owner "$@"
}

admin_psql() {
  run_psql "$ADMIN_PGPASSFILE" postgres job-role-split-admin "$@"
}

verify_sealed_release() {
  test "$SYSTEMD_MANAGER_SCOPE" = system
  test "$(sha256sum "$EXTERNAL_VERIFIER" | awk '{print $1}')" \
    = "$EXTERNAL_VERIFIER_SHA256"
  test "$(sha256sum "$STATIC_AUTHORITY" | awk '{print $1}')" \
    = "$STATIC_AUTHORITY_SHA256"
  test "$(sha256sum "$HOST_VERIFIER_PYTHON" | awk '{print $1}')" \
    = "$HOST_VERIFIER_PYTHON_SHA256"
  env -i LC_ALL=C "$HOST_VERIFIER_PYTHON" -I "$EXTERNAL_VERIFIER" \
    "$SEALED_STAGE_ROOT" "$STATIC_AUTHORITY" \
    --expected-authority-sha256 "$STATIC_AUTHORITY_SHA256" \
    --content-copy
}
~~~

Never replace placeholders without first binding them from the approved record.

## 6. Phase 0 — read-only identity and source gates

This phase performs no database or runtime write.

1. Prove `SEALED_STAGE_ROOT` is the exact root-owned installed release bound by
   approval; never execute migration tooling from a mutable checkout or a
   user-owned `/var/tmp` candidate.
2. Independently verify the sealed Release Authority v2 static document twice.
   It must bind this exact `0005` and the SNAPSHOT-only command manifest. Do not
   create or activate a release record here.
3. Prove the approved manager scope is exactly `system`. At user scope, all
   same-named shadow units must be absent or inactive and disabled. At system
   scope, candidate API/worker/scheduler service and timer must be absent or
   inactive; every loaded unit must be disabled and the timer disabled. Any
   active consumer or enabled shadow stops this runbook. Do not stop it here.
4. Use the repository's read-only cluster verification path and authenticated
   catalog queries. A TCP listener alone is not health evidence.

Documented sealed-source checks:

~~~bash
[[ "$SEALED_STAGE_ROOT" =~ ^/opt/trading-agent-v2/releases/[0-9a-f]{40}$ ]]
[[ "$STATIC_AUTHORITY" =~ ^/etc/trading-agent/release-authority-v2/[0-9a-f]{64}\.json$ ]]
[[ "$EXTERNAL_VERIFIER" =~ ^/usr/(local/)?libexec/trading-agent/[A-Za-z0-9._-]+$ ]]
test -d "$SEALED_STAGE_ROOT" && test ! -L "$SEALED_STAGE_ROOT"
test -f "$MIGRATION" && test ! -L "$MIGRATION"
test -f "$PROVISION" && test ! -L "$PROVISION"
test -x "$SEALED_PYTHON" && test ! -L "$SEALED_PYTHON"

assert_root_sealed_chain() {
  local target="$1" current mode
  test "$(realpath -e -- "$target")" = "$target"
  current="$target"
  while :; do
    test "$(stat -c '%U:%G' "$current")" = root:root
    mode=$(stat -c '%a' "$current")
    test $((8#$mode & 8#022)) -eq 0
    test "$current" = / && break
    current=$(dirname -- "$current")
  done
}
assert_root_sealed_chain "$SEALED_STAGE_ROOT"
assert_root_sealed_chain "$SEALED_PYTHON"
assert_root_sealed_chain "$STATIC_AUTHORITY"
assert_root_sealed_chain "$EXTERNAL_VERIFIER"
assert_root_sealed_chain "$HOST_VERIFIER_PYTHON"
verify_sealed_release
verify_sealed_release
test "$(sha256sum "$RUNBOOK" | awk '{print $1}')" = "<approved-runbook-sha256>"
test "$(sha256sum "$MIGRATION" | awk '{print $1}')" = "<approved-migration-sha256>"
test "$(sha256sum "$PROVISION" | awk '{print $1}')" = "<approved-provision-sha256>"
test "$(sha256sum "$SEALED_PYTHON" | awk '{print $1}')" \
  = "<approved-sealed-application-python-sha256>"

systemctl --user show \
  trading-job-api.service trading-job-worker.service \
  trading-job-scheduler.service trading-job-scheduler.timer \
  -p Id -p LoadState -p ActiveState -p SubState -p UnitFileState \
  > "$EVIDENCE_DIR/job-units-user-before.txt"
systemctl show \
  trading-job-api.service trading-job-worker.service \
  trading-job-scheduler.service trading-job-scheduler.timer \
  -p Id -p LoadState -p ActiveState -p SubState -p UnitFileState \
  > "$EVIDENCE_DIR/job-units-system-before.txt"
~~~

The reviewer parses each four-unit record and requires `ActiveState=inactive`
(or `LoadState=not-found`) in both scopes and `UnitFileState=disabled` (or
`not-found`) for every loaded unit. A user-scope shadow is never an acceptable
substitute for the approved system scope. The current Release Authority v2
source provisioning helper is fake-root-only; until a separately reviewed
production installer has created the exact root-owned release, authority,
stable verifier, hermetic Python, and ancestor chain above, this runbook must
STOP before backup or role mutation.

`--content-copy` is mandatory because the static authority's original build
path differs from the installed `/opt` content copy. This runbook deliberately
does not use `--verifier-copy-of`: the static authority itself must bind the
exact stable root-owned `EXTERNAL_VERIFIER` path executed here. An authority
that still binds a user-owned build-cache verifier is a hard stop.

Authenticated identity/head gate:

~~~sql
BEGIN READ ONLY;
SELECT current_setting('server_version_num');
SELECT current_setting('data_directory');
SELECT current_setting('port');
SELECT current_setting('listen_addresses');
SELECT pg_is_in_recovery();
SELECT current_database();
SELECT current_user;
SELECT count(*) || '|' || min(version_num) || '|' || max(version_num)
FROM public.alembic_version;
SELECT system_identifier FROM pg_catalog.pg_control_system();
COMMIT;
~~~

Require PostgreSQL major 16, approved data directory/system identifier, port
55432, loopback-only listener, `pg_is_in_recovery=false`, database
`trading_agent`, user `trading_owner`, and exactly
`1|0004_durable_research_jobs|0004_durable_research_jobs`.

## 7. Phase 1 — preserve pre-0005 rows, integrity, and ACL evidence

Capture each query under `BEGIN READ ONLY` with explicit owner/admin wrappers.
Output contains names, counts, booleans, and definitions only—never connection
strings, settings arrays that may contain secrets, or password hashes.

Required pre-state evidence:

- counts for all canonical tables and exact counts for `jobs`, `job_attempts`,
  `job_events`, `scheduler_heartbeats`, `job_artifacts`, and
  `worker_heartbeats`;
- zero unvalidated public constraints and zero invalid/not-ready/not-live public
  indexes;
- exact `reject_job_event_mutation()` function and enabled
  `trg_job_events_append_only` trigger;
- zero orphan/cross-attempt rows across jobs, attempts, events, artifacts, and
  heartbeats;
- full job-table table ACL, column ACL, default ACL, RLS flag, and policy set;
- exact role attributes/memberships and active-session counts, without reading
  credential values;
- current safety evidence identity and semantic evidence identity as already
  attested by Release Authority v2 tooling.

The six pre-counts are frozen into `pre-job-counts.txt` and byte-compared to
post-migration counts. A nonzero count is not automatically failure if it is
already part of the independently reviewed recovery evidence; unexplained
drift is always failure. Before the first controlled SNAPSHOT rollout, expected
runtime job-plane counts remain zero.

The ACL capture must classify the known `0004` leakage—reader/migrator grants
inherited from `trading_owner` default ACLs—without repairing it. Any ACL not
explained by the reviewed `0004` evidence stops before provisioning.

Before any backup or role mutation, require exactly zero rows incompatible
with the future validated namespace constraint. This count may not be waived
because role retirement is cluster-global while Alembic is transactional:

~~~sql
BEGIN READ ONLY;
SELECT count(*) AS schedule_namespace_violations
FROM public.jobs
WHERE NOT (
  (
    actor_type = 'SCHEDULER'
    AND job_type = 'SNAPSHOT'
    AND priority = 0
    AND idempotency_key ~
      '^schedule:snapshot:[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]Z$'
    AND pg_catalog.pg_input_is_valid(
      substring(
        idempotency_key FROM
        '^schedule:snapshot:([0-9]{4}-[0-9]{2}-[0-9]{2})T'
      ),
      'date'
    )
  )
  OR (actor_type <> 'SCHEDULER' AND idempotency_key !~ '^schedule:')
);
COMMIT;
~~~

The only accepted result is `0`. Also require exact `trading_owner` ownership
of `alembic_version`, all six job tables, and the append-only trigger function;
require exactly the one reviewed non-internal append-only trigger across all
six tables, including no predicate, arguments, column filter, or constraint
binding. Any difference stops before provisioning.

Zero target sessions is mandatory immediately before provisioning and again
before Alembic:

~~~sql
BEGIN READ ONLY;
SELECT usename || '|' || count(*)
FROM pg_catalog.pg_stat_activity
WHERE usename IN (
  'trading_jobs', 'trading_job_api',
  'trading_job_worker', 'trading_job_scheduler'
)
  AND pid <> pg_backend_pid()
GROUP BY usename
ORDER BY usename;
COMMIT;
~~~

Success is empty output. Never terminate a session under this runbook.

## 8. Phase 2 — durable pre-0005 logical backup

Create one collision-safe PostgreSQL 16 custom-format dump on approved
independent storage before changing roles. Use `trading_owner`, explicit
host/port/database, `--no-password`, `--no-publications`, and
`--no-subscriptions`. Reject existing final and `.partial` paths. Set mode
`0600`, validate `pg_restore --list`, scan the TOC for unexpected side-effect
objects, atomically rename, hash, and sync the file and parent directory.

~~~bash
readonly PRE_DUMP="$BACKUP_DIR/trading_agent-<run-id>-0004-before-role-split.dump"
test ! -e "$PRE_DUMP"
test ! -e "$PRE_DUMP.partial"

env -i LC_ALL=C PGAPPNAME=job-role-split-pre-backup \
  PGCONNECT_TIMEOUT=10 PGPASSFILE="$OWNER_PGPASSFILE" \
  PGSSLMODE=disable PGTARGETSESSIONATTRS=primary \
  "$PG_BIN/pg_dump" --host "$DB_HOST" --port "$DB_PORT" \
    --username trading_owner --dbname "$DB_NAME" --no-password \
    --format=custom --serializable-deferrable \
    --no-publications --no-subscriptions --file "$PRE_DUMP.partial"

env -i LC_ALL=C "$PG_BIN/pg_restore" --list "$PRE_DUMP.partial" \
  > "$EVIDENCE_DIR/pre-0005-dump.catalog"
chmod 0600 "$PRE_DUMP.partial"
mv "$PRE_DUMP.partial" "$PRE_DUMP"
sync -f "$PRE_DUMP"
sync -f "$BACKUP_DIR"
sha256sum "$PRE_DUMP" > "$EVIDENCE_DIR/pre-0005-dump.sha256"
stat -c 'owner=%U mode=%a links=%h size=%s' "$PRE_DUMP" \
  > "$EVIDENCE_DIR/pre-0005-dump.stat"
~~~

Stop on any warning/error, unreadable catalog, unexpected publication,
subscription, event trigger, foreign object, extension, procedural language,
or mismatch with the preserved 0004 catalog. Do not reuse a partial name.

## 9. Phase 3 — provision independent cluster roles

This is the first cluster-global mutation. Revalidate approval time, source
hashes, database identity/head, pre-backup hash, inactive job units, and zero
target-role sessions immediately before proceeding.

Each password input file must be a distinct canonical regular file, owner-only
`0600`, one link, one nonempty line, no NUL/CR, and at least 24 characters.
Compare the three in memory for inequality; never print, hash, or persist their
content. This is a first-use operation: all three target role names must be
absent. The provisioning script uses psql's `\password` command, which hashes
client-side before sending SQL; a detached protected-stdin driver supplies
each value and confirmation, so cleartext never appears in server statement
text or process arguments.

Documented invocation pattern:

~~~bash
read_one_secret() {
  local path="$1" variable_name="$2" value
  test -f "$path" && test ! -L "$path"
  test "$(stat -c '%U' "$path")" = thenam176
  test "$(stat -c '%a' "$path")" = 600
  test "$(stat -c '%h' "$path")" = 1
  test "$(wc -l < "$path")" -eq 1
  test "$(tail -c 1 -- "$path" | od -An -t u1 | tr -d '[:space:]')" = 10
  test "$(LC_ALL=C tr -d '\12\40-\176' < "$path" | wc -c)" -eq 0
  IFS= read -r value < "$path"
  test -n "$value" && test "${#value}" -ge 24
  printf -v "$variable_name" '%s' "$value"
}

test "$(realpath -e -- "$API_PASSWORD_FILE")" != \
  "$(realpath -e -- "$WORKER_PASSWORD_FILE")"
test "$(realpath -e -- "$API_PASSWORD_FILE")" != \
  "$(realpath -e -- "$SCHEDULER_PASSWORD_FILE")"
test "$(realpath -e -- "$WORKER_PASSWORD_FILE")" != \
  "$(realpath -e -- "$SCHEDULER_PASSWORD_FILE")"
test "$(stat -c '%d:%i' "$API_PASSWORD_FILE")" != \
  "$(stat -c '%d:%i' "$WORKER_PASSWORD_FILE")"
test "$(stat -c '%d:%i' "$API_PASSWORD_FILE")" != \
  "$(stat -c '%d:%i' "$SCHEDULER_PASSWORD_FILE")"
test "$(stat -c '%d:%i' "$WORKER_PASSWORD_FILE")" != \
  "$(stat -c '%d:%i' "$SCHEDULER_PASSWORD_FILE")"
read_one_secret "$API_PASSWORD_FILE" JOB_API_PASSWORD
read_one_secret "$WORKER_PASSWORD_FILE" JOB_WORKER_PASSWORD
read_one_secret "$SCHEDULER_PASSWORD_FILE" JOB_SCHEDULER_PASSWORD
test "$JOB_API_PASSWORD" != "$JOB_WORKER_PASSWORD"
test "$JOB_API_PASSWORD" != "$JOB_SCHEDULER_PASSWORD"
test "$JOB_WORKER_PASSWORD" != "$JOB_SCHEDULER_PASSWORD"
verify_sealed_release
verify_sealed_release

set +e
{
  printf '\\i %s\n' "$PROVISION"
  printf '%s\n%s\n' "$JOB_API_PASSWORD" "$JOB_API_PASSWORD"
  printf '%s\n%s\n' "$JOB_WORKER_PASSWORD" "$JOB_WORKER_PASSWORD"
  printf '%s\n%s\n' "$JOB_SCHEDULER_PASSWORD" "$JOB_SCHEDULER_PASSWORD"
} | env -i LC_ALL=C PGAPPNAME=job-role-split-provision \
  PGCONNECT_TIMEOUT=10 PGPASSFILE="$ADMIN_PGPASSFILE" \
  PGSSLMODE=disable PGTARGETSESSIONATTRS=primary \
  PGOPTIONS='-c password_encryption=scram-sha-256 -c log_statement=none -c log_min_error_statement=panic -c log_min_duration_statement=-1 -c log_min_duration_sample=-1 -c log_parameter_max_length_on_error=0 -c log_duration=off -c debug_print_parse=off -c debug_print_rewritten=off -c debug_print_plan=off -c log_parser_stats=off -c log_planner_stats=off -c log_executor_stats=off -c log_statement_stats=off -c track_activities=off' \
  /usr/bin/setsid --wait "$PG_BIN/psql" -X -w \
    --host "$DB_HOST" --port "$DB_PORT" \
    --username postgres --dbname "$DB_NAME" \
    > "$SECRET_DIR/provision.stdout.raw" \
    2> "$SECRET_DIR/provision.stderr.raw"
provision_rc=$?
set -e

# Quietly scan both raw files for each exact input before promoting any output.
# A match, scan failure, or nonzero provisioning exit is a hard stop. Raw files
# remain protected incident material until the reviewer authorizes deletion.
secret_scan_clear=true
for raw_output in \
  "$SECRET_DIR/provision.stdout.raw" "$SECRET_DIR/provision.stderr.raw"
do
  for secret_input in \
    "$API_PASSWORD_FILE" "$WORKER_PASSWORD_FILE" "$SCHEDULER_PASSWORD_FILE"
  do
    set +e
    LC_ALL=C grep -Fq -f "$secret_input" "$raw_output"
    scan_rc=$?
    set -e
    test "$scan_rc" -eq 1 || secret_scan_clear=false
  done
done
unset JOB_API_PASSWORD JOB_WORKER_PASSWORD JOB_SCHEDULER_PASSWORD
printf 'exit_code=%s\nsecret_scan_clear=%s\n' \
  "$provision_rc" "$secret_scan_clear" \
  > "$EVIDENCE_DIR/provision-job-roles.exit"
test "$secret_scan_clear" = true
test "$provision_rc" -eq 0
rm -f -- "$SECRET_DIR/provision.stdout.raw" \
  "$SECRET_DIR/provision.stderr.raw"
~~~

The wrapper never copies raw stdout/stderr into the secret-free evidence
bundle. If provisioning or scanning fails, do not retry or delete protected raw
incident output. This operation is intentionally non-idempotent. If any target
role exists or the shared role is already retired, keep all services stopped
and escalate; never retry this first-use script.

Post-provision admin verification (no password columns):

- `trading_jobs`: exact NOLOGIN/no-power attributes;
- each new role: exact LOGIN/no-power attributes;
- zero memberships in either direction;
- zero target-role sessions;
- pairwise credentials were supplied from three distinct protected files;
- no object privilege is assumed before migration.

## 10. Phase 4 — apply exact transactional `0005`

Re-run authenticated head/identity, sealed-authority/hash, backup, role, zero
session, and append-only-trigger gates. The migration must run as
`trading_owner` from the exact Release Authority v2 source and frozen root lock.
Scope the password to one clean child environment; never print it or put it in
argv. Capture raw Alembic output under the secret directory, screen it before
promoting only exit/byte-count metadata, then delete it under the reviewed
secret-retention policy.

~~~bash
test "$(owner_psql -Atc 'SELECT version_num FROM public.alembic_version')" \
  = "$PRE_HEAD"
verify_sealed_release
verify_sealed_release

read_exact_owner_environment() {
  local path="$1" line key value
  local -A seen=() values=()
  test -f "$path" && test ! -L "$path"
  test "$(stat -c '%U' "$path")" = thenam176
  test "$(stat -c '%a' "$path")" = 600
  test "$(stat -c '%h' "$path")" = 1
  test "$(LC_ALL=C tr -d '\11\12\40-\176' < "$path" | wc -c)" -eq 0
  while IFS= read -r line || test -n "$line"; do
    test -n "$line" && test "${line#*=}" != "$line"
    key=${line%%=*}
    value=${line#*=}
    case "$key" in
      TRADING_DATABASE_HOST|TRADING_DATABASE_PORT|TRADING_DATABASE_NAME|\
      TRADING_DATABASE_USER|TRADING_DATABASE_PASSWORD) ;;
      *) return 1 ;;
    esac
    test -z "${seen[$key]+present}"
    seen[$key]=1
    values[$key]="$value"
  done < "$path"
  test "${#seen[@]}" -eq 5
  test "${values[TRADING_DATABASE_HOST]}" = "$DB_HOST"
  test "${values[TRADING_DATABASE_PORT]}" = "$DB_PORT"
  test "${values[TRADING_DATABASE_NAME]}" = "$DB_NAME"
  test "${values[TRADING_DATABASE_USER]}" = trading_owner
  test -n "${values[TRADING_DATABASE_PASSWORD]}"
  OWNER_PASSWORD=${values[TRADING_DATABASE_PASSWORD]}
}

read_exact_owner_environment "$OWNER_DATABASE_ENV_FILE"
readonly OWNER_PATTERN="$SECRET_DIR/owner-password.pattern"
test ! -e "$OWNER_PATTERN"
printf '%s\n' "$OWNER_PASSWORD" > "$OWNER_PATTERN"
chmod 0600 "$OWNER_PATTERN"

set +e
(
  set +x
  cd "$APPLICATION_ROOT"
  env -i LC_ALL=C HOME="$SECRET_DIR" PATH=/usr/bin:/bin \
    TRADING_DATABASE_HOST="$DB_HOST" \
    TRADING_DATABASE_PORT="$DB_PORT" \
    TRADING_DATABASE_NAME="$DB_NAME" \
    TRADING_DATABASE_USER=trading_owner \
    TRADING_DATABASE_PASSWORD="$OWNER_PASSWORD" \
    "$SEALED_PYTHON" -I -m alembic -c "$APPLICATION_ROOT/alembic.ini" \
      upgrade "$POST_HEAD"
) > "$SECRET_DIR/alembic-0005.stdout.raw" \
  2> "$SECRET_DIR/alembic-0005.stderr.raw"
alembic_rc=$?
set -e
unset OWNER_PASSWORD

alembic_secret_scan_clear=true
for raw_output in \
  "$SECRET_DIR/alembic-0005.stdout.raw" \
  "$SECRET_DIR/alembic-0005.stderr.raw"
do
  set +e
  LC_ALL=C grep -Fq -f "$OWNER_PATTERN" "$raw_output"
  scan_rc=$?
  set -e
  test "$scan_rc" -eq 1 || alembic_secret_scan_clear=false
done
printf 'exit_code=%s\nsecret_scan_clear=%s\n' \
  "$alembic_rc" "$alembic_secret_scan_clear" \
  > "$EVIDENCE_DIR/alembic-0005.exit"
test "$alembic_secret_scan_clear" = true
test "$alembic_rc" -eq 0
rm -f -- "$SECRET_DIR/alembic-0005.stdout.raw" \
  "$SECRET_DIR/alembic-0005.stderr.raw" "$OWNER_PATTERN"
~~~

The reviewed launcher injects the already-parsed protected value without
logging it and unsets it immediately afterward. A nonzero exit is final:
do not rerun, downgrade, edit the revision, or edit `alembic_version`. Query the
head read-only, preserve raw protected output, and follow Section 14.

## 11. Phase 5 — post-0005 integrity and count verification

Require exactly one head row equal to `0005_job_plane_role_split`. Repeat the
same server/database/system identity gates and byte-compare pre/post table
counts. The migration is authority-only: every application table count must be
unchanged.

Verify in `BEGIN READ ONLY`:

- exact public table set; no extra sequence/view/partition/foreign object;
- zero unvalidated constraints and invalid/not-ready/not-live indexes;
- exact prior constraints/indexes plus validated
  `ck_jobs_schedule_namespace`;
- exact unchanged append-only function/trigger plus exact protected API
  cancellation function/trigger, including bodies, owner, language, invoker
  mode, type bits, no predicate/args/column filter, and no other non-internal
  trigger on any of the six job tables;
- zero job/attempt/event/artifact/heartbeat orphan or cross-attempt row;
- `relrowsecurity=true` and `relforcerowsecurity=false` on exactly all six job
  tables;
- exact set of 23 named policies, their commands, roles, USING expressions,
  and WITH CHECK expressions;
- no job row violates the reserved scheduler namespace constraint;
- scheduler heartbeat rows with a job bind `actor_id` to that exact scheduler
  job actor. Jobless `SKIPPED`/`FAILED` heartbeat evidence is accepted only
  when the separately attested scheduler configuration binds `scheduler_id`
  and `actor_id` as distinct fixed identities;
- pre/post job-plane counts and canonical counts are byte-identical.

Any mismatch means NO-GO even if Alembic reports success.

## 12. Phase 6 — exact ACL/default-ACL matrix

Catalog verification must expand both relation and column ACLs; table-only
`has_table_privilege` is insufficient. Zero grant option is allowed.

Required positive authority:

| Role | Required authority |
|---|---|
| `trading_job_api` | public job projection; presentation attempt/event/artifact reads; initial SNAPSHOT/operator inserts; append-only operator events; cancellation-only job columns |
| `trading_job_worker` | SNAPSHOT job SELECT and worker mutation columns; attempt SELECT/INSERT/UPDATE; event SELECT/INSERT; artifact INSERT; worker-heartbeat SELECT/INSERT/UPDATE |
| `trading_job_scheduler` | non-lease scheduled-job projection/INSERT; scheduled event SELECT/INSERT; scheduler-heartbeat INSERT |

Required negative authority:

- database `CONNECT` ACL is exactly owner, migrator, reader, API, worker, and
  scheduler; database `TEMPORARY` ACL is exactly owner and migrator. PUBLIC and
  `trading_jobs` have neither, and no listed ACL entry has grant option;
- public-schema `USAGE` is exactly owner, migrator, reader, API, worker, and
  scheduler, while only the owner has `CREATE`; PUBLIC has neither and no
  listed ACL entry has grant option;
- `trading_jobs`, `trading_migrator`, and `trading_reader`: no table or column
  privilege on any of the six job tables; `trading_jobs` also has no CONNECT or
  schema USAGE;
- API: no lease/result-metadata/error/finished columns, no worker heartbeat, no
  scheduler heartbeat, no attempts/artifact write, no DELETE/TRUNCATE/TRIGGER;
- worker: no job INSERT, no immutable job identity update, no scheduler
  heartbeat, no DELETE/TRUNCATE/TRIGGER;
- scheduler: no job UPDATE, no lease-column SELECT, no attempt/artifact/worker
  table authority, no DELETE/TRUNCATE/TRIGGER;
- all runtime roles: no schema CREATE, object ownership, membership, grant
  option, default ACL inheritance, function EXECUTE, DDL, BYPASSRLS, or shared
  role path;
- PUBLIC has no table, column, sequence, or function authority in the public
  schema; all independently granted PUBLIC column ACLs are absent;
- `trading_owner` remains owner/migration authority; no runtime unit uses it.

Expand `pg_class.relacl`, `pg_attribute.attacl`, `pg_default_acl`, database and
schema ACLs, role attributes, memberships, policy roles, and object owners into
sorted evidence. There must be no default-ACL grantee among the shared,
legacy-reader/migrator, or three runtime roles. Later objects receive authority
only through a reviewed forward migration.

## 13. Phase 7 — cross-role denial and RLS verification

First run the repository's disposable PostgreSQL permission suite against
`0005`; it must cover allowed operations, cross-role denials, schedule actor/key
mismatch, append-only events, shared role login denial, and rollback. Disposable
evidence never substitutes for the approved runtime catalog checks.

On the approved runtime database, use each role's distinct protected passfile
and run only:

1. authenticated `current_user`, timezone, head, and allowed read-only catalog
   probes;
2. `EXPLAIN` without `ANALYZE` for allowed/denied DML shapes where planning is
   sufficient to prove column/table privilege;
3. the exact approval-bound, unique-ID permission matrix inside explicit
   transactions that always end in `ROLLBACK`, only where RLS WITH CHECK must be
   exercised.

The approval must explicitly include
`ALLOW_ROLLED_BACK_PERMISSION_PROBES=YES`. Never use a production-looking
idempotency key. The matrix may use only a reserved
`permission-check:<run-id>` operator key and one
`schedule:snapshot:<reviewed-future-slot>` key. It must not commit, execute a
job, notify a worker, or leave a row/event/artifact/heartbeat behind.

Mandatory behavioral assertions:

- API valid SNAPSHOT/operator insert is visible within its transaction; API
  `schedule:` insert, SCHEDULER actor, lease read/update, worker/scheduler
  heartbeat access, DELETE, and TRUNCATE are denied;
- scheduler exact SNAPSHOT/SCHEDULER/priority-zero slot insert is accepted
  within its transaction; operator actor, malformed/reserved namespace,
  non-SNAPSHOT type, nonzero priority, cancel/update, lease read, and worker
  table access are denied;
- worker can plan/perform a SNAPSHOT claim/fence path only against its rolled
  back fixture; job enqueue, scheduler heartbeat, immutable identity update,
  DELETE, and TRUNCATE are denied;
- shared-role authentication fails; no role can `SET ROLE` to another target;
- event UPDATE/DELETE remains rejected by the append-only trigger even for the
  owner in a rolled-back test;
- after every rollback, exact pre-probe counts, max event sequence, and artifact
  set are unchanged; queue is unchanged and no notification was emitted.

If the repository lacks a reviewed runtime-safe permission harness, do not
improvise SQL on the original database. Stop with runtime permission behavior
NOT VERIFIED. Catalog-only evidence does not upgrade that maturity claim.

## 14. Phase 8 — durable post-0005 backup

After every verification passes and before handoff, create a second unique
PostgreSQL 16 custom dump using the same collision, TOC, side-effect, owner,
mode, hash, and sync gates as Section 8. Its name includes
`0005-after-role-split`. Preserve both dumps and both catalogs; never overwrite
the reviewed pre-0005 dump.

The post dump must restore successfully into a separately approved disposable
PostgreSQL 16 cluster and reproduce:

- head `0005_job_plane_role_split`;
- exact data counts and integrity;
- exact constraint/index/trigger/function definitions;
- exact table/column/default ACLs and RLS policy set;
- role prerequisites supplied separately without copying production
  credentials.

An isolated restore drill is a separate operation and approval. Absence of that
drill remains a residual DR risk; it does not authorize starting services.

## 15. Non-destructive rollback and failure handling

There is no Alembic downgrade and no restoration of `trading_jobs LOGIN`.

1. **Before provisioning:** retain the pre-backup/evidence; database remains at
   0004; all job units remain inactive.
2. **Provisioning fails:** do not retry. Preserve protected raw output. Keep
   `trading_jobs` and any created target role inactive/unconsumed. Do not copy a
   credential or grant a compatibility membership. Review a forward admin
   repair.
3. **0005 fails:** rely only on Alembic/PostgreSQL transaction rollback. Prove
   head is still exactly 0004 and counts unchanged. Do not rerun or downgrade.
   New roles remain with no 0005 object authority; keep all job units inactive.
4. **0005 succeeds but verification fails:** keep head/rows/events/artifacts,
   take a protected evidence dump if the approved gate still permits it, and
   stop. Correct only through a new reviewed forward migration (for example
   0006). Never delete evidence or restore the shared role.
5. **Credential exposure:** stop, quarantine output, and invoke a separately
   approved credential-rotation incident procedure. Do not print or reuse the
   credential while diagnosing.
6. **Backup/restore evidence fails:** retain partials under protected storage,
   do not reuse names, do not start services, and do not restore over the
   original cluster.

The pre-0005 dump is preservation/forensic evidence and an isolated-restore
source, not permission for an in-place schema rewind. Runtime rollback after a
later service rollout disables only newly approved API/worker units, preserves
all rows/events/artifacts, and leaves scheduler disabled; it is outside this
database-only runbook.

## 16. Exit evidence and mandatory stop before services

The secret-free evidence bundle must contain:

- exact approval record and hashes; source commit/tree; Release Authority v2,
  runbook, migration, and provision-script digests;
- authenticated cluster/database/system/head identity before and after;
- inactive job-unit/timer evidence before and after;
- pre/post counts, integrity, orphan, constraint, index, function, and trigger
  evidence;
- pre/post roles, memberships, sessions, object owners, database/schema/table/
  column/function/default ACLs, RLS flags, and exact policy definitions;
- disposable and approved-runtime permission-matrix results, with every runtime
  probe proven rolled back and counts unchanged;
- pre/post dump catalogs, stat, SHA-256, sync evidence, and approved storage
  identity;
- only exit codes and secret-scan status from role provisioning/Alembic; no raw
  password-bearing output, DSN, URI, environment value, or password hash;
- residual risks and a final reviewed decision.

Final mechanical assertions:

- head is exactly `0005_job_plane_role_split`;
- `trading_jobs` is NOLOGIN and has zero session/privilege path;
- all three new roles and only those roles match the exact matrix;
- all data counts are unchanged and append-only events remain intact;
- both backups are durable and independently reviewable;
- Job API, worker, scheduler service, and scheduler timer remain inactive; the
  timer remains disabled;
- no job, child, provider, broker, exchange, or dashboard command ran.

The only successful handoff text for this database sub-gate is:

`JOB-PLANE 0005 EVIDENCE READY FOR INDEPENDENT REVIEW — SERVICES NOT AUTHORIZED`

Any other outcome is:

`NO-GO — JOB-PLANE ROLE SPLIT INCOMPLETE`

Neither outcome starts the next phase.
