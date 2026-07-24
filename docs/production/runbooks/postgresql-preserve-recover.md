# PostgreSQL 16 Preservation and Recovery Sub-Gate Runbook

**Status:** REVIEWED PROCEDURE — NOT EXECUTION APPROVAL. This file is an
operator procedure, not an executable script, not an approval record, and not
evidence that any recovery step ran. Every command remains conditional on the
exact dual-reviewed approval transcript in Section 4.

**Decision scope:** this is only the DATA-001 preservation/recovery sub-gate of
roadmap A1. It cannot return A1 GO. A1 remains NO-GO until a separately reviewed
backup/PITR design, missed-backup alert, retention/off-host policy, current
restore evidence, and any ACL correction are implemented and verified. Nothing
here authorizes an application service/timer, deployment, research job,
provider call, mode change, Release Authority v2, paper promotion, or live
trading. Both live approvals remain false.

No PostgreSQL, service, runtime, provider, network, or runbook command was run
while producing or reviewing this document.

## 1. Known incident state

The documented original cluster identity is:

| Field | Required value |
|---|---|
| PostgreSQL major | 16 (historically 16.14) |
| Cluster | trading-agent |
| Host / port | 127.0.0.1:55432 |
| Database | trading_agent |
| Data directory | /home/thenam176/.local/share/trading-agent/postgres/16/trading-agent |
| Socket directory | /home/thenam176/.local/run/trading-agent |
| Log | /home/thenam176/.local/state/trading-agent/postgres/trading-agent.log |
| Expected recovered schema head | 0004_durable_research_jobs |

The 2026-07-16 audit found no listener on 55432, postmaster.pid naming a dead
PID, pg_controldata state in production, checksums disabled, and a log ending
after a smart-shutdown request without a clean-shutdown record. A start will
write crash-recovery state. Current contents, head, constraints, indexes, ACLs,
and relation health are unknown.

The latest verified fallback dump is:

| Field | Exact verified value |
|---|---|
| Path | /home/thenam176/.local/share/trading-agent-backups/phase4-preapply-20260712T131219Z.dump |
| Mode | 0600 |
| Size | 31,823,453 bytes |
| SHA-256 | 1f4b64bd74811ec9977befd1590e59872c3a32b9988686c08070756cc97798f8 |
| Head | 0003_contract_lineage_repair |
| Restore evidence | 20 application tables; 43,055 canonical rows; 222 quarantine rows |

PostgreSQL ran about 51 hours after that dump. No newer base/full backup, WAL
archive, or PITR proof was found. An isolated restore from it accepts an
approximately 51-hour RPO gap and requires the exact 0003-to-0004 migration. It
must never overwrite the original cluster.

## 2. Absolute prohibitions and stop conditions

Stop, retain evidence, and keep every application unit inactive if any gate
fails.

- Never manually delete, rename, truncate, or edit postmaster.pid.
- Never use pg_resetwal; run initdb or reinitialize original PGDATA; replace
  control/WAL files; restore over the original; copy preservation back in
  place; downgrade; edit alembic_version; drop/truncate tables; or delete audit,
  quarantine, lineage, job, attempt, event, or artifact evidence.
- One original-cluster start per exact approval. Failure, timeout, refusal, or
  crash is not retry permission.
- Never improvise repair, VACUUM, REINDEX, GRANT/REVOKE, tablespace, config,
  ownership, role, or WAL commands.
- Never print a password, protected credential file, PGPASSWORD, URI, or DSN.
  Never use shell tracing, source/eval a credential/approval file, or put a
  password/DSN on a command line.
- Stop on any PostgreSQL major, system identifier, path, config, port, socket,
  owner, PID-to-PGDATA, listener, database, or role mismatch.
- Stop on any source/destination symlink, non-canonical path, ancestor or
  descendant overlap, existing destination leaf, same-device preservation,
  insufficient space, external PGDATA symlink, or copy/hash/metadata mismatch.
- Stop on PANIC, invalid WAL/checkpoint/control file/page, relation loss,
  timeline surprise, recovery restart, multiple/unknown Alembic heads, count
  drift, orphan, unvalidated constraint, unexpected constraint/index
  definition, invalid index, trigger/function mismatch, role/membership/owner
  mismatch, or any ACL/default-ACL difference outside the exact preapproved
  inherited leakage classified in Section 5.12. That known leakage still
  mandates final NO-GO and never authorizes repair or promotion.
- Stop on an unreadable dump/catalog, an existing final or .partial name, a
  failed single-transaction restore, or any isolated/source mismatch.

## 3. Application-stop invariant

The approval gate authorizes stopping, but never starting, these consumers:

- user services: trading-agent.service, trading-dashboard.service,
  trading-control-api.service, trading-job-api.service,
  trading-job-worker.service, trading-job-scheduler.service, and
  trading-safety-state-export.service;
- user timers: trading-job-scheduler.timer and
  trading-safety-state-export.timer;
- system units: trading-semantic-input-refresh.service and
  trading-semantic-input-refresh.timer.

Every unit must be mechanically asserted inactive after stop, immediately
before original PostgreSQL start, after start, before final stop, and at
handoff.

## 4. Exact dual-reviewed execution transcript

This procedure is ready for review, but it grants no execution authority. Before
any service stop, directory creation, PostgreSQL start, or other write, the
operator and a distinct reviewer must approve one access-controlled transcript
with exactly the tab-separated fields below. The change-control system, not
this shell parser, establishes who entered and reviewed the record. The parser
checks the declared identities, exact content, time window, hashes, and target
values; it does not claim cryptographic authentication.

Use a literal TAB between each key and value. Do not add a header, comments,
blank lines, duplicate keys, extra keys, placeholders, shell quoting, or secret
values. The transcript itself contains no password or DSN.

~~~text
DECISION	APPROVED_POSTGRESQL16_RECOVERY_SUBGATE
CHANGE_ID	<approved change identifier>
INCIDENT_ID	<approved incident identifier>
CHANGE_ARTIFACT	<canonical protected export from the authenticated change-control system>
CHANGE_ARTIFACT_SHA256	<64 lowercase hex of that exact export>
APPROVED_AT_UTC	<YYYY-MM-DDTHH:MM:SSZ>
EXPIRES_AT_UTC	<YYYY-MM-DDTHH:MM:SSZ, no more than four hours later>
OPERATOR_NAME	<named operator>
REVIEWER_NAME	<different named reviewer>
OPERATOR_ATTESTATION	I_APPROVE_THIS_EXACT_RECOVERY_TRANSCRIPT
REVIEWER_ATTESTATION	I_INDEPENDENTLY_REVIEWED_THIS_EXACT_RECOVERY_TRANSCRIPT
RUN_ID	<letters, digits, underscore, or hyphen; at most 48 characters>
RUNBOOK_SHA256	<64 lowercase hex>
SOURCE_COMMIT	<40 lowercase hex>
SOURCE_TREE	<40 lowercase hex>
MIGRATION_SHA256	<64 lowercase hex>
EXPECTED_CATALOG_SHA256	<64 lowercase hex of the reviewed clean-PG16 0001-0004 complete V2 catalog snapshot>
EXPECTED_CATALOG_QUERY_ID	PG16_COMPLETE_RELATION_CATALOG_V2
EXPECTED_CATALOG_PROVENANCE	PRECOMPUTED_CLEAN_DISPOSABLE_PG16_EXACT_0001_0004_CATALOG_V2
EXPECTED_CATALOG_REVIEW_ATTESTATION	INDEPENDENTLY_REVIEWED_NOT_FROM_INCIDENT_OR_THIS_RUN_RESTORE
ORIG_SYSTEM_ID	<exact decimal Database system identifier from reviewed evidence>
ORIG_PGDATA_NLINK	<exact positive link count from reviewed offline stat evidence>
ORIG_SOCKET_NLINK	<exact positive link count from reviewed offline stat evidence>
ORIG_LOG_DIR_NLINK	<exact positive link count from reviewed offline stat evidence>
ORIG_LOG_NLINK	1
EVIDENCE_PARENT	<existing canonical private 0700 directory>
PRESERVE_PARENT	<existing canonical private 0700 directory on independent storage>
BACKUP_PARENT	<existing canonical private 0700 directory on independent storage>
SECRET_PARENT	<existing canonical private 0700 runtime directory>
ISO_HOST	<canonical isolated Unix-socket directory>
ISO_PORT	<isolated integer port other than 55432>
ISO_ADMIN_DB	postgres
ISO_RESTORE_DB	<unique trading_agent_restore_* database>
ISO_PGDATA	<canonical pre-provisioned PostgreSQL 16 PGDATA>
ISO_SYSTEM_ID	<exact decimal isolated Database system identifier from reviewed evidence>
ISO_SOCKET	<same value as ISO_HOST>
ISO_ADMIN_ENV	<canonical protected isolated-admin environment file>
ALLOW_STOP_ALL_LISTED_UNITS	YES
ALLOW_OFFLINE_COLD_COPY	YES
ALLOW_ONE_ORIGINAL_START	YES
ALLOW_ONE_ORIGINAL_STOP	YES
ALLOW_READ_ONLY_VERIFICATION	YES
ALLOW_IMMEDIATE_LOGICAL_BACKUP	YES
ALLOW_MIGRATE_ORIGINAL_IF_0003	YES
ALLOW_ISOLATED_RESTORE	YES
ACCEPT_INTERRUPTED_SHUTDOWN	YES
ACCEPT_CHECKSUMS_DISABLED	YES
ACKNOWLEDGE_NO_PITR	YES
RECOVERY_LOG_POLICY_ID	PG16_INTERRUPTED_RECOVERY_V1
ORIGINAL_START_POLICY_ID	PG16_FAIL_CLOSED_MAINTENANCE_START_V1
~~~

The reviewer must compare the transcript and the hash-bound authenticated
change-control export to this runbook, independently check all source hashes,
verify EXPECTED_CATALOG_SHA256 against a deterministic snapshot from a clean
PostgreSQL 16 build of migrations 0001 through 0004 at SOURCE_TREE (never from
the incident source or this run's restore), and confirm the authenticated
change-control export contains that pre-run provenance, query identifier, hash,
and independent review evidence. If any provenance evidence is absent, do not
approve and stop before writes;
confirm destination independence/capacity, confirm the
isolated target is disposable and not production, and explicitly approve the
conditional 0003-to-0004 original migration. If either reviewer declines that
write, do not approve or begin this procedure; use a separately reviewed
preservation-only procedure instead. The original-start policy additionally
binds the no-hook/no-preload maintenance settings, disabled replication and
background maintenance workers, fixed loopback/socket endpoint, and complete
stderr recovery-log capture used below.

Historical fallback is a different operation. It requires a separate
dual-reviewed transcript with decision
APPROVED_HISTORICAL_0003_ISOLATED_ONLY_WITH_51H_RPO and must not authorize or
name an original-cluster write. Section 5.16 is its boundary.

---

## 5. Conditional operator procedure

Run the blocks in one Bash session, in order, only during the approved window.
The blocks are deliberately not packaged as an executable. Record the terminal
transcript in the protected evidence location without shell tracing. No command
in this section was run while authoring or reviewing this file.

### 5.1 Parse the approval transcript and bind every target

The launcher supplies only APPROVAL_RECORD, the canonical path to the
access-controlled transcript. The parser never sources or evaluates it.

~~~bash
set -euo pipefail
set +x
umask 077
readonly -a POSTGRES_AMBIENT_ENV_NAMES=(
  PGAPPNAME PGCHANNELBINDING PGCLIENTENCODING PGCONNECT_TIMEOUT PGDATABASE
  PGDATA PGDATESTYLE PGGEQO PGGSSLIB PGGSSDELEGATION PGGSSENCMODE PGHOST
  PGHOSTADDR PGKRBSRVNAME PGLOADBALANCEHOSTS PGLOCALEDIR PGOPTIONS PGPASSFILE
  PGPASSWORD PGPORT PGREALM PGREQUIREPEER PGREQUIRESSL PGSERVICE PGSERVICEFILE
  PGSSLCERT PGSSLCOMPRESSION PGSSLCRL PGSSLCRLDIR PGSSLKEY PGSSLMODE
  PGSSLROOTCERT PGSSLSNI PGSYSCONFDIR PGTARGETSESSIONATTRS PGTZ PGUSER
  PGCTLTIMEOUT PG_COLOR PSQLRC PSQL_HISTORY PSQL_PAGER PSQL_WATCH_PAGER
)
unset "${POSTGRES_AMBIENT_ENV_NAMES[@]}"

assert_no_ambient_postgres_environment() {
  local exported_name=
  while IFS= read -r exported_name; do
    case "$exported_name" in
      PG*|PSQL*) return 1 ;;
    esac
  done < <(compgen -e | LC_ALL=C sort)
}
assert_no_ambient_postgres_environment

readonly APPROVAL_RECORD="${APPROVAL_RECORD:?set canonical approval-record path}"
test -f "$APPROVAL_RECORD"
test ! -L "$APPROVAL_RECORD"
test "$(realpath -e -- "$APPROVAL_RECORD")" = "$APPROVAL_RECORD"
test "$(stat -c '%U' "$APPROVAL_RECORD")" = thenam176
test "$(stat -c '%a' "$APPROVAL_RECORD")" = 600
test "$(stat -c '%h' "$APPROVAL_RECORD")" = 1

approval_fd_stat() {
  stat -Lc '%d|%i|%s|%f|%u|%g|%h|%Y|%Z' "/proc/$$/fd/$1"
}
exec {APPROVAL_PARSE_FD}<"$APPROVAL_RECORD"
exec {APPROVAL_HASH_FD}<"$APPROVAL_RECORD"
exec {APPROVAL_COPY_FD}<"$APPROVAL_RECORD"
approval_record_stat="$(approval_fd_stat "$APPROVAL_PARSE_FD")"
test "$(approval_fd_stat "$APPROVAL_HASH_FD")" = "$approval_record_stat"
test "$(approval_fd_stat "$APPROVAL_COPY_FD")" = "$approval_record_stat"
test "$(stat -Lc '%d|%i|%s|%f|%u|%g|%h|%Y|%Z' "$APPROVAL_RECORD")" \
  = "$approval_record_stat"
approval_record_sha256="$(sha256sum <&"$APPROVAL_HASH_FD" | awk '{print $1}')"
[[ "$approval_record_sha256" =~ ^[0-9a-f]{64}$ ]]

declare -A PLAN=()
while IFS= read -r line; do
  test -n "$line"
  [[ "$line" == *$'\t'* ]]
  key="${line%%$'\t'*}"
  value="${line#*$'\t'}"
  [[ "$value" != *$'\t'* ]]
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]]
  test -z "$(printf '%s' "$value" | LC_ALL=C tr -d '[:print:]')"
  test -z "${PLAN[$key]+present}"
  PLAN["$key"]="$value"
done <&"$APPROVAL_PARSE_FD"
unset line key value

test "$(approval_fd_stat "$APPROVAL_PARSE_FD")" = "$approval_record_stat"
test "$(approval_fd_stat "$APPROVAL_HASH_FD")" = "$approval_record_stat"
test "$(approval_fd_stat "$APPROVAL_COPY_FD")" = "$approval_record_stat"
test "$(stat -Lc '%d|%i|%s|%f|%u|%g|%h|%Y|%Z' "$APPROVAL_RECORD")" \
  = "$approval_record_stat"

required_keys=(
  DECISION CHANGE_ID INCIDENT_ID CHANGE_ARTIFACT CHANGE_ARTIFACT_SHA256
  APPROVED_AT_UTC EXPIRES_AT_UTC
  OPERATOR_NAME REVIEWER_NAME OPERATOR_ATTESTATION REVIEWER_ATTESTATION RUN_ID
  RUNBOOK_SHA256 SOURCE_COMMIT SOURCE_TREE MIGRATION_SHA256 EXPECTED_CATALOG_SHA256
  EXPECTED_CATALOG_QUERY_ID EXPECTED_CATALOG_PROVENANCE
  EXPECTED_CATALOG_REVIEW_ATTESTATION ORIG_SYSTEM_ID
  ORIG_PGDATA_NLINK ORIG_SOCKET_NLINK ORIG_LOG_DIR_NLINK ORIG_LOG_NLINK
  EVIDENCE_PARENT PRESERVE_PARENT BACKUP_PARENT SECRET_PARENT
  ISO_HOST ISO_PORT ISO_ADMIN_DB ISO_RESTORE_DB ISO_PGDATA ISO_SYSTEM_ID
  ISO_SOCKET ISO_ADMIN_ENV
  ALLOW_STOP_ALL_LISTED_UNITS ALLOW_OFFLINE_COLD_COPY ALLOW_ONE_ORIGINAL_START
  ALLOW_ONE_ORIGINAL_STOP ALLOW_READ_ONLY_VERIFICATION
  ALLOW_IMMEDIATE_LOGICAL_BACKUP ALLOW_MIGRATE_ORIGINAL_IF_0003
  ALLOW_ISOLATED_RESTORE ACCEPT_INTERRUPTED_SHUTDOWN
  ACCEPT_CHECKSUMS_DISABLED ACKNOWLEDGE_NO_PITR RECOVERY_LOG_POLICY_ID
  ORIGINAL_START_POLICY_ID
)
test "${#PLAN[@]}" -eq "${#required_keys[@]}"
for key in "${required_keys[@]}"; do
  test -n "${PLAN[$key]+present}"
  test -n "${PLAN[$key]}"
done
unset key required_keys

test "${PLAN[DECISION]}" = APPROVED_POSTGRESQL16_RECOVERY_SUBGATE
test "${PLAN[OPERATOR_ATTESTATION]}" = I_APPROVE_THIS_EXACT_RECOVERY_TRANSCRIPT
test "${PLAN[REVIEWER_ATTESTATION]}" = I_INDEPENDENTLY_REVIEWED_THIS_EXACT_RECOVERY_TRANSCRIPT
test "${PLAN[OPERATOR_NAME]}" != "${PLAN[REVIEWER_NAME]}"
[[ "${PLAN[OPERATOR_NAME]}" =~ ^[A-Za-z0-9][A-Za-z0-9_.@-]{1,127}$ ]]
[[ "${PLAN[REVIEWER_NAME]}" =~ ^[A-Za-z0-9][A-Za-z0-9_.@-]{1,127}$ ]]
[[ "${PLAN[CHANGE_ID]}" =~ ^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$ ]]
[[ "${PLAN[INCIDENT_ID]}" =~ ^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$ ]]
[[ "${PLAN[RUN_ID]}" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$ ]]
[[ "${PLAN[RUNBOOK_SHA256]}" =~ ^[0-9a-f]{64}$ ]]
[[ "${PLAN[MIGRATION_SHA256]}" =~ ^[0-9a-f]{64}$ ]]
[[ "${PLAN[EXPECTED_CATALOG_SHA256]}" =~ ^[0-9a-f]{64}$ ]]
test "${PLAN[EXPECTED_CATALOG_QUERY_ID]}" = PG16_COMPLETE_RELATION_CATALOG_V2
test "${PLAN[EXPECTED_CATALOG_PROVENANCE]}" \
  = PRECOMPUTED_CLEAN_DISPOSABLE_PG16_EXACT_0001_0004_CATALOG_V2
test "${PLAN[EXPECTED_CATALOG_REVIEW_ATTESTATION]}" \
  = INDEPENDENTLY_REVIEWED_NOT_FROM_INCIDENT_OR_THIS_RUN_RESTORE
[[ "${PLAN[CHANGE_ARTIFACT_SHA256]}" =~ ^[0-9a-f]{64}$ ]]
[[ "${PLAN[SOURCE_COMMIT]}" =~ ^[0-9a-f]{40}$ ]]
[[ "${PLAN[SOURCE_TREE]}" =~ ^[0-9a-f]{40}$ ]]
[[ "${PLAN[ORIG_SYSTEM_ID]}" =~ ^[0-9]{10,24}$ ]]
for key in ORIG_PGDATA_NLINK ORIG_SOCKET_NLINK ORIG_LOG_DIR_NLINK ORIG_LOG_NLINK; do
  [[ "${PLAN[$key]}" =~ ^[1-9][0-9]*$ ]]
done
unset key
test "${PLAN[ORIG_PGDATA_NLINK]}" -ge 2
test "${PLAN[ORIG_SOCKET_NLINK]}" -ge 2
test "${PLAN[ORIG_LOG_DIR_NLINK]}" -ge 2
test "${PLAN[ORIG_LOG_NLINK]}" -eq 1
[[ "${PLAN[ISO_SYSTEM_ID]}" =~ ^[0-9]{10,24}$ ]]
test "${PLAN[ISO_SYSTEM_ID]}" != "${PLAN[ORIG_SYSTEM_ID]}"
[[ "${PLAN[ISO_PORT]}" =~ ^[1-9][0-9]{0,4}$ ]]
test "${PLAN[ISO_PORT]}" -le 65535
[[ "${PLAN[ISO_RESTORE_DB]}" =~ ^trading_agent_restore_[A-Za-z0-9_]{1,48}$ ]]
test "${PLAN[ISO_ADMIN_DB]}" = postgres
test "${PLAN[ISO_HOST]}" = "${PLAN[ISO_SOCKET]}"
test "${PLAN[ISO_PORT]}" != 55432
test "${PLAN[RECOVERY_LOG_POLICY_ID]}" = PG16_INTERRUPTED_RECOVERY_V1
test "${PLAN[ORIGINAL_START_POLICY_ID]}" \
  = PG16_FAIL_CLOSED_MAINTENANCE_START_V1
for key in \
  ALLOW_STOP_ALL_LISTED_UNITS ALLOW_OFFLINE_COLD_COPY ALLOW_ONE_ORIGINAL_START \
  ALLOW_ONE_ORIGINAL_STOP ALLOW_READ_ONLY_VERIFICATION \
  ALLOW_IMMEDIATE_LOGICAL_BACKUP ALLOW_MIGRATE_ORIGINAL_IF_0003 \
  ALLOW_ISOLATED_RESTORE \
  ACCEPT_INTERRUPTED_SHUTDOWN ACCEPT_CHECKSUMS_DISABLED ACKNOWLEDGE_NO_PITR
do
  test "${PLAN[$key]}" = YES
done
unset key

[[ "${PLAN[APPROVED_AT_UTC]}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]
[[ "${PLAN[EXPIRES_AT_UTC]}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]
approved_epoch="$(date -u -d "${PLAN[APPROVED_AT_UTC]}" +%s)"
expires_epoch="$(date -u -d "${PLAN[EXPIRES_AT_UTC]}" +%s)"
now_epoch="$(date -u +%s)"
test "$approved_epoch" -le "$now_epoch"
test "$now_epoch" -le "$expires_epoch"
test "$expires_epoch" -gt "$approved_epoch"
test $((expires_epoch - approved_epoch)) -le 14400
readonly APPROVAL_EXPIRES_EPOCH="$expires_epoch"
unset approved_epoch expires_epoch now_epoch

assert_approval_current() {
  test "$(date -u +%s)" -le "$APPROVAL_EXPIRES_EPOCH"
}

readonly PG_BIN=/usr/lib/postgresql/16/bin
readonly RUN_ID="${PLAN[RUN_ID]}"
readonly REPO=/home/thenam176/projects/trading-agent
readonly RUNBOOK="$REPO/docs/production/runbooks/postgresql-preserve-recover.md"
readonly ORIG_HOST=127.0.0.1
readonly ORIG_PORT=55432
readonly ORIG_DB=trading_agent
readonly ORIG_ADMIN_DB=postgres
readonly ORIG_PGDATA=/home/thenam176/.local/share/trading-agent/postgres/16/trading-agent
readonly ORIG_SOCKET=/home/thenam176/.local/run/trading-agent
readonly ORIG_LOG_DIR=/home/thenam176/.local/state/trading-agent/postgres
readonly ORIG_LOG=/home/thenam176/.local/state/trading-agent/postgres/trading-agent.log
readonly READER_ENV=/home/thenam176/.config/trading-agent/postgres-reader.env
readonly OWNER_ENV=/home/thenam176/.config/trading-agent/postgres-owner.env
readonly ADMIN_ENV=/home/thenam176/.config/trading-agent/postgres-admin.env
readonly ISO_HOST="${PLAN[ISO_HOST]}"
readonly ISO_PORT="${PLAN[ISO_PORT]}"
readonly ISO_ADMIN_DB="${PLAN[ISO_ADMIN_DB]}"
readonly ISO_RESTORE_DB="${PLAN[ISO_RESTORE_DB]}"
readonly ISO_PGDATA="${PLAN[ISO_PGDATA]}"
readonly ISO_SOCKET="${PLAN[ISO_SOCKET]}"
readonly ISO_ADMIN_ENV="${PLAN[ISO_ADMIN_ENV]}"

readonly -a ORIGINAL_SAFE_POSTGRES_OPTIONS=(
  -c archive_mode=off
  -c archive_command=
  -c archive_library=
  -c restore_command=
  -c archive_cleanup_command=
  -c recovery_end_command=
  -c primary_conninfo=
  -c shared_preload_libraries=
  -c session_preload_libraries=
  -c local_preload_libraries=
  -c ssl_passphrase_command=
  -c external_pid_file=
  -c max_logical_replication_workers=0
  -c max_wal_senders=0
  -c autovacuum=off
  -c logging_collector=off
  -c log_destination=stderr
  -c log_min_messages=log
  -c jit=off
  -c 'search_path=pg_catalog,public'
  -c ssl=off
  -c listen_addresses=127.0.0.1
  -c "unix_socket_directories=$ORIG_SOCKET"
  -c "port=$ORIG_PORT"
)
ORIGINAL_SAFE_START_OPTIONS="${ORIGINAL_SAFE_POSTGRES_OPTIONS[*]}"
readonly ORIGINAL_SAFE_START_OPTIONS

test -f "${PLAN[CHANGE_ARTIFACT]}"
test ! -L "${PLAN[CHANGE_ARTIFACT]}"
test "${PLAN[CHANGE_ARTIFACT]}" != "$APPROVAL_RECORD"
test "$(realpath -e -- "${PLAN[CHANGE_ARTIFACT]}")" = "${PLAN[CHANGE_ARTIFACT]}"
test "$(stat -c '%U' "${PLAN[CHANGE_ARTIFACT]}")" = thenam176
test "$(stat -c '%a' "${PLAN[CHANGE_ARTIFACT]}")" = 600
test "$(stat -c '%h' "${PLAN[CHANGE_ARTIFACT]}")" = 1
exec {CHANGE_ARTIFACT_HASH_FD}<"${PLAN[CHANGE_ARTIFACT]}"
exec {CHANGE_ARTIFACT_COPY_FD}<"${PLAN[CHANGE_ARTIFACT]}"
change_artifact_stat="$(approval_fd_stat "$CHANGE_ARTIFACT_HASH_FD")"
test "$(approval_fd_stat "$CHANGE_ARTIFACT_COPY_FD")" = "$change_artifact_stat"
test "$(stat -Lc '%d|%i|%s|%f|%u|%g|%h|%Y|%Z' \
  "${PLAN[CHANGE_ARTIFACT]}")" = "$change_artifact_stat"
change_artifact_sha256="$(sha256sum <&"$CHANGE_ARTIFACT_HASH_FD" | awk '{print $1}')"
test "$change_artifact_sha256" = "${PLAN[CHANGE_ARTIFACT_SHA256]}"

test "$(sha256sum "$RUNBOOK" | awk '{print $1}')" = "${PLAN[RUNBOOK_SHA256]}"
test "$(git -C "$REPO" rev-parse HEAD)" = "${PLAN[SOURCE_COMMIT]}"
test "$(git -C "$REPO" rev-parse 'HEAD^{tree}')" = "${PLAN[SOURCE_TREE]}"
test -z "$(git -C "$REPO" status --porcelain=v1 --untracked-files=all)"
test "$(sha256sum "$REPO/alembic/versions/0004_durable_research_jobs.py" |
  awk '{print $1}')" = "${PLAN[MIGRATION_SHA256]}"
~~~

### 5.2 Zero-write canonical path, overlap, storage, and destination gates

This phase performs no mkdir, redirection, service action, or target write.

~~~bash
canonical_existing() {
  local value="$1"
  test -e "$value"
  test ! -L "$value"
  test "$(realpath -e -- "$value")" = "$value"
  printf '%s' "$value"
}
canonical_new_leaf() {
  local parent="$1"
  local leaf="$2"
  canonical_existing "$parent" >/dev/null
  [[ "$leaf" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]
  local value="$parent/$leaf"
  test "$(realpath -m -- "$value")" = "$value"
  test ! -e "$value"
  test ! -L "$value"
  printf '%s' "$value"
}
paths_overlap() {
  local left="$1"
  local right="$2"
  case "$left/" in "$right/"*) return 0 ;; esac
  case "$right/" in "$left/"*) return 0 ;; esac
  return 1
}
assert_private_parent() {
  local path="$1"
  canonical_existing "$path" >/dev/null
  test -d "$path"
  test "$(stat -c '%U' "$path")" = thenam176
  test "$(stat -c '%a' "$path")" = 700
}

canonical_existing "$ORIG_PGDATA" >/dev/null
canonical_existing "$ORIG_SOCKET" >/dev/null
canonical_existing "$ORIG_LOG_DIR" >/dev/null
canonical_existing "$ORIG_LOG" >/dev/null
canonical_existing "$REPO" >/dev/null
canonical_existing "$ISO_PGDATA" >/dev/null
canonical_existing "$ISO_SOCKET" >/dev/null
canonical_existing "$READER_ENV" >/dev/null
canonical_existing "$OWNER_ENV" >/dev/null
canonical_existing "$ADMIN_ENV" >/dev/null
canonical_existing "$ISO_ADMIN_ENV" >/dev/null

test "$ORIG_PGDATA" = /home/thenam176/.local/share/trading-agent/postgres/16/trading-agent
test "$ORIG_SOCKET" = /home/thenam176/.local/run/trading-agent
test "$ORIG_LOG_DIR" = /home/thenam176/.local/state/trading-agent/postgres
test "$ORIG_LOG" = "$ORIG_LOG_DIR/trading-agent.log"
test "$(dirname -- "$ORIG_LOG")" = "$ORIG_LOG_DIR"

ORIG_PGDATA_DEVICE_INODE="$(stat -Lc '%d:%i' "$ORIG_PGDATA")"
ORIG_SOCKET_DEVICE_INODE="$(stat -Lc '%d:%i' "$ORIG_SOCKET")"
ORIG_LOG_DIR_DEVICE_INODE="$(stat -Lc '%d:%i' "$ORIG_LOG_DIR")"
ORIG_LOG_DEVICE_INODE="$(stat -Lc '%d:%i' "$ORIG_LOG")"
readonly ORIG_PGDATA_DEVICE_INODE ORIG_SOCKET_DEVICE_INODE
readonly ORIG_LOG_DIR_DEVICE_INODE ORIG_LOG_DEVICE_INODE

assert_original_path_properties() {
  local phase="$1"
  [[ "$phase" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]

  canonical_existing "$ORIG_PGDATA" >/dev/null
  test -d "$ORIG_PGDATA"
  test "$(stat -c '%U' "$ORIG_PGDATA")" = thenam176
  test "$(stat -c '%a' "$ORIG_PGDATA")" = 700
  test "$(stat -c '%h' "$ORIG_PGDATA")" = "${PLAN[ORIG_PGDATA_NLINK]}"
  test "$(stat -Lc '%d:%i' "$ORIG_PGDATA")" = "$ORIG_PGDATA_DEVICE_INODE"

  canonical_existing "$ORIG_SOCKET" >/dev/null
  test -d "$ORIG_SOCKET"
  test "$(stat -c '%U' "$ORIG_SOCKET")" = thenam176
  test "$(stat -c '%a' "$ORIG_SOCKET")" = 700
  test "$(stat -c '%h' "$ORIG_SOCKET")" = "${PLAN[ORIG_SOCKET_NLINK]}"
  test "$(stat -Lc '%d:%i' "$ORIG_SOCKET")" = "$ORIG_SOCKET_DEVICE_INODE"

  canonical_existing "$ORIG_LOG_DIR" >/dev/null
  test -d "$ORIG_LOG_DIR"
  test "$(stat -c '%U' "$ORIG_LOG_DIR")" = thenam176
  test "$(stat -c '%a' "$ORIG_LOG_DIR")" = 700
  test "$(stat -c '%h' "$ORIG_LOG_DIR")" = "${PLAN[ORIG_LOG_DIR_NLINK]}"
  test "$(stat -Lc '%d:%i' "$ORIG_LOG_DIR")" = "$ORIG_LOG_DIR_DEVICE_INODE"

  canonical_existing "$ORIG_LOG" >/dev/null
  test -f "$ORIG_LOG"
  test "$(stat -c '%U' "$ORIG_LOG")" = thenam176
  test "$(stat -c '%a' "$ORIG_LOG")" = 600
  test "$(stat -c '%h' "$ORIG_LOG")" = "${PLAN[ORIG_LOG_NLINK]}"
  test "$(stat -Lc '%d:%i' "$ORIG_LOG")" = "$ORIG_LOG_DEVICE_INODE"

  printf '%s|pgdata|owner=%s|mode=%s|links=%s|device_inode=%s\n' \
    "$phase" "$(stat -c '%U' "$ORIG_PGDATA")" \
    "$(stat -c '%a' "$ORIG_PGDATA")" "$(stat -c '%h' "$ORIG_PGDATA")" \
    "$ORIG_PGDATA_DEVICE_INODE"
  printf '%s|socket_dir|owner=%s|mode=%s|links=%s|device_inode=%s\n' \
    "$phase" "$(stat -c '%U' "$ORIG_SOCKET")" \
    "$(stat -c '%a' "$ORIG_SOCKET")" "$(stat -c '%h' "$ORIG_SOCKET")" \
    "$ORIG_SOCKET_DEVICE_INODE"
  printf '%s|log_dir|owner=%s|mode=%s|links=%s|device_inode=%s\n' \
    "$phase" "$(stat -c '%U' "$ORIG_LOG_DIR")" \
    "$(stat -c '%a' "$ORIG_LOG_DIR")" "$(stat -c '%h' "$ORIG_LOG_DIR")" \
    "$ORIG_LOG_DIR_DEVICE_INODE"
  printf '%s|log_file|owner=%s|mode=%s|links=%s|device_inode=%s\n' \
    "$phase" "$(stat -c '%U' "$ORIG_LOG")" \
    "$(stat -c '%a' "$ORIG_LOG")" "$(stat -c '%h' "$ORIG_LOG")" \
    "$ORIG_LOG_DEVICE_INODE"
}

assert_no_external_recovery_configuration() {
  local phase="$1"
  local archive_mode=
  local archive_command=
  local archive_library=
  local archive_cleanup_command=
  local recovery_end_command=
  local primary_conninfo=
  local restore_command=
  local shared_preload_libraries=
  local session_preload_libraries=
  local local_preload_libraries=
  local ssl_passphrase_command=
  local external_pid_file=
  local max_logical_replication_workers=
  [[ "$phase" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]
  test ! -e "$ORIG_PGDATA/standby.signal"
  test ! -L "$ORIG_PGDATA/standby.signal"
  test ! -e "$ORIG_PGDATA/recovery.signal"
  test ! -L "$ORIG_PGDATA/recovery.signal"
  archive_mode="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" -C archive_mode)"
  archive_command="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    -c archive_mode=on -C archive_command)"
  archive_library="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    -c archive_mode=on -C archive_library)"
  archive_cleanup_command="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    -C archive_cleanup_command)"
  recovery_end_command="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    -C recovery_end_command)"
  primary_conninfo="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" -C primary_conninfo)"
  restore_command="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" -C restore_command)"
  shared_preload_libraries="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    -C shared_preload_libraries)"
  session_preload_libraries="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    -C session_preload_libraries)"
  local_preload_libraries="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    -C local_preload_libraries)"
  ssl_passphrase_command="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    -C ssl_passphrase_command)"
  external_pid_file="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    -C external_pid_file)"
  max_logical_replication_workers="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    -C max_logical_replication_workers)"
  test "$archive_mode" = off
  test -z "$archive_command"
  test -z "$archive_library"
  test -z "$archive_cleanup_command"
  test -z "$recovery_end_command"
  test -z "$primary_conninfo"
  test -z "$restore_command"
  test -z "$shared_preload_libraries"
  test -z "$session_preload_libraries"
  test -z "$local_preload_libraries"
  test -z "$ssl_passphrase_command"
  test -z "$external_pid_file"
  test "$max_logical_replication_workers" = 0
  printf 'policy_id=%s|phase=%s|standby.signal=absent|' \
    "${PLAN[ORIGINAL_START_POLICY_ID]}" "$phase"
  printf 'recovery.signal=absent|archive_mode=off|archive_command=empty|'
  printf 'archive_library=empty|archive_cleanup_command=empty|'
  printf 'recovery_end_command=empty|primary_conninfo=empty|restore_command=empty|'
  printf 'shared_preload_libraries=empty|session_preload_libraries=empty|'
  printf 'local_preload_libraries=empty|ssl_passphrase_command=empty|'
  printf 'external_pid_file=empty|max_logical_replication_workers=0\n'
}

assert_effective_safe_maintenance_start_configuration() {
  local phase="$1"
  local archive_command=
  local archive_library=
  local setting=
  [[ "$phase" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]

  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C archive_mode)" = off
  archive_command="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -c archive_mode=on \
    -C archive_command)"
  archive_library="$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -c archive_mode=on \
    -C archive_library)"
  test -z "$archive_command"
  test -z "$archive_library"
  for setting in restore_command archive_cleanup_command recovery_end_command \
    primary_conninfo shared_preload_libraries session_preload_libraries \
    local_preload_libraries ssl_passphrase_command external_pid_file
  do
    test -z "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
      "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C "$setting")"
  done
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" \
    -C max_logical_replication_workers)" = 0
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C max_wal_senders)" = 0
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C autovacuum)" = off
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C logging_collector)" = off
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C log_destination)" = stderr
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C log_min_messages)" = log
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C jit)" = off
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C search_path)" \
    = pg_catalog,public
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C ssl)" = off
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C listen_addresses)" = 127.0.0.1
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" \
    -C unix_socket_directories)" = "$ORIG_SOCKET"
  test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" \
    "${ORIGINAL_SAFE_POSTGRES_OPTIONS[@]}" -C port)" = "$ORIG_PORT"
  printf 'policy_id=%s|phase=%s|all_external_hooks=empty|' \
    "${PLAN[ORIGINAL_START_POLICY_ID]}" "$phase"
  printf 'archive_mode=off|max_logical_replication_workers=0|'
  printf 'max_wal_senders=0|autovacuum=off|logging_collector=off|'
  printf 'log_destination=stderr|log_min_messages=log|jit=off|'
  printf 'search_path=pg_catalog,public|ssl=off|'
  printf 'listen_addresses=127.0.0.1|unix_socket=%s|port=%s\n' \
    "$ORIG_SOCKET" "$ORIG_PORT"
}

assert_original_path_properties zero-write >/dev/null
assert_no_external_recovery_configuration zero-write >/dev/null
assert_effective_safe_maintenance_start_configuration zero-write >/dev/null

test -d "$ISO_PGDATA"
test -d "$ISO_SOCKET"
test "$(stat -c '%U' "$ISO_PGDATA")" = thenam176
test "$(stat -c '%a' "$ISO_PGDATA")" = 700
test "$(stat -c '%U' "$ISO_SOCKET")" = thenam176
test "$(stat -c '%a' "$ISO_SOCKET")" = 700
test "$(<"$ISO_PGDATA/PG_VERSION")" = 16
test -z "$(find "$ISO_PGDATA" -xdev -type l -print -quit)"
test "$REPO" = /home/thenam176/projects/trading-agent
test "$(git -C "$REPO" rev-parse --show-toplevel)" = "$REPO"
git_dir="$(git -C "$REPO" rev-parse --path-format=absolute --git-dir)"
test "$(realpath -e -- "$git_dir")" = "$git_dir"

for parent in "${PLAN[EVIDENCE_PARENT]}" "${PLAN[PRESERVE_PARENT]}" \
  "${PLAN[BACKUP_PARENT]}" "${PLAN[SECRET_PARENT]}"
do
  assert_private_parent "$parent"
done

EVIDENCE_DIR="$(canonical_new_leaf "${PLAN[EVIDENCE_PARENT]}" "recovery-$RUN_ID-evidence")"
PRESERVE_DIR="$(canonical_new_leaf "${PLAN[PRESERVE_PARENT]}" "recovery-$RUN_ID-pgdata")"
LOG_PRESERVE_DIR="$(canonical_new_leaf "${PLAN[PRESERVE_PARENT]}" "recovery-$RUN_ID-postgres-logs")"
POST_ATTEMPT_DIR="$(canonical_new_leaf "${PLAN[PRESERVE_PARENT]}" "recovery-$RUN_ID-post-attempt-pgdata")"
BACKUP_DIR="$(canonical_new_leaf "${PLAN[BACKUP_PARENT]}" "recovery-$RUN_ID-backups")"
SECRET_DIR="$(canonical_new_leaf "${PLAN[SECRET_PARENT]}" "recovery-$RUN_ID-secrets")"
readonly EVIDENCE_DIR PRESERVE_DIR LOG_PRESERVE_DIR POST_ATTEMPT_DIR
readonly BACKUP_DIR SECRET_DIR

source_paths=(
  "$ORIG_PGDATA" "$ORIG_LOG_DIR" "$REPO" "$git_dir" "$ISO_PGDATA"
  "$APPROVAL_RECORD" "${PLAN[CHANGE_ARTIFACT]}"
)
destination_paths=(
  "$EVIDENCE_DIR" "$PRESERVE_DIR" "$LOG_PRESERVE_DIR" "$POST_ATTEMPT_DIR"
  "$BACKUP_DIR" "$SECRET_DIR"
)
for destination in "${destination_paths[@]}"; do
  for source_path in "${source_paths[@]}"; do
    if paths_overlap "$destination" "$source_path"; then
      exit 1
    fi
  done
done
for ((i=0; i<${#destination_paths[@]}; i++)); do
  for ((j=i+1; j<${#destination_paths[@]}; j++)); do
    if paths_overlap "${destination_paths[i]}" "${destination_paths[j]}"; then
      exit 1
    fi
  done
done
if paths_overlap "$ORIG_PGDATA" "$ISO_PGDATA" ||
   paths_overlap "$REPO" "$ISO_PGDATA"; then
  exit 1
fi
unset source_paths destination_paths destination source_path i j parent git_dir

orig_device="$(findmnt -n -o MAJ:MIN --target "$ORIG_PGDATA")"
preserve_device="$(findmnt -n -o MAJ:MIN --target "${PLAN[PRESERVE_PARENT]}")"
backup_device="$(findmnt -n -o MAJ:MIN --target "${PLAN[BACKUP_PARENT]}")"
test -n "$orig_device"
test -n "$preserve_device"
test -n "$backup_device"
test "$preserve_device" != "$orig_device"
test "$backup_device" != "$orig_device"

source_bytes="$(du -sx --block-size=1 "$ORIG_PGDATA" | awk '{print $1}')"
log_bytes="$(du -sx --block-size=1 "$ORIG_LOG_DIR" | awk '{print $1}')"
preserve_available="$(df -P --block-size=1 "${PLAN[PRESERVE_PARENT]}" | awk 'NR==2 {print $4}')"
backup_available="$(df -P --block-size=1 "${PLAN[BACKUP_PARENT]}" | awk 'NR==2 {print $4}')"
preserve_bytes=$((source_bytes + source_bytes + log_bytes))
test "$preserve_available" -ge $((preserve_bytes + preserve_bytes / 4))
test "$backup_available" -ge "$source_bytes"

test "$(<"$ORIG_PGDATA/PG_VERSION")" = 16
test -z "$(find "$ORIG_PGDATA" -xdev -type l -print -quit)"
test -z "$(find "$ORIG_LOG_DIR" -xdev -type l -print -quit)"
set +e
"$PG_BIN/pg_ctl" -D "$ORIG_PGDATA" status >/dev/null 2>&1
offline_rc=$?
set -e
test "$offline_rc" -eq 3
test -z "$(ss -ltnH | awk -v p=":$ORIG_PORT" '$4 ~ p"$" {print}')"
if test -f "$ORIG_PGDATA/postmaster.pid"; then
  stale_pid="$(sed -n '1p' "$ORIG_PGDATA/postmaster.pid")"
  [[ "$stale_pid" =~ ^[1-9][0-9]*$ ]]
  if kill -0 "$stale_pid" 2>/dev/null; then
    # A live or reused PID makes the target identity ambiguous; do not touch it.
    exit 1
  fi
  unset stale_pid
fi

~~~

Only after every zero-write gate passes may the six unique directories be
created. Each must be empty, mode 0700, canonical, and nonsymlinked.

~~~bash
assert_approval_current
install -d -m 0700 "$EVIDENCE_DIR"

LAST_COLLISION_SAFE_EVIDENCE_PATH=

write_collision_safe_evidence_raw() {
  local preferred_path="$1"
  local message="$2"
  local fallback_path=
  local fallback_stem=
  LAST_COLLISION_SAFE_EVIDENCE_PATH=
  fallback_stem="$(basename -- "$preferred_path" .txt)"
  if (set -o noclobber; printf '%s\n' "$message" > "$preferred_path") \
      2>/dev/null; then
    LAST_COLLISION_SAFE_EVIDENCE_PATH="$preferred_path"
    return 0
  fi
  fallback_path="$(mktemp \
    "$EVIDENCE_DIR/$fallback_stem-collision-XXXXXXXX.txt")" || return 1
  printf '%s\n' "$message" > "$fallback_path"
  LAST_COLLISION_SAFE_EVIDENCE_PATH="$fallback_path"
}

write_collision_safe_evidence_best_effort() {
  local preferred_path="$1"
  local message="$2"
  local evidence_path=
  local sync_record_path=
  local file_sync_rc=
  local evidence_dir_sync_rc=
  local sync_record_sync_rc=
  local sync_record_dir_sync_rc=

  write_collision_safe_evidence_raw "$preferred_path" "$message" || return 1
  evidence_path="$LAST_COLLISION_SAFE_EVIDENCE_PATH"
  if sync -f "$evidence_path"; then file_sync_rc=0; else file_sync_rc=$?; fi
  if sync -f "$EVIDENCE_DIR"; then
    evidence_dir_sync_rc=0
  else
    evidence_dir_sync_rc=$?
  fi
  write_collision_safe_evidence_raw \
    "$EVIDENCE_DIR/decision-sync-attempt.exit" \
    "decision_path=$evidence_path
file_sync_exit=$file_sync_rc
evidence_dir_sync_exit=$evidence_dir_sync_rc" || return 1
  sync_record_path="$LAST_COLLISION_SAFE_EVIDENCE_PATH"
  if sync -f "$sync_record_path"; then
    sync_record_sync_rc=0
  else
    sync_record_sync_rc=$?
  fi
  if sync -f "$EVIDENCE_DIR"; then
    sync_record_dir_sync_rc=0
  else
    sync_record_dir_sync_rc=$?
  fi
  test "$file_sync_rc" -eq 0 &&
    test "$evidence_dir_sync_rc" -eq 0 &&
    test "$sync_record_sync_rc" -eq 0 &&
    test "$sync_record_dir_sync_rc" -eq 0
}

write_collision_safe_evidence_strict() {
  local preferred_path="$1"
  local message="$2"
  local evidence_path=
  local sync_record_path=
  local file_sync_rc=
  local evidence_dir_sync_rc=
  local sync_record_sync_rc=
  local sync_record_dir_sync_rc=

  write_collision_safe_evidence_raw "$preferred_path" "$message"
  evidence_path="$LAST_COLLISION_SAFE_EVIDENCE_PATH"
  if sync -f "$evidence_path"; then file_sync_rc=0; else file_sync_rc=$?; fi
  if sync -f "$EVIDENCE_DIR"; then
    evidence_dir_sync_rc=0
  else
    evidence_dir_sync_rc=$?
  fi
  write_collision_safe_evidence_raw \
    "$EVIDENCE_DIR/final-decision-sync.exit" \
    "decision_path=$evidence_path
file_sync_exit=$file_sync_rc
evidence_dir_sync_exit=$evidence_dir_sync_rc"
  sync_record_path="$LAST_COLLISION_SAFE_EVIDENCE_PATH"
  if sync -f "$sync_record_path"; then
    sync_record_sync_rc=0
  else
    sync_record_sync_rc=$?
  fi
  if sync -f "$EVIDENCE_DIR"; then
    sync_record_dir_sync_rc=0
  else
    sync_record_dir_sync_rc=$?
  fi
  test "$file_sync_rc" -eq 0
  test "$evidence_dir_sync_rc" -eq 0
  test "$sync_record_sync_rc" -eq 0
  test "$sync_record_dir_sync_rc" -eq 0
}

write_failure_decision_once() {
  local reason="$1"
  write_collision_safe_evidence_best_effort "$EVIDENCE_DIR/final-decision.txt" \
    "NO-GO — RECOVERY SUB-GATE FAILED: $reason"
}

evidence_failure_cleanup() {
  local original_rc="$1"
  trap - EXIT INT TERM
  set +e +u
  set +x
  if test "$original_rc" -ne 0; then
    write_failure_decision_once unexpected-pre-start-failure
  fi
  if declare -F cleanup_secrets >/dev/null; then
    cleanup_secrets
  fi
  exit "$original_rc"
}

trap 'evidence_failure_cleanup $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

install -d -m 0700 "$PRESERVE_DIR" "$LOG_PRESERVE_DIR" \
  "$POST_ATTEMPT_DIR" "$BACKUP_DIR" "$SECRET_DIR"
for path in "$EVIDENCE_DIR" "$PRESERVE_DIR" "$LOG_PRESERVE_DIR" \
  "$POST_ATTEMPT_DIR" "$BACKUP_DIR" "$SECRET_DIR"
do
  test ! -L "$path"
  test "$(realpath -e -- "$path")" = "$path"
  test "$(stat -c '%U' "$path")" = thenam176
  test "$(stat -c '%a' "$path")" = 700
  test -z "$(find "$path" -mindepth 1 -print -quit)"
done
test "$(approval_fd_stat "$APPROVAL_COPY_FD")" = "$approval_record_stat"
test "$(stat -Lc '%d|%i|%s|%f|%u|%g|%h|%Y|%Z' "$APPROVAL_RECORD")" \
  = "$approval_record_stat"
dd iflag=fullblock status=none <&"$APPROVAL_COPY_FD" \
  > "$EVIDENCE_DIR/approved-transcript.tsv"
chmod 0600 "$EVIDENCE_DIR/approved-transcript.tsv"
test "$(sha256sum "$EVIDENCE_DIR/approved-transcript.tsv" | awk '{print $1}')" \
  = "$approval_record_sha256"
printf 'fd_stat=%s\nsha256=%s\n' "$approval_record_stat" \
  "$approval_record_sha256" > "$EVIDENCE_DIR/approval-source.stat"
sha256sum "$EVIDENCE_DIR/approved-transcript.tsv" > "$EVIDENCE_DIR/approval.sha256"

test "$(approval_fd_stat "$CHANGE_ARTIFACT_COPY_FD")" = "$change_artifact_stat"
test "$(stat -Lc '%d|%i|%s|%f|%u|%g|%h|%Y|%Z' \
  "${PLAN[CHANGE_ARTIFACT]}")" = "$change_artifact_stat"
dd iflag=fullblock status=none <&"$CHANGE_ARTIFACT_COPY_FD" \
  > "$EVIDENCE_DIR/approved-change-artifact"
chmod 0600 "$EVIDENCE_DIR/approved-change-artifact"
test "$(sha256sum "$EVIDENCE_DIR/approved-change-artifact" | awk '{print $1}')" \
  = "${PLAN[CHANGE_ARTIFACT_SHA256]}"
printf 'fd_stat=%s\nsha256=%s\n' "$change_artifact_stat" \
  "$change_artifact_sha256" > "$EVIDENCE_DIR/change-artifact-source.stat"
sha256sum "$EVIDENCE_DIR/approved-change-artifact" \
  > "$EVIDENCE_DIR/change-artifact.sha256"
exec {APPROVAL_PARSE_FD}<&-
exec {APPROVAL_HASH_FD}<&-
exec {APPROVAL_COPY_FD}<&-
exec {CHANGE_ARTIFACT_HASH_FD}<&-
exec {CHANGE_ARTIFACT_COPY_FD}<&-
~~~

### 5.3 Parse credentials exactly; never source them

The parser accepts exactly five nonempty KEY=value lines, rejects duplicate or
unknown keys, CR/control syntax, symlinks, wrong owner/mode/link count, and
target mismatches. It never prints a value.

~~~bash
DB_HOST='' DB_PORT='' DB_NAME='' DB_USER='' DB_PASSWORD=''
parse_db_env() {
  local file="$1"
  local expected_host="$2"
  local expected_port="$3"
  local expected_db="$4"
  local expected_user="$5"
  declare -A values=()
  local line key value

  test -f "$file"
  test ! -L "$file"
  test "$(realpath -e -- "$file")" = "$file"
  test "$(stat -c '%U' "$file")" = thenam176
  test "$(stat -c '%a' "$file")" = 600
  test "$(stat -c '%h' "$file")" = 1

  while IFS= read -r line || test -n "$line"; do
    test -n "$line"
    [[ "$line" != *$'\r'* ]]
    [[ "$line" == *=* ]]
    key="${line%%=*}"
    value="${line#*=}"
    test -n "$value"
    case "$key" in
      TRADING_DATABASE_HOST|TRADING_DATABASE_PORT|TRADING_DATABASE_NAME|\
      TRADING_DATABASE_USER|TRADING_DATABASE_PASSWORD) ;;
      *) return 1 ;;
    esac
    test -z "${values[$key]+present}"
    values["$key"]="$value"
  done < "$file"

  test "${#values[@]}" -eq 5
  DB_HOST="${values[TRADING_DATABASE_HOST]}"
  DB_PORT="${values[TRADING_DATABASE_PORT]}"
  DB_NAME="${values[TRADING_DATABASE_NAME]}"
  DB_USER="${values[TRADING_DATABASE_USER]}"
  DB_PASSWORD="${values[TRADING_DATABASE_PASSWORD]}"
  test "$DB_HOST" = "$expected_host"
  test "$DB_PORT" = "$expected_port"
  test "$DB_NAME" = "$expected_db"
  test "$DB_USER" = "$expected_user"
  unset values line key value
}
escape_pgpass() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//:/\\:}"
  printf '%s' "$value"
}
percent_encode_secret() {
  local value="$1"
  local hex_case="$2"
  local safe_mode="${3:-unreserved}"
  local encoded=
  local character=
  local escaped=
  local code=
  local keep_literal=
  local i=
  local LC_ALL=C
  case "$hex_case" in upper|lower) ;; *) return 1 ;; esac
  case "$safe_mode" in unreserved|sqlalchemy-safe-space-plus) ;; *) return 1 ;; esac
  for ((i=0; i<${#value}; i++)); do
    character="${value:i:1}"
    keep_literal=false
    case "$character" in
      [A-Za-z0-9.~_-]) keep_literal=true ;;
      ' '|+)
        if test "$safe_mode" = sqlalchemy-safe-space-plus; then
          keep_literal=true
        fi
        ;;
    esac
    if test "$keep_literal" = true; then
      encoded+="$character"
    else
      printf -v code '%d' "'$character"
      if test "$hex_case" = upper; then
        printf -v escaped '%%%02X' "$code"
      else
        printf -v escaped '%%%02x' "$code"
      fi
      encoded+="$escaped"
    fi
  done
  printf '%s' "$encoded"
}
write_alembic_secret_patterns() {
  local output="$1"
  local secret="$2"
  local percent_upper=
  local percent_lower=
  local plus_upper=
  local plus_lower=
  local sqlalchemy_upper=
  local sqlalchemy_lower=
  local double_percent_upper=
  local double_percent_lower=
  local nested_sqlalchemy_upper=
  local nested_sqlalchemy_lower=
  test -n "$secret"
  test ! -e "$output"
  test ! -L "$output"
  percent_upper="$(percent_encode_secret "$secret" upper)"
  percent_lower="$(percent_encode_secret "$secret" lower)"
  plus_upper="${percent_upper//%20/+}"
  plus_lower="${percent_lower//%20/+}"
  sqlalchemy_upper="$(percent_encode_secret \
    "$secret" upper sqlalchemy-safe-space-plus)"
  sqlalchemy_lower="$(percent_encode_secret \
    "$secret" lower sqlalchemy-safe-space-plus)"
  double_percent_upper="$(percent_encode_secret "$percent_upper" upper)"
  double_percent_lower="$(percent_encode_secret "$percent_lower" lower)"
  nested_sqlalchemy_upper="$(percent_encode_secret \
    "$sqlalchemy_upper" upper sqlalchemy-safe-space-plus)"
  nested_sqlalchemy_lower="$(percent_encode_secret \
    "$sqlalchemy_lower" lower sqlalchemy-safe-space-plus)"
  test -n "$percent_upper"
  test -n "$percent_lower"
  test -n "$plus_upper"
  test -n "$plus_lower"
  test -n "$sqlalchemy_upper"
  test -n "$sqlalchemy_lower"
  test -n "$double_percent_upper"
  test -n "$double_percent_lower"
  test -n "$nested_sqlalchemy_upper"
  test -n "$nested_sqlalchemy_lower"
  {
    printf '%s\n' "$secret"
    printf '%s\n' "$percent_upper" "$percent_lower"
    printf '%s\n' "$plus_upper" "$plus_lower"
    printf '%s\n' "$sqlalchemy_upper" "$sqlalchemy_lower"
    printf '%s\n' "$double_percent_upper" "$double_percent_lower"
    printf '%s\n' "$nested_sqlalchemy_upper" "$nested_sqlalchemy_lower"
  } > "$output"
  chmod 0600 "$output"
}
make_pgpass() {
  local output="$1"
  local pgpass_database="${2:-$DB_NAME}"
  test ! -e "$output"
  test ! -L "$output"
  printf '%s:%s:%s:%s:%s\n' \
    "$(escape_pgpass "$DB_HOST")" "$(escape_pgpass "$DB_PORT")" \
    "$(escape_pgpass "$pgpass_database")" "$(escape_pgpass "$DB_USER")" \
    "$(escape_pgpass "$DB_PASSWORD")" > "$output"
  chmod 0600 "$output"
  DB_HOST='' DB_PORT='' DB_NAME='' DB_USER='' DB_PASSWORD=''
}

readonly READER_PGPASS="$SECRET_DIR/original-reader.pgpass"
readonly OWNER_PGPASS="$SECRET_DIR/original-owner.pgpass"
readonly ADMIN_PGPASS="$SECRET_DIR/original-admin.pgpass"
readonly ISO_ADMIN_PGPASS="$SECRET_DIR/isolated-admin.pgpass"
readonly ISO_RESTORE_PGPASS="$SECRET_DIR/isolated-restore.pgpass"
readonly ALEMBIC_RAW_STDOUT="$SECRET_DIR/alembic-0004.stdout.raw"
readonly ALEMBIC_RAW_STDERR="$SECRET_DIR/alembic-0004.stderr.raw"
readonly ALEMBIC_SECRET_PATTERNS="$SECRET_DIR/alembic-secret-patterns.txt"

parse_db_env "$READER_ENV" "$ORIG_HOST" "$ORIG_PORT" "$ORIG_DB" trading_reader
make_pgpass "$READER_PGPASS"
parse_db_env "$OWNER_ENV" "$ORIG_HOST" "$ORIG_PORT" "$ORIG_DB" trading_owner
make_pgpass "$OWNER_PGPASS"
parse_db_env "$ADMIN_ENV" "$ORIG_HOST" "$ORIG_PORT" "$ORIG_ADMIN_DB" postgres
make_pgpass "$ADMIN_PGPASS" "$ORIG_DB"
parse_db_env "$ISO_ADMIN_ENV" "$ISO_HOST" "$ISO_PORT" "$ISO_ADMIN_DB" postgres
make_pgpass "$ISO_ADMIN_PGPASS"
parse_db_env "$ISO_ADMIN_ENV" "$ISO_HOST" "$ISO_PORT" "$ISO_ADMIN_DB" postgres
make_pgpass "$ISO_RESTORE_PGPASS" "$ISO_RESTORE_DB"

cleanup_secrets() {
  set +x
  DB_HOST='' DB_PORT='' DB_NAME='' DB_USER='' DB_PASSWORD=''
  rm -f -- "$READER_PGPASS" "$OWNER_PGPASS" "$ADMIN_PGPASS" \
    "$ISO_ADMIN_PGPASS" "$ISO_RESTORE_PGPASS" \
    "$ALEMBIC_RAW_STDOUT" "$ALEMBIC_RAW_STDERR" \
    "$ALEMBIC_SECRET_PATTERNS"
  rmdir -- "$SECRET_DIR" 2>/dev/null || true
  unset "${POSTGRES_AMBIENT_ENV_NAMES[@]}"
}

readonly PGOPTIONS_ADMIN_POLICY='-c session_preload_libraries= -c local_preload_libraries= -c search_path=pg_catalog,public -c jit=off'
readonly PGOPTIONS_UNPRIVILEGED_POLICY='-c local_preload_libraries= -c search_path=pg_catalog,public -c jit=off'

run_postgres_client_no_connection() {
  assert_no_ambient_postgres_environment
  env -i LC_ALL=C "$@"
}

run_libpq_client() {
  local profile="$1"
  local passfile="$2"
  local application_name="$3"
  local client_pgoptions=
  shift 3
  case "$profile" in
    admin) client_pgoptions="$PGOPTIONS_ADMIN_POLICY" ;;
    unprivileged) client_pgoptions="$PGOPTIONS_UNPRIVILEGED_POLICY" ;;
    *) return 1 ;;
  esac
  test -f "$passfile"
  test ! -L "$passfile"
  [[ "$application_name" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]]
  assert_no_ambient_postgres_environment
  env -i LC_ALL=C \
    PGAPPNAME="$application_name" \
    PGCONNECT_TIMEOUT=10 \
    PGOPTIONS="$client_pgoptions" \
    PGPASSFILE="$passfile" \
    PGSSLMODE=disable \
    PGTARGETSESSIONATTRS=any \
    "$@"
}

orig_reader_psql() {
  run_libpq_client unprivileged "$READER_PGPASS" recovery-orig-reader \
    "$PG_BIN/psql" -X -q -w \
    --host "$ORIG_HOST" --port "$ORIG_PORT" \
    --username trading_reader --dbname "$ORIG_DB" "$@"
}
orig_admin_psql() {
  run_libpq_client admin "$ADMIN_PGPASS" recovery-orig-admin \
    "$PG_BIN/psql" -X -q -w \
    --host "$ORIG_HOST" --port "$ORIG_PORT" \
    --username postgres --dbname "$ORIG_DB" "$@"
}
iso_admin_psql() {
  run_libpq_client admin "$ISO_ADMIN_PGPASS" recovery-iso-admin \
    "$PG_BIN/psql" -X -q -w \
    --host "$ISO_HOST" --port "$ISO_PORT" \
    --username postgres --dbname "$ISO_ADMIN_DB" "$@"
}
iso_restore_psql() {
  run_libpq_client admin "$ISO_RESTORE_PGPASS" recovery-iso-restore \
    "$PG_BIN/psql" -X -q -w \
    --host "$ISO_HOST" --port "$ISO_PORT" \
    --username postgres --dbname "$ISO_RESTORE_DB" "$@"
}

capture_side_effect_object_mismatches() {
  local psql_wrapper="$1"
  local expected_state="$2"
  local output="$3"
  case "$expected_state" in empty|0003|0004) ;; *) return 1 ;; esac
  "$psql_wrapper" -v ON_ERROR_STOP=1 -At \
    --set=expected_state="$expected_state" > "$output" <<'SQL'
BEGIN READ ONLY;
WITH params(state) AS (VALUES (:'expected_state'::text)),
expected_extensions(extname,nspname,owner_name,extversion,relocatable) AS (
  VALUES ('plpgsql','pg_catalog','postgres','1.0',false)
), actual_extensions(extname,nspname,owner_name,extversion,relocatable) AS (
  SELECT extension.extname::text, namespace.nspname::text,
         pg_get_userbyid(extension.extowner), extension.extversion::text,
         extension.extrelocatable
  FROM pg_extension extension
  JOIN pg_namespace namespace ON namespace.oid=extension.extnamespace
), expected_languages(lanname) AS (
  VALUES ('internal'),('c'),('sql'),('plpgsql')
), actual_languages(lanname) AS (
  SELECT language.lanname::text FROM pg_language language
)
SELECT 'subscription|' || subscription.subname || '|owner=' ||
       pg_get_userbyid(subscription.subowner)
FROM pg_subscription subscription
UNION ALL
SELECT 'publication|' || publication.pubname || '|owner=' ||
       pg_get_userbyid(publication.pubowner)
FROM pg_publication publication
UNION ALL
SELECT 'event-trigger|' || event_trigger.evtname || '|owner=' ||
       pg_get_userbyid(event_trigger.evtowner)
FROM pg_event_trigger event_trigger
UNION ALL
SELECT 'foreign-server|' || server.srvname || '|owner=' ||
       pg_get_userbyid(server.srvowner) || '|fdw=' || wrapper.fdwname
FROM pg_foreign_server server
JOIN pg_foreign_data_wrapper wrapper ON wrapper.oid=server.srvfdw
UNION ALL
SELECT 'user-mapping|server=' || server.srvname || '|user=' ||
       CASE WHEN mapping.umuser=0 THEN 'PUBLIC'
            ELSE pg_get_userbyid(mapping.umuser) END
FROM pg_user_mapping mapping
JOIN pg_foreign_server server ON server.oid=mapping.umserver
UNION ALL
SELECT 'replication-slot|' || slot.slot_name || '|type=' || slot.slot_type ||
       '|database=' || coalesce(slot.database,'')
FROM pg_replication_slots slot
UNION ALL
SELECT 'logical-backend|type=' || activity.backend_type || '|user=' ||
       coalesce(activity.usename,'') || '|database=' || coalesce(activity.datname,'')
FROM pg_stat_activity activity
WHERE activity.backend_type LIKE 'logical replication%'
   OR activity.backend_type='walsender'
UNION ALL
SELECT 'db-role-setting|database=' ||
       CASE WHEN setting.setdatabase=0 THEN 'ALL'
            ELSE coalesce(database_row.datname,'unknown') END ||
       '|role=' || CASE WHEN setting.setrole=0 THEN 'ALL'
                        ELSE coalesce(role_row.rolname,'unknown') END ||
       '|setting_count=' || cardinality(setting.setconfig)
FROM pg_db_role_setting setting
LEFT JOIN pg_database database_row ON database_row.oid=setting.setdatabase
LEFT JOIN pg_roles role_row ON role_row.oid=setting.setrole
UNION ALL
SELECT 'missing-or-wrong-extension|' || extname || '|' || nspname || '|' ||
       owner_name || '|' || extversion || '|relocatable=' || relocatable
FROM (SELECT * FROM expected_extensions EXCEPT SELECT * FROM actual_extensions) q
UNION ALL
SELECT 'extra-or-wrong-extension|' || extname || '|' || nspname || '|' ||
       owner_name || '|' || extversion || '|relocatable=' || relocatable
FROM (SELECT * FROM actual_extensions EXCEPT SELECT * FROM expected_extensions) q
UNION ALL
SELECT 'missing-language|' || lanname
FROM (SELECT * FROM expected_languages EXCEPT SELECT * FROM actual_languages) q
UNION ALL
SELECT 'unexpected-language|' || lanname
FROM (SELECT * FROM actual_languages EXCEPT SELECT * FROM expected_languages) q
UNION ALL
SELECT 'unexpected-schema|' || namespace.nspname || '|owner=' ||
       pg_get_userbyid(namespace.nspowner)
FROM pg_namespace namespace
WHERE namespace.nspname <> 'public'
  AND namespace.nspname <> 'information_schema'
  AND namespace.nspname !~ '^pg_'
UNION ALL
SELECT 'unexpected-foreign-table|' || namespace.nspname || '.' || relation.relname
FROM pg_class relation
JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
WHERE relation.relkind='f'
  AND namespace.nspname <> 'information_schema'
  AND namespace.nspname !~ '^pg_'
UNION ALL
SELECT 'unexpected-rewrite-rule|' || namespace.nspname || '.' ||
       relation.relname || '|' || rewrite_rule.rulename
FROM pg_rewrite rewrite_rule
JOIN pg_class relation ON relation.oid=rewrite_rule.ev_class
JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
WHERE rewrite_rule.rulename <> '_RETURN'
  AND namespace.nspname <> 'information_schema'
  AND namespace.nspname !~ '^pg_'
UNION ALL
SELECT 'unexpected-public-function|' || function_row.proname || '(' ||
       pg_get_function_identity_arguments(function_row.oid) || ')'
FROM pg_proc function_row
JOIN pg_namespace namespace ON namespace.oid=function_row.pronamespace
CROSS JOIN params
WHERE namespace.nspname='public'
  AND NOT (
    params.state='0004' AND function_row.proname='reject_job_event_mutation'
    AND pg_get_function_identity_arguments(function_row.oid)=''
    AND function_row.prorettype='trigger'::regtype
    AND function_row.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
    AND pg_get_userbyid(function_row.proowner)='trading_owner'
    AND function_row.prokind='f' AND function_row.provolatile='v'
    AND NOT function_row.prosecdef AND NOT function_row.proleakproof
    AND NOT function_row.proisstrict AND function_row.proparallel='u'
    AND function_row.proconfig IS NULL
    AND btrim(regexp_replace(function_row.prosrc,'[[:space:]]+',' ','g')) =
      'BEGIN RAISE EXCEPTION ''job_events is append-only'' USING ERRCODE = ''55000''; END;'
  )
UNION ALL
SELECT 'missing-0004-public-function|reject_job_event_mutation()'
FROM params
WHERE params.state='0004' AND NOT EXISTS (
  SELECT 1 FROM pg_proc function_row
  JOIN pg_namespace namespace ON namespace.oid=function_row.pronamespace
  WHERE namespace.nspname='public'
    AND function_row.proname='reject_job_event_mutation'
    AND pg_get_function_identity_arguments(function_row.oid)=''
    AND function_row.prorettype='trigger'::regtype
    AND function_row.prolang=(SELECT oid FROM pg_language WHERE lanname='plpgsql')
    AND pg_get_userbyid(function_row.proowner)='trading_owner'
    AND function_row.prokind='f' AND function_row.provolatile='v'
    AND NOT function_row.prosecdef AND NOT function_row.proleakproof
    AND NOT function_row.proisstrict AND function_row.proparallel='u'
    AND function_row.proconfig IS NULL
    AND btrim(regexp_replace(function_row.prosrc,'[[:space:]]+',' ','g')) =
      'BEGIN RAISE EXCEPTION ''job_events is append-only'' USING ERRCODE = ''55000''; END;'
)
UNION ALL
SELECT 'unexpected-dml-trigger|' || namespace.nspname || '.' || relation.relname ||
       '|' || trigger_row.tgname
FROM pg_trigger trigger_row
JOIN pg_class relation ON relation.oid=trigger_row.tgrelid
JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
JOIN pg_proc trigger_function ON trigger_function.oid=trigger_row.tgfoid
JOIN pg_namespace trigger_function_namespace
  ON trigger_function_namespace.oid=trigger_function.pronamespace
CROSS JOIN params
WHERE NOT trigger_row.tgisinternal
  AND namespace.nspname <> 'information_schema'
  AND namespace.nspname !~ '^pg_'
  AND NOT (
    params.state='0004' AND namespace.nspname='public'
    AND relation.relname='job_events'
    AND trigger_row.tgname='trg_job_events_append_only'
    AND trigger_row.tgenabled='O' AND trigger_row.tgtype=27
    AND trigger_function_namespace.nspname='public'
    AND trigger_function.proname='reject_job_event_mutation'
    AND pg_get_function_identity_arguments(trigger_function.oid)=''
  )
UNION ALL
SELECT 'missing-0004-dml-trigger|public.job_events|trg_job_events_append_only'
FROM params
WHERE params.state='0004' AND NOT EXISTS (
  SELECT 1 FROM pg_trigger trigger_row
  JOIN pg_class relation ON relation.oid=trigger_row.tgrelid
  JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
  JOIN pg_proc trigger_function ON trigger_function.oid=trigger_row.tgfoid
  JOIN pg_namespace trigger_function_namespace
    ON trigger_function_namespace.oid=trigger_function.pronamespace
  WHERE namespace.nspname='public' AND relation.relname='job_events'
    AND trigger_row.tgname='trg_job_events_append_only'
    AND NOT trigger_row.tgisinternal AND trigger_row.tgenabled='O'
    AND trigger_row.tgtype=27
    AND trigger_function_namespace.nspname='public'
    AND trigger_function.proname='reject_job_event_mutation'
    AND pg_get_function_identity_arguments(trigger_function.oid)=''
)
ORDER BY 1;
COMMIT;
SQL
}

assert_archive_toc_side_effect_free() {
  local catalog="$1"
  local expected_state="$2"
  local evidence="$3"
  local forbidden_count=
  local plpgsql_extension_count=
  local plpgsql_comment_count=
  local other_extension_count=
  local function_count=
  local expected_function_count=
  local trigger_count=
  local expected_trigger_count=
  case "$expected_state" in 0003|0004) ;; *) return 1 ;; esac
  test -f "$catalog"
  test ! -L "$catalog"
  read -r forbidden_count plpgsql_extension_count plpgsql_comment_count \
    other_extension_count function_count expected_function_count \
    trigger_count expected_trigger_count < <(awk '
    /^[0-9]+; 3079 [1-9][0-9]* EXTENSION - plpgsql( postgres| )?$/ {
      plpgsql_extension++; next
    }
    /^[0-9]+; 0 0 COMMENT - EXTENSION plpgsql( postgres| )?$/ {
      plpgsql_comment++; next
    }
    /^[0-9]+; [0-9]+ [0-9]+ EXTENSION .*$/ {other_extension++; next}
    /^[0-9]+; [0-9]+ [0-9]+ (COMMENT|ACL) [^ ]+ EXTENSION .*$/ {
      other_extension++; next
    }
    /^[0-9]+; [0-9]+ [0-9]+ (EVENT TRIGGER|SUBSCRIPTION|PUBLICATION|PUBLICATION TABLE|FOREIGN DATA WRAPPER|SERVER|USER MAPPING|FOREIGN TABLE|PROCEDURAL LANGUAGE|ACCESS METHOD) .*$/ {
      forbidden++
    }
    /^[0-9]+; [0-9]+ [0-9]+ (COMMENT|ACL) [^ ]+ (EVENT TRIGGER|SUBSCRIPTION|PUBLICATION|PUBLICATION TABLE|FOREIGN DATA WRAPPER|SERVER|USER MAPPING|FOREIGN TABLE|PROCEDURAL LANGUAGE|ACCESS METHOD) .*$/ {
      forbidden++
    }
    /^[0-9]+; [0-9]+ [0-9]+ FUNCTION public .*$/ {function_count++}
    /^[0-9]+; 1255 [1-9][0-9]* FUNCTION public reject_job_event_mutation\(\) trading_owner$/ {
      expected_function++
    }
    /^[0-9]+; [0-9]+ [0-9]+ TRIGGER public .*$/ {trigger_count++}
    /^[0-9]+; 2620 [1-9][0-9]* TRIGGER public job_events trg_job_events_append_only trading_owner$/ {
      expected_trigger++
    }
    END {
      printf "%d %d %d %d %d %d %d %d\n", forbidden+0,
        plpgsql_extension+0, plpgsql_comment+0, other_extension+0,
        function_count+0, expected_function+0, trigger_count+0,
        expected_trigger+0
    }
  ' "$catalog")
  : > "$evidence"
  if test "$forbidden_count" -ne 0 || test "$other_extension_count" -ne 0; then
    printf 'forbidden_side_effect_toc_entries=%s\n' "$forbidden_count" \
      > "$evidence"
    printf 'forbidden_extension_toc_entries=%s\n' "$other_extension_count" \
      >> "$evidence"
    return 1
  fi
  if ! { test "$plpgsql_extension_count" -eq 0 &&
         test "$plpgsql_comment_count" -eq 0; } &&
     ! { test "$plpgsql_extension_count" -eq 1 &&
         test "$plpgsql_comment_count" -eq 1; }; then
    printf 'wrong_plpgsql_extension_entries=%s\n' "$plpgsql_extension_count" \
      > "$evidence"
    printf 'wrong_plpgsql_comment_entries=%s\n' "$plpgsql_comment_count" \
      >> "$evidence"
    return 1
  fi
  if test "$expected_state" = 0003; then
    if test "$function_count" -ne 0 || test "$trigger_count" -ne 0; then
      printf 'unexpected_0003_function_entries=%s\n' "$function_count" \
        > "$evidence"
      printf 'unexpected_0003_trigger_entries=%s\n' "$trigger_count" \
        >> "$evidence"
      return 1
    fi
  else
    if test "$function_count" -ne 1 || test "$expected_function_count" -ne 1 ||
       test "$trigger_count" -ne 1 || test "$expected_trigger_count" -ne 1; then
      printf 'wrong_0004_function_entries=%s expected_matches=%s\n' \
        "$function_count" "$expected_function_count" > "$evidence"
      printf 'wrong_0004_trigger_entries=%s expected_matches=%s\n' \
        "$trigger_count" "$expected_trigger_count" >> "$evidence"
      return 1
    fi
  fi
}

build_restore_list_without_builtin_plpgsql() {
  local catalog="$1"
  local output="$2"
  local hash_output="$3"
  test -f "$catalog"
  test ! -L "$catalog"
  test ! -e "$output"
  test ! -L "$output"
  test ! -e "$output.partial"
  test ! -L "$output.partial"
  awk '
    /^[0-9]+; 3079 [1-9][0-9]* EXTENSION - plpgsql( postgres| )?$/ {
      removed++; next
    }
    /^[0-9]+; 0 0 COMMENT - EXTENSION plpgsql( postgres| )?$/ {
      removed++; next
    }
    {print}
    END {if (removed != 0 && removed != 2) exit 1}
  ' "$catalog" > "$output.partial"
  test -s "$output.partial"
  awk '
    /^[0-9]+; [0-9]+ [0-9]+ EXTENSION .*$/ {bad=1}
    /^[0-9]+; [0-9]+ [0-9]+ (COMMENT|ACL) [^ ]+ EXTENSION .*$/ {bad=1}
    END {exit bad}
  ' "$output.partial"
  chmod 0600 "$output.partial"
  mv "$output.partial" "$output"
  sha256sum "$output" > "$hash_output"
}
~~~

Every direct PostgreSQL utility below uses one of these explicit wrappers. The
parent shell first unsets the complete PostgreSQL 16 client/server environment
list and rejects any remaining exported `PG*` or `PSQL*` name. Each wrapped
client then runs under `env -i` with only its explicit passfile, endpoint
arguments, TLS policy, application name, timeout, and the reviewed admin or
unprivileged `PGOPTIONS`. The admin policy clears session/local preload
libraries; both policies clear local preload libraries, disable JIT library
loading, and bind `search_path`. Section 5.10 separately binds Alembic because
the application's SQLAlchemy URL supplies its own statement-timeout options.
No service file, host address, options string, password, or target can leak in
from the launcher. The side-effect gate emits names, owners, and counts only;
it never selects subscription conninfo or foreign server/mapping options.

### 5.4 Stop and assert every application unit

~~~bash
user_units=(
  trading-job-scheduler.timer trading-safety-state-export.timer
  trading-job-scheduler.service trading-job-worker.service
  trading-job-api.service trading-control-api.service
  trading-safety-state-export.service trading-dashboard.service
  trading-agent.service
)
system_units=(
  trading-semantic-input-refresh.timer
  trading-semantic-input-refresh.service
)
assert_application_units_inactive() {
  local phase="$1"
  local unit state
  for unit in "${user_units[@]}"; do
    state="$(systemctl --user is-active "$unit" || true)"
    printf '%s|user|%s|%s\n' "$phase" "$unit" "$state" \
      >> "$EVIDENCE_DIR/application-unit-states.txt"
    test "$state" = inactive
  done
  for unit in "${system_units[@]}"; do
    state="$(sudo systemctl is-active "$unit" || true)"
    printf '%s|system|%s|%s\n' "$phase" "$unit" "$state" \
      >> "$EVIDENCE_DIR/application-unit-states.txt"
    test "$state" = inactive
  done
}

assert_approval_current
systemctl --user stop "${user_units[@]}"
sudo systemctl stop "${system_units[@]}"
assert_application_units_inactive after-stop
~~~

### 5.5 Capture exact offline identity and preserve PGDATA

~~~bash
assert_original_path_properties before-preservation \
  > "$EVIDENCE_DIR/original-paths-before-preservation.txt"
assert_no_external_recovery_configuration before-preservation \
  > "$EVIDENCE_DIR/original-recovery-settings-before-preservation.txt"
assert_effective_safe_maintenance_start_configuration before-preservation \
  > "$EVIDENCE_DIR/original-safe-start-settings-before-preservation.txt"
test "$("$PG_BIN/postgres" --version | awk '{print $NF}' | cut -d. -f1)" = 16
test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" -C data_directory)" = "$ORIG_PGDATA"
test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" -C port)" = "$ORIG_PORT"
test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" -C listen_addresses)" = 127.0.0.1
test "$("$PG_BIN/postgres" -D "$ORIG_PGDATA" -C unix_socket_directories)" = "$ORIG_SOCKET"

"$PG_BIN/pg_controldata" -D "$ORIG_PGDATA" \
  > "$EVIDENCE_DIR/pg-controldata-before.txt"
system_id="$(awk -F': *' '/Database system identifier/ {print $2}' \
  "$EVIDENCE_DIR/pg-controldata-before.txt")"
test "$system_id" = "${PLAN[ORIG_SYSTEM_ID]}"

set +e
"$PG_BIN/pg_ctl" -D "$ORIG_PGDATA" status \
  > "$EVIDENCE_DIR/pg-ctl-before.txt" 2>&1
offline_rc=$?
set -e
printf 'exit_code=%s\n' "$offline_rc" >> "$EVIDENCE_DIR/pg-ctl-before.txt"
test "$offline_rc" -eq 3
test -z "$(ss -ltnH | awk -v p=":$ORIG_PORT" '$4 ~ p"$" {print}')"
if test -f "$ORIG_PGDATA/postmaster.pid"; then
  stale_pid="$(sed -n '1p' "$ORIG_PGDATA/postmaster.pid")"
  [[ "$stale_pid" =~ ^[1-9][0-9]*$ ]]
  if kill -0 "$stale_pid" 2>/dev/null; then
    exit 1
  fi
  unset stale_pid
fi
assert_application_units_inactive before-preservation

rsync -aHAX --numeric-ids "$ORIG_PGDATA/" "$PRESERVE_DIR/"
rsync -aHAX --numeric-ids "$ORIG_LOG_DIR/" "$LOG_PRESERVE_DIR/"

set +e
sync -f "$PRESERVE_DIR"
pgdata_sync_rc=$?
sync -f "$LOG_PRESERVE_DIR"
log_sync_rc=$?
set -e
printf 'pgdata_sync_exit=%s\nlog_sync_exit=%s\n' \
  "$pgdata_sync_rc" "$log_sync_rc" > "$EVIDENCE_DIR/cold-copy-sync.exit"
test "$pgdata_sync_rc" -eq 0
test "$log_sync_rc" -eq 0

(cd "$ORIG_PGDATA" &&
  find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) \
  > "$EVIDENCE_DIR/original-files.sha256"
(cd "$PRESERVE_DIR" &&
  find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) \
  > "$EVIDENCE_DIR/preserved-files.sha256"
(cd "$ORIG_PGDATA" &&
  find . -printf '%P|%y|%m|%U|%G|%l\n' | LC_ALL=C sort) \
  > "$EVIDENCE_DIR/original-metadata.txt"
(cd "$PRESERVE_DIR" &&
  find . -printf '%P|%y|%m|%U|%G|%l\n' | LC_ALL=C sort) \
  > "$EVIDENCE_DIR/preserved-metadata.txt"
cmp "$EVIDENCE_DIR/original-files.sha256" "$EVIDENCE_DIR/preserved-files.sha256"
cmp "$EVIDENCE_DIR/original-metadata.txt" "$EVIDENCE_DIR/preserved-metadata.txt"

(cd "$ORIG_LOG_DIR" &&
  find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) \
  > "$EVIDENCE_DIR/pre-start-log-original-files.sha256"
(cd "$LOG_PRESERVE_DIR" &&
  find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) \
  > "$EVIDENCE_DIR/pre-start-log-preserved-files.sha256"
(cd "$ORIG_LOG_DIR" &&
  find . -printf '%P|%y|%m|%U|%G|%l\n' | LC_ALL=C sort) \
  > "$EVIDENCE_DIR/pre-start-log-original-metadata.txt"
(cd "$LOG_PRESERVE_DIR" &&
  find . -printf '%P|%y|%m|%U|%G|%l\n' | LC_ALL=C sort) \
  > "$EVIDENCE_DIR/pre-start-log-preserved-metadata.txt"
test -s "$EVIDENCE_DIR/pre-start-log-original-files.sha256"
grep -Fq '  ./trading-agent.log' \
  "$EVIDENCE_DIR/pre-start-log-original-files.sha256"
cmp "$EVIDENCE_DIR/pre-start-log-original-files.sha256" \
  "$EVIDENCE_DIR/pre-start-log-preserved-files.sha256"
cmp "$EVIDENCE_DIR/pre-start-log-original-metadata.txt" \
  "$EVIDENCE_DIR/pre-start-log-preserved-metadata.txt"

sha256sum "$EVIDENCE_DIR/preserved-files.sha256" \
  "$EVIDENCE_DIR/preserved-metadata.txt" \
  "$EVIDENCE_DIR/pre-start-log-preserved-files.sha256" \
  "$EVIDENCE_DIR/pre-start-log-preserved-metadata.txt" \
  "$EVIDENCE_DIR/cold-copy-sync.exit" \
  > "$EVIDENCE_DIR/preservation-manifests.sha256"
sync -f "$EVIDENCE_DIR"
sync -f "$PRESERVE_DIR"
sync -f "$LOG_PRESERVE_DIR"
~~~

The original postmaster.pid remains present and unchanged in the preservation.
No operator command removes it. The entire pre-start PostgreSQL log directory,
not only the configured log file, is preserved, hashed, metadata-compared, and
durably synced before any original start.

### 5.6 Verify the literal historical 0003 dump

~~~bash
readonly HISTORICAL_DUMP=/home/thenam176/.local/share/trading-agent-backups/phase4-preapply-20260712T131219Z.dump
test -f "$HISTORICAL_DUMP"
test ! -L "$HISTORICAL_DUMP"
test "$(realpath -e -- "$HISTORICAL_DUMP")" = "$HISTORICAL_DUMP"
test "$(stat -c '%a' "$HISTORICAL_DUMP")" = 600
test "$(stat -c '%s' "$HISTORICAL_DUMP")" = 31823453
test "$(sha256sum "$HISTORICAL_DUMP" | awk '{print $1}')" \
  = 1f4b64bd74811ec9977befd1590e59872c3a32b9988686c08070756cc97798f8
run_postgres_client_no_connection "$PG_BIN/pg_restore" --list "$HISTORICAL_DUMP" \
  > "$EVIDENCE_DIR/historical-0003.catalog"
test -s "$EVIDENCE_DIR/historical-0003.catalog"
assert_archive_toc_side_effect_free "$EVIDENCE_DIR/historical-0003.catalog" \
  0003 "$EVIDENCE_DIR/historical-0003-forbidden-toc.txt"
stat -c 'path=%n mode=%a size=%s mtime=%y' "$HISTORICAL_DUMP" \
  > "$EVIDENCE_DIR/historical-0003.stat"
sha256sum "$HISTORICAL_DUMP" > "$EVIDENCE_DIR/historical-0003.sha256"
~~~

The 0003 head is historical restore evidence; the isolated fallback phase must
verify it from the restored alembic_version row.

### 5.7 One controlled original start and post-start identity gates

Immediately before the approved start attempt, replace the secret-only exit
trap with a failure trap. It stops only a live postmaster whose PID file, PID,
executable, working directory, port, and socket all match the bound original
cluster. It makes at most one fast-stop attempt, never overwrites evidence,
never retries, and keeps application units inactive. An identity mismatch is a
new incident and is never permission to kill a process.

~~~bash
ORIGINAL_START_ATTEMPTED=false
ORIGINAL_IDENTITY_BOUND=false
ORIGINAL_STOP_ATTEMPTED=false
ORIGINAL_STOP_SUCCEEDED=false
RECOVERY_FINISHED=false
RECOVERY_GATE_FAILED=false
ORIGINAL_BOUND_PID=
ORIGINAL_BOUND_START_EPOCH=
ORIGINAL_BOUND_PROC_START_TICKS=

assert_bound_original_postmaster() {
  local observed_pid=
  local observed_start_epoch=
  local observed_proc_start_ticks=
  local found_data_arg=false
  local argv_i=
  local -a observed_pid_lines=()
  local -a observed_argv=()

  test "$ORIGINAL_IDENTITY_BOUND" = true || return 1
  test -f "$ORIG_PGDATA/postmaster.pid" || return 1
  test ! -L "$ORIG_PGDATA/postmaster.pid" || return 1
  mapfile -t observed_pid_lines < "$ORIG_PGDATA/postmaster.pid" || return 1
  test "${#observed_pid_lines[@]}" -ge 6 || return 1
  observed_pid="${observed_pid_lines[0]}"
  observed_start_epoch="${observed_pid_lines[2]}"
  test "$observed_pid" = "$ORIGINAL_BOUND_PID" || return 1
  test "$observed_start_epoch" = "$ORIGINAL_BOUND_START_EPOCH" || return 1
  test "${observed_pid_lines[1]}" = "$ORIG_PGDATA" || return 1
  test "${observed_pid_lines[3]}" = "$ORIG_PORT" || return 1
  test "${observed_pid_lines[4]}" = "$ORIG_SOCKET" || return 1
  kill -0 "$observed_pid" 2>/dev/null || return 1
  test "$(readlink -e -- "/proc/$observed_pid/cwd" 2>/dev/null)" \
    = "$ORIG_PGDATA" || return 1
  test "$(readlink -e -- "/proc/$observed_pid/exe" 2>/dev/null)" \
    = "$PG_BIN/postgres" || return 1
  observed_proc_start_ticks="$(awk '{print $22}' "/proc/$observed_pid/stat" \
    2>/dev/null)" || return 1
  test "$observed_proc_start_ticks" = "$ORIGINAL_BOUND_PROC_START_TICKS" || return 1
  mapfile -d '' -t observed_argv < "/proc/$observed_pid/cmdline" || return 1
  for ((argv_i=0; argv_i<${#observed_argv[@]}-1; argv_i++)); do
    if test "${observed_argv[argv_i]}" = -D &&
       test "${observed_argv[argv_i+1]}" = "$ORIG_PGDATA"; then
      found_data_arg=true
    fi
  done
  test "$found_data_arg" = true || return 1
  test ! -L "$ORIG_PGDATA" || return 1
  test "$(realpath -e -- "$ORIG_PGDATA")" = "$ORIG_PGDATA" || return 1
  test "$(stat -Lc '%d:%i' "$ORIG_PGDATA")" \
    = "$ORIG_PGDATA_DEVICE_INODE" || return 1
}

preserve_post_attempt_pgdata() (
  set -euo pipefail
  local copy_rc=
  local copy_sync_rc=
  local evidence_sync_rc=
  local final_content_sync_rc=
  local manifest_check_rc=
  local terminal_sync_rc=
  local one_sync_rc=
  local evidence_path=

  test -z "$(find "$POST_ATTEMPT_DIR" -mindepth 1 -print -quit)"
  set +e
  rsync -aHAX --numeric-ids "$ORIG_PGDATA/" "$POST_ATTEMPT_DIR/"
  copy_rc=$?
  set -e
  printf 'copy_exit=%s\n' "$copy_rc" \
    > "$EVIDENCE_DIR/post-attempt-preservation.exit"
  test "$copy_rc" -eq 0

  set +e
  sync -f "$POST_ATTEMPT_DIR"
  copy_sync_rc=$?
  set -e
  printf 'copy_sync_exit=%s\n' "$copy_sync_rc" \
    >> "$EVIDENCE_DIR/post-attempt-preservation.exit"
  test "$copy_sync_rc" -eq 0

  (cd "$ORIG_PGDATA" &&
    find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) \
    > "$EVIDENCE_DIR/post-attempt-original-files.sha256"
  (cd "$POST_ATTEMPT_DIR" &&
    find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) \
    > "$EVIDENCE_DIR/post-attempt-preserved-files.sha256"
  (cd "$ORIG_PGDATA" &&
    find . -printf '%P|%y|%m|%U|%G|%l\n' | LC_ALL=C sort) \
    > "$EVIDENCE_DIR/post-attempt-original-metadata.txt"
  (cd "$POST_ATTEMPT_DIR" &&
    find . -printf '%P|%y|%m|%U|%G|%l\n' | LC_ALL=C sort) \
    > "$EVIDENCE_DIR/post-attempt-preserved-metadata.txt"
  cmp "$EVIDENCE_DIR/post-attempt-original-files.sha256" \
    "$EVIDENCE_DIR/post-attempt-preserved-files.sha256"
  cmp "$EVIDENCE_DIR/post-attempt-original-metadata.txt" \
    "$EVIDENCE_DIR/post-attempt-preserved-metadata.txt"

  set +e
  evidence_sync_rc=0
  for evidence_path in \
    "$EVIDENCE_DIR/post-attempt-original-files.sha256" \
    "$EVIDENCE_DIR/post-attempt-preserved-files.sha256" \
    "$EVIDENCE_DIR/post-attempt-original-metadata.txt" \
    "$EVIDENCE_DIR/post-attempt-preserved-metadata.txt"
  do
    sync -f "$evidence_path"
    one_sync_rc=$?
    if test "$one_sync_rc" -ne 0; then
      evidence_sync_rc="$one_sync_rc"
    fi
  done
  sync -f "$EVIDENCE_DIR"
  one_sync_rc=$?
  if test "$one_sync_rc" -ne 0; then
    evidence_sync_rc="$one_sync_rc"
  fi
  set -e
  printf 'evidence_sync_exit=%s\n' "$evidence_sync_rc" \
    >> "$EVIDENCE_DIR/post-attempt-preservation.exit"
  test "$evidence_sync_rc" -eq 0

  # The exit record is immutable after this point. The manifest is created only
  # after all of its fields have been finalized.
  sha256sum "$EVIDENCE_DIR/post-attempt-original-files.sha256" \
    "$EVIDENCE_DIR/post-attempt-preserved-files.sha256" \
    "$EVIDENCE_DIR/post-attempt-original-metadata.txt" \
    "$EVIDENCE_DIR/post-attempt-preserved-metadata.txt" \
    "$EVIDENCE_DIR/post-attempt-preservation.exit" \
    > "$EVIDENCE_DIR/post-attempt-preservation-manifests.sha256"

  set +e
  final_content_sync_rc=0
  for evidence_path in \
    "$EVIDENCE_DIR/post-attempt-original-files.sha256" \
    "$EVIDENCE_DIR/post-attempt-preserved-files.sha256" \
    "$EVIDENCE_DIR/post-attempt-original-metadata.txt" \
    "$EVIDENCE_DIR/post-attempt-preserved-metadata.txt" \
    "$EVIDENCE_DIR/post-attempt-preservation.exit" \
    "$EVIDENCE_DIR/post-attempt-preservation-manifests.sha256"
  do
    sync -f "$evidence_path"
    one_sync_rc=$?
    if test "$one_sync_rc" -ne 0; then
      final_content_sync_rc="$one_sync_rc"
    fi
  done
  for evidence_path in "$POST_ATTEMPT_DIR" "$EVIDENCE_DIR"; do
    sync -f "$evidence_path"
    one_sync_rc=$?
    if test "$one_sync_rc" -ne 0; then
      final_content_sync_rc="$one_sync_rc"
    fi
  done
  set -e
  test "$final_content_sync_rc" -eq 0

  set +e
  sha256sum -c "$EVIDENCE_DIR/post-attempt-preservation-manifests.sha256" \
    > "$EVIDENCE_DIR/post-attempt-manifest-check.txt" 2>&1
  manifest_check_rc=$?
  set -e
  printf 'final_content_sync_exit=%s\nmanifest_check_exit=%s\n' \
    "$final_content_sync_rc" "$manifest_check_rc" \
    > "$EVIDENCE_DIR/post-attempt-final-sync.exit"
  test "$manifest_check_rc" -eq 0

  set +e
  terminal_sync_rc=0
  for evidence_path in \
    "$EVIDENCE_DIR/post-attempt-manifest-check.txt" \
    "$EVIDENCE_DIR/post-attempt-final-sync.exit" \
    "$POST_ATTEMPT_DIR" "$EVIDENCE_DIR"
  do
    sync -f "$evidence_path"
    one_sync_rc=$?
    if test "$one_sync_rc" -ne 0; then
      terminal_sync_rc="$one_sync_rc"
    fi
  done
  set -e
  test "$terminal_sync_rc" -eq 0
)

failure_exit_cleanup() {
  local original_rc="$1"
  local exact_original=false
  local failure_stop_rc=
  local failure_status_rc=
  local post_attempt_rc=
  local listener_count=
  local failure_path_identity_rc=1
  local preservation_safe=false
  trap - EXIT INT TERM
  set +e +u
  set +x

  if test "$RECOVERY_FINISHED" != true && test "$original_rc" -eq 0; then
    original_rc=1
  fi
  if test "$RECOVERY_FINISHED" != true; then
    write_failure_decision_once unexpected-recovery-procedure-failure
  fi

  if test "$RECOVERY_FINISHED" != true &&
     test "$ORIGINAL_START_ATTEMPTED" = true; then
    assert_application_units_inactive failure-cleanup-before-stop
    if test "$ORIGINAL_STOP_ATTEMPTED" != true; then
      if test ! -e "$EVIDENCE_DIR/original-paths-failure-before-stop.txt"; then
        (set -e; assert_original_path_properties failure-cleanup-before-stop) \
          > "$EVIDENCE_DIR/original-paths-failure-before-stop.txt"
        failure_path_identity_rc=$?
      fi
      if test "$failure_path_identity_rc" -eq 0 &&
         assert_bound_original_postmaster; then
        exact_original=true
        ORIGINAL_STOP_ATTEMPTED=true
        if test ! -e "$EVIDENCE_DIR/failure-original-stop.stdout" &&
           test ! -e "$EVIDENCE_DIR/failure-original-stop.stderr" &&
           test ! -e "$EVIDENCE_DIR/failure-original-stop.exit"; then
          "$PG_BIN/pg_ctl" -D "$ORIG_PGDATA" -w -t 120 stop -m fast \
            > "$EVIDENCE_DIR/failure-original-stop.stdout" \
            2> "$EVIDENCE_DIR/failure-original-stop.stderr"
          failure_stop_rc=$?
          printf 'exit_code=%s\n' "$failure_stop_rc" \
            > "$EVIDENCE_DIR/failure-original-stop.exit"
          if test "$failure_stop_rc" -eq 0; then
            ORIGINAL_STOP_SUCCEEDED=true
          fi
        fi
      else
        write_collision_safe_evidence_best_effort \
          "$EVIDENCE_DIR/post-attempt-preservation-incident.txt" \
          'identity-ambiguity-or-unbound-start; no stop and no post-attempt copy'
      fi
    fi
    write_collision_safe_evidence_best_effort \
      "$EVIDENCE_DIR/failure-cleanup-identity.txt" \
      "original_exit=$original_rc exact_bound_original=$exact_original pid=${ORIGINAL_BOUND_PID:-none} start_epoch=${ORIGINAL_BOUND_START_EPOCH:-none} proc_start_ticks=${ORIGINAL_BOUND_PROC_START_TICKS:-none}"
    if test ! -e "$EVIDENCE_DIR/failure-pg-ctl-final.txt"; then
      "$PG_BIN/pg_ctl" -D "$ORIG_PGDATA" status \
        > "$EVIDENCE_DIR/failure-pg-ctl-final.txt" 2>&1
      failure_status_rc=$?
      printf 'exit_code=%s\n' "$failure_status_rc" \
        >> "$EVIDENCE_DIR/failure-pg-ctl-final.txt"
    fi
    if test ! -e "$EVIDENCE_DIR/failure-listeners-final.txt"; then
      ss -ltnH | awk -v p=":$ORIG_PORT" '$4 ~ p"$" {print}' \
        > "$EVIDENCE_DIR/failure-listeners-final.txt"
    fi
    if test ! -e "$EVIDENCE_DIR/failure-pg-controldata-final.txt"; then
      "$PG_BIN/pg_controldata" -D "$ORIG_PGDATA" \
        > "$EVIDENCE_DIR/failure-pg-controldata-final.txt" 2>&1
    fi
    listener_count="$(wc -l < "$EVIDENCE_DIR/failure-listeners-final.txt")"
    if test "$ORIGINAL_STOP_SUCCEEDED" = true &&
       test "$failure_status_rc" -eq 3 &&
       test "$listener_count" -eq 0 &&
       test ! -e "$ORIG_PGDATA/postmaster.pid"; then
      preservation_safe=true
    fi
    if test "$preservation_safe" = true; then
      preserve_post_attempt_pgdata
      post_attempt_rc=$?
      if test "$post_attempt_rc" -ne 0; then
        write_collision_safe_evidence_best_effort \
          "$EVIDENCE_DIR/post-attempt-preservation-incident.txt" \
          "safe-stop-confirmed-but-post-attempt-copy-failed exit=$post_attempt_rc"
      fi
    elif test "$exact_original" = true ||
         test "$ORIGINAL_STOP_ATTEMPTED" = true; then
      write_collision_safe_evidence_best_effort \
        "$EVIDENCE_DIR/post-attempt-preservation-incident.txt" \
        'stop-not-proven-safe; no post-attempt copy'
    fi
    assert_application_units_inactive failure-cleanup-final
  fi

  cleanup_secrets
  exit "$original_rc"
}

trap 'failure_exit_cleanup $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
assert_approval_current
ORIGINAL_START_ATTEMPTED=true
assert_application_units_inactive immediately-before-original-start
assert_original_path_properties immediately-before-original-start \
  > "$EVIDENCE_DIR/original-paths-before-start.txt"
assert_no_external_recovery_configuration pre-start-recheck \
  > "$EVIDENCE_DIR/original-recovery-settings-pre-start-recheck.txt"
(cd "$ORIG_LOG_DIR" &&
  find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) \
  > "$EVIDENCE_DIR/pre-start-log-source-recheck.sha256"
(cd "$ORIG_LOG_DIR" &&
  find . -printf '%P|%y|%m|%U|%G|%l\n' | LC_ALL=C sort) \
  > "$EVIDENCE_DIR/pre-start-log-source-recheck-metadata.txt"
cmp "$EVIDENCE_DIR/pre-start-log-original-files.sha256" \
  "$EVIDENCE_DIR/pre-start-log-source-recheck.sha256"
cmp "$EVIDENCE_DIR/pre-start-log-original-metadata.txt" \
  "$EVIDENCE_DIR/pre-start-log-source-recheck-metadata.txt"
log_bytes_before="$(stat -c '%s' "$ORIG_LOG")"
assert_no_external_recovery_configuration immediately-before-original-start \
  > "$EVIDENCE_DIR/original-recovery-settings-before-start.txt"
assert_effective_safe_maintenance_start_configuration \
  immediately-before-original-start \
  > "$EVIDENCE_DIR/original-safe-start-settings-before-start.txt"

set +e
"$PG_BIN/pg_ctl" -D "$ORIG_PGDATA" -l "$ORIG_LOG" \
  -o "$ORIGINAL_SAFE_START_OPTIONS" -w -t 120 start \
  > "$EVIDENCE_DIR/original-start.stdout" \
  2> "$EVIDENCE_DIR/original-start.stderr"
start_rc=$?
set -e
printf 'exit_code=%s\n' "$start_rc" > "$EVIDENCE_DIR/original-start.exit"
test "$start_rc" -eq 0

mapfile -t pid_lines < "$ORIG_PGDATA/postmaster.pid"
test "${#pid_lines[@]}" -ge 6
postmaster_pid="${pid_lines[0]}"
postmaster_start_epoch="${pid_lines[2]}"
[[ "$postmaster_pid" =~ ^[1-9][0-9]*$ ]]
[[ "$postmaster_start_epoch" =~ ^[1-9][0-9]*$ ]]
kill -0 "$postmaster_pid"
test "${pid_lines[1]}" = "$ORIG_PGDATA"
test "${pid_lines[3]}" = "$ORIG_PORT"
test "${pid_lines[4]}" = "$ORIG_SOCKET"
test "$(readlink -e -- "/proc/$postmaster_pid/cwd")" = "$ORIG_PGDATA"
test "$(readlink -e -- "/proc/$postmaster_pid/exe")" = "$PG_BIN/postgres"
postmaster_proc_start_ticks="$(awk '{print $22}' "/proc/$postmaster_pid/stat")"
[[ "$postmaster_proc_start_ticks" =~ ^[1-9][0-9]*$ ]]

mapfile -d '' -t postmaster_argv < "/proc/$postmaster_pid/cmdline"
found_data_arg=false
for ((i=0; i<${#postmaster_argv[@]}-1; i++)); do
  if test "${postmaster_argv[i]}" = -D &&
     test "${postmaster_argv[i+1]}" = "$ORIG_PGDATA"; then
    found_data_arg=true
  fi
done
test "$found_data_arg" = true
unset postmaster_argv found_data_arg i

ORIGINAL_BOUND_PID="$postmaster_pid"
ORIGINAL_BOUND_START_EPOCH="$postmaster_start_epoch"
ORIGINAL_BOUND_PROC_START_TICKS="$postmaster_proc_start_ticks"
ORIGINAL_IDENTITY_BOUND=true
assert_bound_original_postmaster
write_collision_safe_evidence_best_effort \
  "$EVIDENCE_DIR/original-postmaster-bound.txt" \
  "pid=$ORIGINAL_BOUND_PID start_epoch=$ORIGINAL_BOUND_START_EPOCH proc_start_ticks=$ORIGINAL_BOUND_PROC_START_TICKS executable=$PG_BIN/postgres cwd=$ORIG_PGDATA port=$ORIG_PORT socket=$ORIG_SOCKET"
test "$(readlink -e -- "/proc/$ORIGINAL_BOUND_PID/fd/1")" = "$ORIG_LOG"
test "$(readlink -e -- "/proc/$ORIGINAL_BOUND_PID/fd/2")" = "$ORIG_LOG"
test "$(stat -Lc '%d:%i' "/proc/$ORIGINAL_BOUND_PID/fd/1")" \
  = "$ORIG_LOG_DEVICE_INODE"
test "$(stat -Lc '%d:%i' "/proc/$ORIGINAL_BOUND_PID/fd/2")" \
  = "$ORIG_LOG_DEVICE_INODE"
printf 'policy_id=%s\nstdout=%s\nstderr=%s\ndevice_inode=%s\n' \
  "${PLAN[ORIGINAL_START_POLICY_ID]}" "$ORIG_LOG" "$ORIG_LOG" \
  "$ORIG_LOG_DEVICE_INODE" > "$EVIDENCE_DIR/original-log-binding.txt"

"$PG_BIN/pg_ctl" -D "$ORIG_PGDATA" status \
  > "$EVIDENCE_DIR/pg-ctl-running.txt" 2>&1

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/original-running-settings.txt" <<'SQL'
BEGIN READ ONLY;
SELECT name || E'\t' || setting || E'\t' || source
FROM pg_settings
WHERE name IN (
  'archive_mode','archive_command','archive_library','restore_command',
  'archive_cleanup_command','recovery_end_command','primary_conninfo',
  'shared_preload_libraries','session_preload_libraries',
  'local_preload_libraries','ssl_passphrase_command','external_pid_file',
  'max_logical_replication_workers','max_wal_senders','autovacuum',
  'logging_collector','log_destination','log_min_messages','ssl',
  'listen_addresses','unix_socket_directories','port','jit','search_path'
)
ORDER BY name COLLATE "C";
COMMIT;
SQL
orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/original-running-settings-gate.txt" <<SQL
BEGIN READ ONLY;
WITH expected(name, expected_setting, allow_disabled, expected_source) AS (
  VALUES
    ('archive_mode','off',false,'command line'),
    ('archive_command','',true,'command line'),
    ('archive_library','',true,'command line'),
    ('restore_command','',false,'command line'),
    ('archive_cleanup_command','',false,'command line'),
    ('recovery_end_command','',false,'command line'),
    ('primary_conninfo','',false,'command line'),
    ('shared_preload_libraries','',false,'command line'),
    ('session_preload_libraries','',false,'client'),
    ('local_preload_libraries','',false,'client'),
    ('ssl_passphrase_command','',false,'command line'),
    ('external_pid_file','',false,'command line'),
    ('max_logical_replication_workers','0',false,'command line'),
    ('max_wal_senders','0',false,'command line'),
    ('autovacuum','off',false,'command line'),
    ('logging_collector','off',false,'command line'),
    ('log_destination','stderr',false,'command line'),
    ('log_min_messages','log',false,'command line'),
    ('ssl','off',false,'command line'),
    ('listen_addresses','127.0.0.1',false,'command line'),
    ('unix_socket_directories','$ORIG_SOCKET',false,'command line'),
    ('port','$ORIG_PORT',false,'command line'),
    ('jit','off',false,'client'),
    ('search_path','pg_catalog,public',false,'client')
)
SELECT count(*) = 24 AND bool_and(
         (s.setting=e.expected_setting OR
          (e.allow_disabled AND s.setting='(disabled)')) AND
         s.source=e.expected_source AND
         CASE e.name
           WHEN 'session_preload_libraries' THEN s.reset_val=''
           WHEN 'local_preload_libraries' THEN s.reset_val=''
           WHEN 'jit' THEN s.reset_val='off'
           WHEN 'search_path' THEN s.reset_val='pg_catalog,public'
           ELSE true
         END
       )
FROM expected e
JOIN pg_settings s USING(name);
COMMIT;
SQL
test "$(tail -n1 "$EVIDENCE_DIR/original-running-settings-gate.txt")" = t

head_value="$(orig_admin_psql -v ON_ERROR_STOP=1 -Atc \
  'SELECT version_num FROM public.alembic_version')"
case "$head_value" in
  0003_contract_lineage_repair) SOURCE_SCHEMA_STATE=0003 ;;
  0004_durable_research_jobs) SOURCE_SCHEMA_STATE=0004 ;;
  *) exit 1 ;;
esac
capture_side_effect_object_mismatches orig_admin_psql "$SOURCE_SCHEMA_STATE" \
  "$EVIDENCE_DIR/source-side-effect-mismatches-before-any-nonadmin.txt"
test ! -s "$EVIDENCE_DIR/source-side-effect-mismatches-before-any-nonadmin.txt"

run_libpq_client unprivileged "$READER_PGPASS" recovery-orig-ready \
  "$PG_BIN/pg_isready" --host "$ORIG_HOST" --port "$ORIG_PORT" \
  --username trading_reader --dbname "$ORIG_DB" \
  > "$EVIDENCE_DIR/original-pg-isready.txt"

listeners="$(ss -ltnH | awk -v p=":$ORIG_PORT" '$4 ~ p"$" {print $4}')"
test -n "$listeners"
test -z "$(printf '%s\n' "$listeners" | awk -v p="$ORIG_PORT" \
  '$0 != "127.0.0.1:"p {print}')"
assert_original_path_properties immediately-after-original-start \
  > "$EVIDENCE_DIR/original-paths-after-start.txt"

log_bytes_after="$(stat -c '%s' "$ORIG_LOG")"
test "$log_bytes_after" -ge "$log_bytes_before"
tail -c "+$((log_bytes_before + 1))" "$ORIG_LOG" \
  > "$EVIDENCE_DIR/recovery-log-delta.txt"

test "${PLAN[RECOVERY_LOG_POLICY_ID]}" = PG16_INTERRUPTED_RECOVERY_V1
test -s "$EVIDENCE_DIR/recovery-log-delta.txt"
required_recovery_patterns=(
  'database system (was interrupted|was not properly shut down)'
  '(automatic recovery in progress|redo (starts at|is not required))'
  '(redo done at|redo is not required|consistent recovery state reached)'
  'database system is ready to accept connections'
)
allowed_recovery_pattern=
allowed_recovery_evidence="$EVIDENCE_DIR/recovery-log-allowed-evidence.txt"
: > "$allowed_recovery_evidence"
printf 'policy_id=%s\n' "${PLAN[RECOVERY_LOG_POLICY_ID]}" \
  > "$EVIDENCE_DIR/recovery-log-policy.txt"
for allowed_recovery_pattern in "${required_recovery_patterns[@]}"; do
  printf 'required_regex=%s\n' "$allowed_recovery_pattern" \
    >> "$EVIDENCE_DIR/recovery-log-policy.txt"
  grep -Ein -- "$allowed_recovery_pattern" \
    "$EVIDENCE_DIR/recovery-log-delta.txt" >> "$allowed_recovery_evidence"
done

known_recovery_pattern='(database system (was interrupted|was not properly shut down|is ready to accept connections)|automatic recovery in progress|redo (starts at|done at|is not required)|consistent recovery state reached|invalid record length at .+: wanted [0-9]+, got 0|checkpoint (starting|complete))'
printf 'allowed_regex=%s\n' "$known_recovery_pattern" \
  >> "$EVIDENCE_DIR/recovery-log-policy.txt"
grep -Ein -- "$known_recovery_pattern" "$EVIDENCE_DIR/recovery-log-delta.txt" \
  >> "$allowed_recovery_evidence" || true
test -s "$allowed_recovery_evidence"

denied_recovery_pattern='((PANIC|FATAL|ERROR|WARNING):|could not locate a valid checkpoint|invalid (primary|secondary) checkpoint record|could not find redo location|WAL ends before end of online backup|requested WAL segment .* has already been removed|record with incorrect prev-link|unexpected pageaddr|invalid resource manager ID|incorrect resource manager data checksum|invalid magic number|invalid xl_info|database system was interrupted while in recovery|recovery is paused|recovery stopping before|selected new timeline|new timeline|could not open file .*pg_wal|input/output error|I/O error|permission denied|read-only file system|no space left|checksum mismatch|page verification failed|could not read block|could not read from log segment)'
printf 'denied_regex=%s\n' "$denied_recovery_pattern" \
  >> "$EVIDENCE_DIR/recovery-log-policy.txt"
grep -Ein -- "$denied_recovery_pattern" "$EVIDENCE_DIR/recovery-log-delta.txt" \
  > "$EVIDENCE_DIR/recovery-log-denied-evidence.txt" || true
test ! -s "$EVIDENCE_DIR/recovery-log-denied-evidence.txt"

grep -Ein -- 'invalid record length' "$EVIDENCE_DIR/recovery-log-delta.txt" \
  > "$EVIDENCE_DIR/recovery-log-invalid-record-observed.txt" || true
grep -Eiv -- 'invalid record length at .+: wanted [0-9]+, got 0' \
  "$EVIDENCE_DIR/recovery-log-invalid-record-observed.txt" \
  > "$EVIDENCE_DIR/recovery-log-invalid-record-denied.txt" || true
test ! -s "$EVIDENCE_DIR/recovery-log-invalid-record-denied.txt"
unset required_recovery_patterns allowed_recovery_pattern \
  allowed_recovery_evidence known_recovery_pattern denied_recovery_pattern
assert_application_units_inactive after-original-start
~~~

The exact policy identifier is part of the dual-approved transcript, while the
approved runbook hash binds its required, allowed, and denied regular
expressions. The mechanical gate records matches and runs before any backup or
migration. Any missing required recovery milestone, denied severity or recovery
pattern, or nonterminal invalid-record-length message stops the run without
retry; prose-only operator judgment cannot pass this gate. The separate start
policy is checked offline, rechecked immediately before start with the exact
command-line overrides, and proved again from command-line-sourced
`pg_settings`. With the logging collector disabled, stderr as the sole
destination, and both postmaster output descriptors bound to the reviewed log
inode, the byte delta is authoritative for this controlled start.

### 5.8 Repeated head, identity, table, and count gates as reader

Every invocation is explicitly original host/port/user/database.

~~~bash
assert_original_identity_and_head() {
  local phase="$1"
  orig_reader_psql -v ON_ERROR_STOP=1 -At \
    > "$EVIDENCE_DIR/head-$phase.txt" <<'SQL'
BEGIN READ ONLY;
SELECT current_setting('server_version_num');
SELECT current_setting('data_directory');
SELECT current_setting('port');
SELECT current_setting('listen_addresses');
SELECT current_database();
SELECT current_user;
SELECT current_setting('default_transaction_read_only');
SELECT count(*) || '|' || min(version_num) || '|' || max(version_num)
FROM alembic_version;
COMMIT;
SQL
  test "$(sed -n '1p' "$EVIDENCE_DIR/head-$phase.txt" | cut -c1-2)" = 16
  test "$(sed -n '2p' "$EVIDENCE_DIR/head-$phase.txt")" = "$ORIG_PGDATA"
  test "$(sed -n '3p' "$EVIDENCE_DIR/head-$phase.txt")" = "$ORIG_PORT"
  test "$(sed -n '4p' "$EVIDENCE_DIR/head-$phase.txt")" = 127.0.0.1
  test "$(sed -n '5p' "$EVIDENCE_DIR/head-$phase.txt")" = "$ORIG_DB"
  test "$(sed -n '6p' "$EVIDENCE_DIR/head-$phase.txt")" = trading_reader
  test "$(sed -n '7p' "$EVIDENCE_DIR/head-$phase.txt")" = on
  head_line="$(sed -n '8p' "$EVIDENCE_DIR/head-$phase.txt")"
  case "$head_line" in
    "1|0003_contract_lineage_repair|0003_contract_lineage_repair"|\
    "1|0004_durable_research_jobs|0004_durable_research_jobs") ;;
    *) return 1 ;;
  esac
}
assert_original_identity_and_head after-recovery

orig_reader_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/base-count-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH expected(name, n) AS (
  VALUES
    ('assets',17::bigint), ('market_reports',2186),
    ('market_asset_snapshots',23961), ('decisions',16517),
    ('signals',344), ('capability_evidence',9), ('cost_summaries',1),
    ('cost_sessions',20), ('migration_errors',222),
    ('decision_field_lineage',33034), ('cost_session_assets',200),
    ('asset_source_lineage',41039), ('phase3b_backfill_runs',4),
    ('phase3b_backfill_events',0)
), actual(name, n) AS (
  SELECT 'assets',count(*) FROM assets UNION ALL
  SELECT 'market_reports',count(*) FROM market_reports UNION ALL
  SELECT 'market_asset_snapshots',count(*) FROM market_asset_snapshots UNION ALL
  SELECT 'decisions',count(*) FROM decisions UNION ALL
  SELECT 'signals',count(*) FROM signals UNION ALL
  SELECT 'capability_evidence',count(*) FROM capability_evidence UNION ALL
  SELECT 'cost_summaries',count(*) FROM cost_summaries UNION ALL
  SELECT 'cost_sessions',count(*) FROM cost_sessions UNION ALL
  SELECT 'migration_errors',count(*) FROM migration_errors UNION ALL
  SELECT 'decision_field_lineage',count(*) FROM decision_field_lineage UNION ALL
  SELECT 'cost_session_assets',count(*) FROM cost_session_assets UNION ALL
  SELECT 'asset_source_lineage',count(*) FROM asset_source_lineage UNION ALL
  SELECT 'phase3b_backfill_runs',count(*) FROM phase3b_backfill_runs UNION ALL
  SELECT 'phase3b_backfill_events',count(*) FROM phase3b_backfill_events
)
SELECT expected.name || '|expected=' || expected.n || '|actual=' || actual.n
FROM expected JOIN actual USING (name)
WHERE expected.n <> actual.n ORDER BY expected.name;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/base-count-mismatches.txt"

canonical_total="$(orig_reader_psql -v ON_ERROR_STOP=1 -Atc "
  BEGIN READ ONLY;
  SELECT
    (SELECT count(*) FROM assets) +
    (SELECT count(*) FROM market_reports) +
    (SELECT count(*) FROM market_asset_snapshots) +
    (SELECT count(*) FROM decisions) +
    (SELECT count(*) FROM signals) +
    (SELECT count(*) FROM capability_evidence) +
    (SELECT count(*) FROM cost_summaries) +
    (SELECT count(*) FROM cost_sessions);
  COMMIT;")"
test "$canonical_total" = 43055
~~~

Capture migration/audit tracking-table counts even when no exact baseline
exists; unexpected changes are preserved and reviewed, never deleted.

### 5.9 Immediate recovered-state backup with explicit original target

Reject both final and .partial names, repeat head immediately before/after, and
retain ACLs.

~~~bash
durably_finalize_dump() {
  local dump_path="$1"
  local evidence_label="$2"
  local catalog_path="$3"
  local stat_path="$EVIDENCE_DIR/$evidence_label.stat"
  local hash_path="$EVIDENCE_DIR/$evidence_label.sha256"
  local sync_path="$EVIDENCE_DIR/$evidence_label-sync.exit"
  local dump_sync_rc=
  local backup_dir_sync_rc=
  local catalog_sync_rc=
  local stat_sync_rc=
  local hash_sync_rc=
  local sync_evidence_rc=
  local evidence_dir_sync_rc=

  case "$evidence_label" in
    recovered-dump|final-0004) ;;
    *) return 1 ;;
  esac
  test -f "$dump_path"
  test ! -L "$dump_path"
  test "$(realpath -e -- "$dump_path")" = "$dump_path"
  test "$(stat -c '%U' "$dump_path")" = thenam176
  test "$(stat -c '%a' "$dump_path")" = 600
  test "$(stat -c '%h' "$dump_path")" = 1
  test -s "$catalog_path"

  set +e
  sync -f "$dump_path"
  dump_sync_rc=$?
  sync -f "$BACKUP_DIR"
  backup_dir_sync_rc=$?
  set -e
  printf 'dump_sync_exit=%s\nbackup_dir_sync_exit=%s\n' \
    "$dump_sync_rc" "$backup_dir_sync_rc" > "$sync_path"
  test "$dump_sync_rc" -eq 0
  test "$backup_dir_sync_rc" -eq 0

  stat -c 'path=%n owner=%U mode=%a links=%h size=%s mtime=%y' "$dump_path" \
    > "$stat_path"
  sha256sum "$dump_path" > "$hash_path"

  set +e
  sync -f "$catalog_path"
  catalog_sync_rc=$?
  sync -f "$stat_path"
  stat_sync_rc=$?
  sync -f "$hash_path"
  hash_sync_rc=$?
  sync -f "$sync_path"
  sync_evidence_rc=$?
  sync -f "$EVIDENCE_DIR"
  evidence_dir_sync_rc=$?
  set -e
  printf 'catalog_sync_exit=%s\nstat_sync_exit=%s\nhash_sync_exit=%s\n' \
    "$catalog_sync_rc" "$stat_sync_rc" "$hash_sync_rc" >> "$sync_path"
  printf 'initial_sync_evidence_exit=%s\nevidence_dir_sync_exit=%s\n' \
    "$sync_evidence_rc" "$evidence_dir_sync_rc" >> "$sync_path"
  test "$catalog_sync_rc" -eq 0
  test "$stat_sync_rc" -eq 0
  test "$hash_sync_rc" -eq 0
  test "$sync_evidence_rc" -eq 0
  test "$evidence_dir_sync_rc" -eq 0
  sync -f "$sync_path"
  sync -f "$EVIDENCE_DIR"
}

test "$(orig_admin_psql -v ON_ERROR_STOP=1 -Atc \
  'SELECT version_num FROM public.alembic_version')" = "$head_value"
assert_original_identity_and_head before-recovered-dump
capture_side_effect_object_mismatches orig_admin_psql "$SOURCE_SCHEMA_STATE" \
  "$EVIDENCE_DIR/source-side-effect-mismatches-before-recovered-dump.txt"
test ! -s "$EVIDENCE_DIR/source-side-effect-mismatches-before-recovered-dump.txt"

RECOVERED_DUMP="$BACKUP_DIR/trading_agent-$RUN_ID-$head_value-recovered.dump"
test ! -e "$RECOVERED_DUMP"
test ! -L "$RECOVERED_DUMP"
test ! -e "$RECOVERED_DUMP.partial"
test ! -L "$RECOVERED_DUMP.partial"

run_libpq_client unprivileged "$OWNER_PGPASS" recovery-orig-dump \
  "$PG_BIN/pg_dump" \
  --host "$ORIG_HOST" --port "$ORIG_PORT" \
  --username trading_owner --dbname "$ORIG_DB" --no-password \
  --format=custom --serializable-deferrable \
  --no-publications --no-subscriptions \
  --file "$RECOVERED_DUMP.partial"
run_postgres_client_no_connection "$PG_BIN/pg_restore" \
  --list "$RECOVERED_DUMP.partial" \
  > "$EVIDENCE_DIR/recovered-dump.catalog"
assert_archive_toc_side_effect_free "$EVIDENCE_DIR/recovered-dump.catalog" \
  "$SOURCE_SCHEMA_STATE" "$EVIDENCE_DIR/recovered-dump-forbidden-toc.txt"
chmod 0600 "$RECOVERED_DUMP.partial"
mv "$RECOVERED_DUMP.partial" "$RECOVERED_DUMP"
durably_finalize_dump "$RECOVERED_DUMP" recovered-dump \
  "$EVIDENCE_DIR/recovered-dump.catalog"
assert_original_identity_and_head after-recovered-dump
~~~

### 5.10 Explicit 0003/0004 branch and scoped Alembic

At 0004, never invoke Alembic.

At 0003, no migration runs unless the exact dual-reviewed transcript says
ALLOW_MIGRATE_ORIGINAL_IF_0003=YES. An unapproved migration means this procedure
must not begin.

Before Alembic, the entire source tree is clean and bound to the approved
commit/tree/migration hash. The source-bound `DatabaseSettings.sqlalchemy_url()`
is proved to add only `statement_timeout=5000`; it therefore cannot be made to
inherit the wrapper `PGOPTIONS`. Safe preload, JIT, and `search_path` reset
values instead come from the reviewed postmaster command line, and the
side-effect gate proves zero database/role settings immediately before the
migration. Alembic receives `TRADING_DATABASE_*` only inside one scoped clean
environment. Its password crosses an inherited descriptor, never an external
process argument. Raw stdout/stderr remain in the secret directory, are
screened quietly for raw, conventional URL-encoded, SQLAlchemy 2.0.51's exact
`quote(password, safe=" +")`, and nested password forms, and are deleted. Only
exit status, clear-scan metadata, byte counts, and the separately repeated
database head enter evidence. No ambient PostgreSQL target is used.

~~~bash
if test "$head_value" = 0003_contract_lineage_repair; then
  test "${PLAN[ALLOW_MIGRATE_ORIGINAL_IF_0003]}" = YES
  assert_approval_current
  printf '%s\n' ALLOW_MIGRATE_ORIGINAL_IF_0003=YES \
    > "$EVIDENCE_DIR/approved-0003-to-0004-branch.txt"

  test "$(git -C "$REPO" rev-parse HEAD)" = "${PLAN[SOURCE_COMMIT]}"
  test "$(git -C "$REPO" rev-parse 'HEAD^{tree}')" = "${PLAN[SOURCE_TREE]}"
  test -z "$(git -C "$REPO" status --porcelain=v1 --untracked-files=all)"
  test "$(awk '
    /^\[\[package\]\]$/ {in_package=0}
    /^name = "sqlalchemy"$/ {in_package=1; package_count++; next}
    in_package && /^version = "2\.0\.51"$/ {version_count++}
    END {printf "%d|%d\n", package_count+0, version_count+0}
  ' "$REPO/uv.lock")" = '1|1'
  test "$(sha256sum "$REPO/alembic/versions/0004_durable_research_jobs.py" |
    awk '{print $1}')" = "${PLAN[MIGRATION_SHA256]}"
  database_settings_source="$REPO/apps/control_api/trading_control/db.py"
  test -f "$database_settings_source"
  test ! -L "$database_settings_source"
  test "$(grep -F -c \
    'query={"options": f"-c statement_timeout={self.statement_timeout_ms}"}' \
    "$database_settings_source")" -eq 1
  sha256sum "$database_settings_source" \
    > "$EVIDENCE_DIR/alembic-database-settings-source.sha256"
  sha256sum "$REPO/uv.lock" > "$EVIDENCE_DIR/alembic-uv-lock.sha256"
  printf '%s\n' \
    'sqlalchemy_url_options=statement_timeout_only' \
    'sqlalchemy_version=2.0.51' \
    'sqlalchemy_password_quote_safe=space-plus' \
    'safe_preload_jit_search_path_source=postmaster_command_line' \
    'pg_db_role_setting=must_remain_empty' \
    > "$EVIDENCE_DIR/alembic-connection-policy.txt"

  parse_db_env "$OWNER_ENV" "$ORIG_HOST" "$ORIG_PORT" "$ORIG_DB" trading_owner
  uv_bin="$(command -v uv)"
  test -x "$uv_bin"
  test ! -L "$uv_bin"
  test "$(realpath -e -- "$uv_bin")" = "$uv_bin"
  bash_bin="$(command -v bash)"
  test -x "$bash_bin"
  test ! -L "$bash_bin"
  test "$(realpath -e -- "$bash_bin")" = "$bash_bin"
  orig_admin_psql -v ON_ERROR_STOP=1 -At \
    > "$EVIDENCE_DIR/alembic-server-defaults-gate.txt" <<'SQL'
BEGIN READ ONLY;
WITH expected(name, reset_value) AS (
  VALUES ('session_preload_libraries',''),('local_preload_libraries',''),
         ('jit','off'),('search_path','pg_catalog,public')
)
SELECT count(*)=4 AND bool_and(settings.reset_val=expected.reset_value)
FROM expected JOIN pg_settings settings USING(name);
COMMIT;
SQL
  test "$(tail -n1 "$EVIDENCE_DIR/alembic-server-defaults-gate.txt")" = t
  capture_side_effect_object_mismatches orig_admin_psql 0003 \
    "$EVIDENCE_DIR/source-side-effect-mismatches-before-alembic.txt"
  test ! -s "$EVIDENCE_DIR/source-side-effect-mismatches-before-alembic.txt"
  for alembic_secret_path in \
    "$ALEMBIC_RAW_STDOUT" "$ALEMBIC_RAW_STDERR" "$ALEMBIC_SECRET_PATTERNS"
  do
    test ! -e "$alembic_secret_path"
    test ! -L "$alembic_secret_path"
  done
  write_alembic_secret_patterns "$ALEMBIC_SECRET_PATTERNS" "$DB_PASSWORD"
  : > "$ALEMBIC_RAW_STDOUT"
  : > "$ALEMBIC_RAW_STDERR"
  chmod 0600 "$ALEMBIC_RAW_STDOUT" "$ALEMBIC_RAW_STDERR"
  for alembic_secret_path in \
    "$ALEMBIC_RAW_STDOUT" "$ALEMBIC_RAW_STDERR" "$ALEMBIC_SECRET_PATTERNS"
  do
    test -f "$alembic_secret_path"
    test ! -L "$alembic_secret_path"
    test "$(stat -c '%a' "$alembic_secret_path")" = 600
    test "$(stat -c '%h' "$alembic_secret_path")" = 1
  done
  set +e
  (
    set +x
    assert_no_ambient_postgres_environment
    cd "$REPO"
    # The clean child Bash, not this shell, expands the quoted names below.
    # shellcheck disable=SC2016
    env -i LC_ALL=C HOME=/home/thenam176 \
      PATH="$(dirname -- "$uv_bin"):/usr/bin:/bin" \
      PGAPPNAME=recovery-alembic-0004 \
      PGCONNECT_TIMEOUT=10 \
      PGSSLMODE=disable \
      PGTARGETSESSIONATTRS=any \
      TRADING_DATABASE_HOST="$DB_HOST" \
      TRADING_DATABASE_PORT="$DB_PORT" \
      TRADING_DATABASE_NAME="$DB_NAME" \
      TRADING_DATABASE_USER="$DB_USER" \
      TRADING_DB_STATEMENT_TIMEOUT_MS=5000 \
      "$bash_bin" -c '
        set -euo pipefail
        IFS= read -r TRADING_DATABASE_PASSWORD <&3
        exec 3<&-
        test -n "$TRADING_DATABASE_PASSWORD"
        export TRADING_DATABASE_PASSWORD
        exec "$@"
      ' recovery-alembic-env \
      "$uv_bin" run --frozen --offline alembic \
      upgrade 0004_durable_research_jobs 3<<< "$DB_PASSWORD"
  ) > "$ALEMBIC_RAW_STDOUT" 2> "$ALEMBIC_RAW_STDERR"
  alembic_rc=$?
  set -e
  printf 'exit_code=%s\n' "$alembic_rc" > "$EVIDENCE_DIR/alembic-0004.exit"

  set +e
  LC_ALL=C grep -Fq -f "$ALEMBIC_SECRET_PATTERNS" "$ALEMBIC_RAW_STDOUT"
  alembic_stdout_scan_rc=$?
  LC_ALL=C grep -Fq -f "$ALEMBIC_SECRET_PATTERNS" "$ALEMBIC_RAW_STDERR"
  alembic_stderr_scan_rc=$?
  set -e
  printf '%s\n' \
    'patterns_raw=raw' \
    'patterns_url=percent-upper,percent-lower,quote-plus-upper,quote-plus-lower' \
    'patterns_sqlalchemy=safe-space-plus-upper,safe-space-plus-lower' \
    'patterns_nested=double-percent-upper,double-percent-lower,nested-safe-space-plus-upper,nested-safe-space-plus-lower' \
    "stdout_secret_scan_exit=$alembic_stdout_scan_rc" \
    "stderr_secret_scan_exit=$alembic_stderr_scan_rc" \
    'grep_no_match_exit=1' \
    'raw_content_promoted=false' \
    > "$EVIDENCE_DIR/alembic-output-screen.txt"
  test "$alembic_stdout_scan_rc" -eq 1
  test "$alembic_stderr_scan_rc" -eq 1

  alembic_stdout_bytes="$(stat -c '%s' "$ALEMBIC_RAW_STDOUT")"
  alembic_stderr_bytes="$(stat -c '%s' "$ALEMBIC_RAW_STDERR")"
  rm -f -- "$ALEMBIC_RAW_STDOUT" "$ALEMBIC_RAW_STDERR" \
    "$ALEMBIC_SECRET_PATTERNS"
  test ! -e "$ALEMBIC_RAW_STDOUT"
  test ! -e "$ALEMBIC_RAW_STDERR"
  test ! -e "$ALEMBIC_SECRET_PATTERNS"
  printf 'stdout_bytes=%s\nstderr_bytes=%s\n' \
    "$alembic_stdout_bytes" "$alembic_stderr_bytes" \
    >> "$EVIDENCE_DIR/alembic-output-screen.txt"
  printf 'raw_files_deleted=true\n' \
    >> "$EVIDENCE_DIR/alembic-output-screen.txt"
  DB_HOST='' DB_PORT='' DB_NAME='' DB_USER='' DB_PASSWORD=''

  set +e
  orig_admin_psql -v ON_ERROR_STOP=1 -At \
    > "$EVIDENCE_DIR/alembic-post-attempt-head.txt" \
    2> "$EVIDENCE_DIR/alembic-post-attempt-head.stderr" <<'SQL'
BEGIN READ ONLY;
SELECT count(*) || '|' || coalesce(min(version_num),'NULL') || '|' ||
       coalesce(max(version_num),'NULL')
FROM alembic_version;
COMMIT;
SQL
  alembic_head_rc=$?
  set -e
  printf 'exit_code=%s\n' "$alembic_head_rc" \
    > "$EVIDENCE_DIR/alembic-post-attempt-head.exit"
  test "$alembic_head_rc" -eq 0
  post_alembic_head="$(tail -n1 \
    "$EVIDENCE_DIR/alembic-post-attempt-head.txt")"
  case "$post_alembic_head" in
    1\|0003_contract_lineage_repair\|0003_contract_lineage_repair)
      POST_ALEMBIC_SCHEMA_STATE=0003 ;;
    1\|0004_durable_research_jobs\|0004_durable_research_jobs)
      POST_ALEMBIC_SCHEMA_STATE=0004 ;;
    *) exit 1 ;;
  esac
  capture_side_effect_object_mismatches orig_admin_psql \
    "$POST_ALEMBIC_SCHEMA_STATE" \
    "$EVIDENCE_DIR/source-side-effect-mismatches-after-alembic-attempt.txt"
  test ! -s \
    "$EVIDENCE_DIR/source-side-effect-mismatches-after-alembic-attempt.txt"
  test "$alembic_rc" -eq 0
  test "$POST_ALEMBIC_SCHEMA_STATE" = 0004
  assert_original_identity_and_head after-0004
  test "$(tail -n1 "$EVIDENCE_DIR/head-after-0004.txt")" \
    = "1|0004_durable_research_jobs|0004_durable_research_jobs"
else
  assert_original_identity_and_head already-0004
fi
SOURCE_SCHEMA_STATE=0004
readonly SOURCE_SCHEMA_STATE
~~~

Failure stops the run. Never retry, downgrade, edit an existing revision, or
edit alembic_version.

### 5.11 Exact 0004 tables, zero rows, constraints, indexes, trigger, and orphans

First capture the post-0004 logical backup. This occurs before the expected ACL
NO-GO so a successful migration is never left with only its pre-migration
dump. Then run as explicit original reader unless the step states admin.

~~~bash
capture_complete_catalog_snapshot() {
  local psql_wrapper="$1"
  local output="$2"
  "$psql_wrapper" -v ON_ERROR_STOP=1 -At > "$output" <<'SQL'
BEGIN READ ONLY;
SET LOCAL search_path = public, pg_catalog;
WITH catalog(line) AS (
  SELECT 'snapshot|query_id=PG16_COMPLETE_RELATION_CATALOG_V2|pg_major=' ||
         (current_setting('server_version_num')::integer / 10000)::text
  UNION ALL
  SELECT 'relation|' || c.relname || '|kind=' || c.relkind ||
         '|relam=' || coalesce(am.amname,'') ||
         '|reltablespace=' || CASE WHEN c.reltablespace=0
           THEN 'database-default:' || database_tablespace.spcname
           ELSE 'explicit:' || relation_tablespace.spcname END ||
         '|persistence=' || c.relpersistence ||
         '|rowsecurity=' || c.relrowsecurity ||
         '|forcerowsecurity=' || c.relforcerowsecurity ||
         '|replica_identity=' || c.relreplident ||
         '|is_partition=' || c.relispartition ||
         '|options=' || coalesce((
           SELECT string_agg(option_value,',' ORDER BY option_value)
           FROM unnest(c.reloptions) AS options(option_value)
         ),'') ||
         '|partition_key=' || coalesce(btrim(regexp_replace(
           pg_get_partkeydef(c.oid),'[[:space:]]+',' ','g')),'') ||
         '|partition_bound=' || coalesce(btrim(regexp_replace(
           pg_get_expr(c.relpartbound,c.oid,false),'[[:space:]]+',' ','g')),'') ||
         '|parents=' || coalesce((
           SELECT string_agg(parent_ns.nspname || '.' || parent.relname,','
                             ORDER BY parent_ns.nspname,parent.relname)
           FROM pg_inherits inheritance
           JOIN pg_class parent ON parent.oid=inheritance.inhparent
           JOIN pg_namespace parent_ns ON parent_ns.oid=parent.relnamespace
           WHERE inheritance.inhrelid=c.oid
         ),'')
  FROM pg_class c
  LEFT JOIN pg_am am ON am.oid=c.relam
  JOIN pg_database current_database_row
    ON current_database_row.datname=current_database()
  JOIN pg_tablespace database_tablespace
    ON database_tablespace.oid=current_database_row.dattablespace
  LEFT JOIN pg_tablespace relation_tablespace
    ON relation_tablespace.oid=c.reltablespace
  WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p')
  UNION ALL
  SELECT 'column|' || c.relname || '|attnum=' ||
         lpad(a.attnum::text,5,'0') || '|name=' || a.attname ||
         '|type=' || format_type(a.atttypid,a.atttypmod) ||
         '|notnull=' || a.attnotnull ||
         '|default_or_generated=' || coalesce(btrim(regexp_replace(
           pg_get_expr(ad.adbin,ad.adrelid,false),'[[:space:]]+',' ','g')),'') ||
         '|identity=' || a.attidentity || '|generated=' || a.attgenerated ||
         '|collation=' || CASE WHEN a.attcollation=0 THEN '' ELSE
           collation_ns.nspname || '.' || collation.collname END ||
         '|storage=' || a.attstorage ||
         '|compression=' || coalesce(a.attcompression::text,'') ||
         '|statistics_target=' || a.attstattarget ||
         '|attacl_state=' || CASE WHEN a.attacl IS NULL THEN 'null'
                                  ELSE 'explicit' END ||
         '|attacl_items=' || coalesce(cardinality(a.attacl),0)
  FROM pg_attribute a
  JOIN pg_class c ON c.oid=a.attrelid
  LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
  LEFT JOIN pg_collation collation ON collation.oid=a.attcollation
  LEFT JOIN pg_namespace collation_ns ON collation_ns.oid=collation.collnamespace
  WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p')
    AND a.attnum > 0 AND NOT a.attisdropped
  UNION ALL
  SELECT 'column-acl|' || c.relname || '|attnum=' ||
         lpad(a.attnum::text,5,'0') || '|name=' || a.attname ||
         '|grantor=' || pg_get_userbyid(column_acl.grantor) ||
         '|grantee=' || CASE WHEN column_acl.grantee=0 THEN 'PUBLIC'
           ELSE pg_get_userbyid(column_acl.grantee) END ||
         '|privilege=' || column_acl.privilege_type ||
         '|grantable=' || column_acl.is_grantable
  FROM pg_attribute a
  JOIN pg_class c ON c.oid=a.attrelid
  CROSS JOIN LATERAL aclexplode(a.attacl) AS column_acl(
    grantor,grantee,privilege_type,is_grantable
  )
  WHERE c.relnamespace='public'::regnamespace AND c.relkind IN ('r','p')
    AND a.attnum > 0 AND NOT a.attisdropped
  UNION ALL
  SELECT 'policy-set|count=' || count(*) || '|names=' || coalesce(string_agg(
           policy_table.relname || '.' || policy.polname,','
           ORDER BY policy_table.relname,policy.polname),'')
  FROM pg_policy policy
  JOIN pg_class policy_table ON policy_table.oid=policy.polrelid
  WHERE policy_table.relnamespace='public'::regnamespace
  UNION ALL
  SELECT 'policy|' || policy_table.relname || '|' || policy.polname ||
         '|permissive=' || policy.polpermissive ||
         '|command=' || policy.polcmd ||
         '|roles=' || coalesce((
           SELECT string_agg(
             CASE WHEN policy_role.role_oid=0 THEN 'PUBLIC'
                  ELSE pg_get_userbyid(policy_role.role_oid) END,','
             ORDER BY CASE WHEN policy_role.role_oid=0 THEN 'PUBLIC'
                           ELSE pg_get_userbyid(policy_role.role_oid) END)
           FROM unnest(policy.polroles) AS policy_role(role_oid)
         ),'') ||
         '|qual=' || coalesce(btrim(regexp_replace(
           pg_get_expr(policy.polqual,policy.polrelid,false),
           '[[:space:]]+',' ','g')),'') ||
         '|with_check=' || coalesce(btrim(regexp_replace(
           pg_get_expr(policy.polwithcheck,policy.polrelid,false),
           '[[:space:]]+',' ','g')),'')
  FROM pg_policy policy
  JOIN pg_class policy_table ON policy_table.oid=policy.polrelid
  WHERE policy_table.relnamespace='public'::regnamespace
  UNION ALL
  SELECT 'public-class|' || c.relname || '|kind=' || c.relkind
  FROM pg_class c
  WHERE c.relnamespace='public'::regnamespace
  UNION ALL
  SELECT 'sequence-set|count=' || count(*) || '|names=' ||
         coalesce(string_agg(c.relname,',' ORDER BY c.relname),'')
  FROM pg_class c
  WHERE c.relnamespace='public'::regnamespace AND c.relkind='S'
  UNION ALL
  SELECT 'sequence|' || c.relname || '|owner=' || pg_get_userbyid(c.relowner) ||
         '|persistence=' || c.relpersistence
  FROM pg_class c
  WHERE c.relnamespace='public'::regnamespace AND c.relkind='S'
  UNION ALL
  SELECT 'constraint|' || t.relname || '|' || c.conname || '|type=' ||
         c.contype || '|validated=' || c.convalidated || '|deferrable=' ||
         c.condeferrable || '|deferred=' || c.condeferred || '|noinherit=' ||
         c.connoinherit || '|definition=' ||
         btrim(regexp_replace(pg_get_constraintdef(c.oid,false),
                              '[[:space:]]+',' ','g'))
  FROM pg_constraint c
  JOIN pg_class t ON t.oid=c.conrelid
  WHERE t.relnamespace='public'::regnamespace AND t.relkind IN ('r','p')
    AND c.contype IN ('p','u','f','c','x')
  UNION ALL
  SELECT 'index|' || t.relname || '|' || x.relname || '|method=' || am.amname ||
         '|primary=' || i.indisprimary || '|unique=' || i.indisunique ||
         '|nulls_not_distinct=' || i.indnullsnotdistinct ||
         '|valid=' || i.indisvalid || '|ready=' || i.indisready ||
         '|live=' || i.indislive || '|clustered=' || i.indisclustered ||
         '|replica_identity=' || i.indisreplident || '|definition=' ||
         btrim(regexp_replace(pg_get_indexdef(x.oid,0,false),
                              '[[:space:]]+',' ','g'))
  FROM pg_index i
  JOIN pg_class t ON t.oid=i.indrelid
  JOIN pg_class x ON x.oid=i.indexrelid
  JOIN pg_am am ON am.oid=x.relam
  WHERE t.relnamespace='public'::regnamespace AND t.relkind IN ('r','p')
  UNION ALL
  SELECT 'trigger|' || trigger_table.relname || '|' || t.tgname ||
         '|enabled=' || t.tgenabled || '|type=' || t.tgtype || '|function=' ||
         t.tgfoid::regprocedure::text || '|definition=' ||
         btrim(regexp_replace(pg_get_triggerdef(t.oid,false),
                              '[[:space:]]+',' ','g'))
  FROM pg_trigger t
  JOIN pg_class trigger_table ON trigger_table.oid=t.tgrelid
  WHERE trigger_table.relnamespace='public'::regnamespace AND NOT t.tgisinternal
  UNION ALL
  SELECT 'function|' || p.oid::regprocedure::text || '|owner=' ||
         pg_get_userbyid(p.proowner) || '|kind=' || p.prokind || '|volatility=' ||
         p.provolatile || '|security_definer=' || p.prosecdef || '|leakproof=' ||
         p.proleakproof || '|strict=' || p.proisstrict || '|parallel=' ||
         p.proparallel || '|config=' || coalesce(array_to_string(p.proconfig,','),'') ||
         '|definition=' ||
         btrim(regexp_replace(pg_get_functiondef(p.oid),'[[:space:]]+',' ','g'))
  FROM pg_proc p
  WHERE p.pronamespace='public'::regnamespace
)
SELECT line FROM catalog ORDER BY line COLLATE "C";
COMMIT;
SQL
}

capture_public_relkind_mismatches() {
  local psql_wrapper="$1"
  local output="$2"
  "$psql_wrapper" -v ON_ERROR_STOP=1 -At > "$output" <<'SQL'
BEGIN READ ONLY;
WITH expected(name) AS (
  VALUES
    ('alembic_version'),('assets'),('market_reports'),
    ('market_asset_snapshots'),('decisions'),('decision_signal_snapshots'),
    ('signals'),('capability_evidence'),('cost_summaries'),('cost_sessions'),
    ('system_status_snapshots'),('migration_runs'),('migration_source_files'),
    ('migration_source_chunks'),('migration_errors'),('audit_events'),
    ('decision_field_lineage'),('cost_session_assets'),
    ('asset_source_lineage'),('phase3b_backfill_runs'),
    ('phase3b_backfill_events'),('jobs'),('job_attempts'),('job_events'),
    ('scheduler_heartbeats'),('job_artifacts'),('worker_heartbeats')
)
SELECT 'missing-regular-table|' || expected.name
FROM expected
WHERE NOT EXISTS (
  SELECT 1 FROM pg_class relation
  WHERE relation.relnamespace='public'::regnamespace
    AND relation.relname=expected.name AND relation.relkind='r'
)
UNION ALL
SELECT 'extra-or-wrong-table|' || relation.relname || '|kind=' || relation.relkind
FROM pg_class relation
WHERE relation.relnamespace='public'::regnamespace
  AND relation.relkind IN ('r','p','f')
  AND NOT (
    relation.relkind='r' AND
    relation.relname IN (SELECT expected.name FROM expected)
  )
UNION ALL
SELECT 'unsupported-public-relkind|' || relation.relname || '|kind=' ||
       relation.relkind
FROM pg_class relation
WHERE relation.relnamespace='public'::regnamespace
  AND relation.relkind NOT IN ('r','i')
UNION ALL
SELECT 'index-on-unexpected-relation|' || index_relation.relname ||
       '|target=' || coalesce(table_relation.relname,'missing')
FROM pg_class index_relation
LEFT JOIN pg_index index_catalog ON index_catalog.indexrelid=index_relation.oid
LEFT JOIN pg_class table_relation ON table_relation.oid=index_catalog.indrelid
WHERE index_relation.relnamespace='public'::regnamespace
  AND index_relation.relkind='i'
  AND (
    table_relation.oid IS NULL OR
    table_relation.relnamespace <> 'public'::regnamespace OR
    table_relation.relkind <> 'r' OR
    table_relation.relname NOT IN (SELECT expected.name FROM expected)
  )
ORDER BY 1;
COMMIT;
SQL
}

assert_original_identity_and_head before-post-0004-dump
test "$(tail -n1 "$EVIDENCE_DIR/head-before-post-0004-dump.txt")" \
  = "1|0004_durable_research_jobs|0004_durable_research_jobs"
capture_side_effect_object_mismatches orig_admin_psql 0004 \
  "$EVIDENCE_DIR/source-side-effect-mismatches-before-final-dump.txt"
test ! -s "$EVIDENCE_DIR/source-side-effect-mismatches-before-final-dump.txt"

readonly FINAL_DUMP="$BACKUP_DIR/trading_agent-$RUN_ID-0004-recovered.dump"
test ! -e "$FINAL_DUMP"
test ! -L "$FINAL_DUMP"
test ! -e "$FINAL_DUMP.partial"
test ! -L "$FINAL_DUMP.partial"

run_libpq_client unprivileged "$OWNER_PGPASS" recovery-final-dump \
  "$PG_BIN/pg_dump" \
  --host "$ORIG_HOST" --port "$ORIG_PORT" \
  --username trading_owner --dbname "$ORIG_DB" --no-password \
  --format=custom --serializable-deferrable \
  --no-publications --no-subscriptions \
  --file "$FINAL_DUMP.partial"
run_postgres_client_no_connection "$PG_BIN/pg_restore" \
  --list "$FINAL_DUMP.partial" \
  > "$EVIDENCE_DIR/final-0004.catalog"
assert_archive_toc_side_effect_free "$EVIDENCE_DIR/final-0004.catalog" \
  0004 "$EVIDENCE_DIR/final-0004-forbidden-toc.txt"
readonly FINAL_RESTORE_TOC="$EVIDENCE_DIR/final-0004.restore.list"
readonly FINAL_RESTORE_TOC_SHA256="$EVIDENCE_DIR/final-0004.restore-list.sha256"
build_restore_list_without_builtin_plpgsql "$EVIDENCE_DIR/final-0004.catalog" \
  "$FINAL_RESTORE_TOC" "$FINAL_RESTORE_TOC_SHA256"
chmod 0600 "$FINAL_DUMP.partial"
mv "$FINAL_DUMP.partial" "$FINAL_DUMP"
durably_finalize_dump "$FINAL_DUMP" final-0004 \
  "$EVIDENCE_DIR/final-0004.catalog"
assert_original_identity_and_head after-post-0004-dump

orig_reader_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/table-set-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH expected(name) AS (
  VALUES
    ('assets'),('market_reports'),('market_asset_snapshots'),('decisions'),
    ('decision_signal_snapshots'),('signals'),('capability_evidence'),
    ('cost_summaries'),('cost_sessions'),('system_status_snapshots'),
    ('migration_runs'),('migration_source_files'),('migration_source_chunks'),
    ('migration_errors'),('audit_events'),('decision_field_lineage'),
    ('cost_session_assets'),('asset_source_lineage'),
    ('phase3b_backfill_runs'),('phase3b_backfill_events'),
    ('jobs'),('job_attempts'),('job_events'),('scheduler_heartbeats'),
    ('job_artifacts'),('worker_heartbeats')
), actual(name) AS (
  SELECT tablename FROM pg_tables
  WHERE schemaname='public' AND tablename <> 'alembic_version'
)
SELECT 'missing|' || name FROM (SELECT name FROM expected EXCEPT SELECT name FROM actual) q
UNION ALL
SELECT 'extra|' || name FROM (SELECT name FROM actual EXCEPT SELECT name FROM expected) q
ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/table-set-mismatches.txt"

orig_reader_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/phase4-count-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH counts(name,n) AS (
  SELECT 'jobs',count(*) FROM jobs UNION ALL
  SELECT 'job_attempts',count(*) FROM job_attempts UNION ALL
  SELECT 'job_events',count(*) FROM job_events UNION ALL
  SELECT 'scheduler_heartbeats',count(*) FROM scheduler_heartbeats UNION ALL
  SELECT 'job_artifacts',count(*) FROM job_artifacts UNION ALL
  SELECT 'worker_heartbeats',count(*) FROM worker_heartbeats
)
SELECT name || '|actual=' || n || '|expected=0'
FROM counts WHERE n <> 0 ORDER BY name;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/phase4-count-mismatches.txt"

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/constraint-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH expected(table_name,constraint_name,constraint_type) AS (
  VALUES
    ('jobs','jobs_pkey','p'),('jobs','uq_jobs_type_idempotency','u'),
    ('jobs','ck_jobs_actor_type','c'),('jobs','ck_jobs_attempt_count','c'),
    ('jobs','ck_jobs_cancel_actor_type','c'),('jobs','ck_jobs_cancel_shape','c'),
    ('jobs','ck_jobs_lease_shape','c'),('jobs','ck_jobs_max_attempts','c'),
    ('jobs','ck_jobs_payload_fingerprint','c'),('jobs','ck_jobs_payload_object','c'),
    ('jobs','ck_jobs_priority','c'),('jobs','ck_jobs_result_hash','c'),
    ('jobs','ck_jobs_result_metadata_object','c'),('jobs','ck_jobs_state','c'),
    ('jobs','ck_jobs_type','c'),
    ('job_attempts','job_attempts_pkey','p'),
    ('job_attempts','uq_job_attempts_job_number','u'),
    ('job_attempts','uq_job_attempts_job_id','u'),
    ('job_attempts','job_attempts_job_id_fkey','f'),
    ('job_attempts','ck_job_attempts_child_pid','c'),
    ('job_attempts','ck_job_attempts_command_fingerprint','c'),
    ('job_attempts','ck_job_attempts_number','c'),
    ('job_attempts','ck_job_attempts_outcome','c'),
    ('job_attempts','ck_job_attempts_process_group','c'),
    ('job_attempts','ck_job_attempts_process_start_ticks','c'),
    ('job_attempts','ck_job_attempts_stderr_sha256','c'),
    ('job_attempts','ck_job_attempts_stderr_shape','c'),
    ('job_attempts','ck_job_attempts_stderr_size','c'),
    ('job_attempts','ck_job_attempts_stdout_sha256','c'),
    ('job_attempts','ck_job_attempts_stdout_shape','c'),
    ('job_attempts','ck_job_attempts_stdout_size','c'),
    ('job_events','job_events_pkey','p'),
    ('job_events','uq_job_events_job_sequence','u'),
    ('job_events','job_events_job_id_fkey','f'),
    ('job_events','fk_job_events_job_attempt','f'),
    ('job_events','ck_job_events_actor_type','c'),
    ('job_events','ck_job_events_from_state','c'),
    ('job_events','ck_job_events_metadata_object','c'),
    ('job_events','ck_job_events_sequence','c'),
    ('job_events','ck_job_events_to_state','c'),
    ('scheduler_heartbeats','scheduler_heartbeats_pkey','p'),
    ('scheduler_heartbeats','scheduler_heartbeats_job_id_fkey','f'),
    ('scheduler_heartbeats','ck_scheduler_heartbeats_metadata_object','c'),
    ('scheduler_heartbeats','ck_scheduler_heartbeats_outcome','c'),
    ('job_artifacts','job_artifacts_pkey','p'),
    ('job_artifacts','uq_job_artifacts_attempt_ref','u'),
    ('job_artifacts','job_artifacts_job_id_fkey','f'),
    ('job_artifacts','fk_job_artifacts_job_attempt','f'),
    ('job_artifacts','ck_job_artifacts_sha256','c'),
    ('job_artifacts','ck_job_artifacts_size','c'),
    ('job_artifacts','ck_job_artifacts_storage_shape','c'),
    ('job_artifacts','ck_job_artifacts_validation_metadata_object','c'),
    ('worker_heartbeats','worker_heartbeats_pkey','p'),
    ('worker_heartbeats','worker_heartbeats_current_job_id_fkey','f'),
    ('worker_heartbeats','fk_worker_heartbeats_job_attempt','f'),
    ('worker_heartbeats','ck_worker_heartbeats_current_shape','c'),
    ('worker_heartbeats','ck_worker_heartbeats_metadata_object','c'),
    ('worker_heartbeats','ck_worker_heartbeats_status','c')
), actual AS (
  SELECT t.relname::text, c.conname::text, c.contype::text
  FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
  WHERE t.relnamespace='public'::regnamespace
    AND t.relname IN ('jobs','job_attempts','job_events','scheduler_heartbeats',
                      'job_artifacts','worker_heartbeats')
    AND c.contype IN ('p','u','f','c')
)
SELECT 'missing|' || table_name || '|' || constraint_name || '|' || constraint_type
FROM (SELECT * FROM expected EXCEPT SELECT * FROM actual) q
UNION ALL
SELECT 'extra|' || relname || '|' || conname || '|' || contype
FROM (SELECT * FROM actual EXCEPT SELECT * FROM expected) q(relname,conname,contype)
ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/constraint-mismatches.txt"

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/key-fk-definition-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH expected(table_name,constraint_name,definition) AS (
  VALUES
    ('jobs','jobs_pkey','PRIMARY KEY (job_id)'),
    ('jobs','uq_jobs_type_idempotency','UNIQUE (job_type, idempotency_key)'),
    ('job_attempts','job_attempts_pkey','PRIMARY KEY (attempt_id)'),
    ('job_attempts','uq_job_attempts_job_number','UNIQUE (job_id, attempt_number)'),
    ('job_attempts','uq_job_attempts_job_id','UNIQUE (job_id, attempt_id)'),
    ('job_attempts','job_attempts_job_id_fkey',
      'FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE'),
    ('job_events','job_events_pkey','PRIMARY KEY (event_id)'),
    ('job_events','uq_job_events_job_sequence','UNIQUE (job_id, sequence)'),
    ('job_events','job_events_job_id_fkey',
      'FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE'),
    ('job_events','fk_job_events_job_attempt',
      'FOREIGN KEY (job_id, attempt_id) REFERENCES job_attempts(job_id, attempt_id)'),
    ('scheduler_heartbeats','scheduler_heartbeats_pkey','PRIMARY KEY (heartbeat_id)'),
    ('scheduler_heartbeats','scheduler_heartbeats_job_id_fkey',
      'FOREIGN KEY (job_id) REFERENCES jobs(job_id)'),
    ('job_artifacts','job_artifacts_pkey','PRIMARY KEY (artifact_id)'),
    ('job_artifacts','uq_job_artifacts_attempt_ref',
      'UNIQUE (attempt_id, artifact_type, relative_ref)'),
    ('job_artifacts','job_artifacts_job_id_fkey',
      'FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE'),
    ('job_artifacts','fk_job_artifacts_job_attempt',
      'FOREIGN KEY (job_id, attempt_id) REFERENCES job_attempts(job_id, attempt_id)'),
    ('worker_heartbeats','worker_heartbeats_pkey','PRIMARY KEY (worker_id)'),
    ('worker_heartbeats','worker_heartbeats_current_job_id_fkey',
      'FOREIGN KEY (current_job_id) REFERENCES jobs(job_id)'),
    ('worker_heartbeats','fk_worker_heartbeats_job_attempt',
      'FOREIGN KEY (current_job_id, current_attempt_id) REFERENCES job_attempts(job_id, attempt_id)')
), actual AS (
  SELECT t.relname::text, c.conname::text, pg_get_constraintdef(c.oid,true)
  FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
  WHERE t.relnamespace='public'::regnamespace
    AND t.relname IN ('jobs','job_attempts','job_events','scheduler_heartbeats',
                      'job_artifacts','worker_heartbeats')
    AND c.contype IN ('p','u','f')
)
SELECT 'missing-or-wrong|' || table_name || '|' || constraint_name || '|' || definition
FROM (SELECT * FROM expected EXCEPT SELECT * FROM actual) q
UNION ALL
SELECT 'extra-or-wrong|' || relname || '|' || conname || '|' || definition
FROM (SELECT * FROM actual EXCEPT SELECT * FROM expected) q(relname,conname,definition)
ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/key-fk-definition-mismatches.txt"

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/check-constraint-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH expected(table_name,constraint_name,expression) AS (
  VALUES
    ('jobs','ck_jobs_actor_type',
      $$actor_type::text = ANY (ARRAY['OPERATOR'::character varying, 'SCHEDULER'::character varying, 'WORKER'::character varying, 'RECOVERY'::character varying, 'SYSTEM'::character varying]::text[])$$),
    ('jobs','ck_jobs_attempt_count',$$attempt_count >= 0$$),
    ('jobs','ck_jobs_cancel_actor_type',
      $$cancel_actor_type IS NULL OR (cancel_actor_type::text = ANY (ARRAY['OPERATOR'::character varying, 'SCHEDULER'::character varying, 'WORKER'::character varying, 'RECOVERY'::character varying, 'SYSTEM'::character varying]::text[]))$$),
    ('jobs','ck_jobs_cancel_shape',
      $$cancel_requested_at IS NULL AND cancel_actor_type IS NULL AND cancel_actor_id IS NULL OR cancel_requested_at IS NOT NULL AND cancel_actor_type IS NOT NULL AND cancel_actor_id IS NOT NULL$$),
    ('jobs','ck_jobs_lease_shape',
      $$lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL OR lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL$$),
    ('jobs','ck_jobs_max_attempts',$$max_attempts >= 1$$),
    ('jobs','ck_jobs_payload_fingerprint',$$char_length(payload_fingerprint::text) = 64$$),
    ('jobs','ck_jobs_payload_object',$$jsonb_typeof(payload) = 'object'::text$$),
    ('jobs','ck_jobs_priority',$$priority >= 0 AND priority <= 100$$),
    ('jobs','ck_jobs_result_hash',$$result_hash IS NULL OR char_length(result_hash::text) = 64$$),
    ('jobs','ck_jobs_result_metadata_object',$$jsonb_typeof(result_metadata) = 'object'::text$$),
    ('jobs','ck_jobs_state',
      $$state::text = ANY (ARRAY['QUEUED'::character varying, 'CLAIMED'::character varying, 'RUNNING'::character varying, 'SUCCEEDED'::character varying, 'FAILED'::character varying, 'BLOCKED'::character varying, 'TIMED_OUT'::character varying, 'CANCEL_REQUESTED'::character varying, 'CANCELLED'::character varying]::text[])$$),
    ('jobs','ck_jobs_type',
      $$job_type::text = ANY (ARRAY['SNAPSHOT'::character varying, 'DEBATE'::character varying, 'REPLAY'::character varying, 'BACKTEST'::character varying]::text[])$$),
    ('job_attempts','ck_job_attempts_child_pid',$$child_pid IS NULL OR child_pid > 0$$),
    ('job_attempts','ck_job_attempts_command_fingerprint',
      $$command_fingerprint IS NULL OR char_length(command_fingerprint::text) = 64$$),
    ('job_attempts','ck_job_attempts_number',$$attempt_number >= 1$$),
    ('job_attempts','ck_job_attempts_outcome',
      $$outcome::text = ANY (ARRAY['CLAIMED'::character varying, 'RUNNING'::character varying, 'SUCCEEDED'::character varying, 'FAILED'::character varying, 'BLOCKED'::character varying, 'TIMED_OUT'::character varying, 'CANCELLED'::character varying, 'INTERRUPTED'::character varying]::text[])$$),
    ('job_attempts','ck_job_attempts_process_group',$$process_group_id IS NULL OR process_group_id > 0$$),
    ('job_attempts','ck_job_attempts_process_start_ticks',
      $$process_start_ticks IS NULL OR process_start_ticks >= 0$$),
    ('job_attempts','ck_job_attempts_stderr_sha256',
      $$stderr_sha256 IS NULL OR char_length(stderr_sha256::text) = 64$$),
    ('job_attempts','ck_job_attempts_stderr_shape',
      $$stderr_ref IS NULL AND stderr_sha256 IS NULL AND stderr_size_bytes IS NULL OR stderr_ref IS NOT NULL AND stderr_sha256 IS NOT NULL AND stderr_size_bytes IS NOT NULL$$),
    ('job_attempts','ck_job_attempts_stderr_size',
      $$stderr_size_bytes IS NULL OR stderr_size_bytes >= 0$$),
    ('job_attempts','ck_job_attempts_stdout_sha256',
      $$stdout_sha256 IS NULL OR char_length(stdout_sha256::text) = 64$$),
    ('job_attempts','ck_job_attempts_stdout_shape',
      $$stdout_ref IS NULL AND stdout_sha256 IS NULL AND stdout_size_bytes IS NULL OR stdout_ref IS NOT NULL AND stdout_sha256 IS NOT NULL AND stdout_size_bytes IS NOT NULL$$),
    ('job_attempts','ck_job_attempts_stdout_size',
      $$stdout_size_bytes IS NULL OR stdout_size_bytes >= 0$$),
    ('job_events','ck_job_events_actor_type',
      $$actor_type::text = ANY (ARRAY['OPERATOR'::character varying, 'SCHEDULER'::character varying, 'WORKER'::character varying, 'RECOVERY'::character varying, 'SYSTEM'::character varying]::text[])$$),
    ('job_events','ck_job_events_from_state',
      $$from_state IS NULL OR (from_state::text = ANY (ARRAY['QUEUED'::character varying, 'CLAIMED'::character varying, 'RUNNING'::character varying, 'SUCCEEDED'::character varying, 'FAILED'::character varying, 'BLOCKED'::character varying, 'TIMED_OUT'::character varying, 'CANCEL_REQUESTED'::character varying, 'CANCELLED'::character varying]::text[]))$$),
    ('job_events','ck_job_events_metadata_object',$$jsonb_typeof(metadata) = 'object'::text$$),
    ('job_events','ck_job_events_sequence',$$sequence >= 1$$),
    ('job_events','ck_job_events_to_state',
      $$to_state::text = ANY (ARRAY['QUEUED'::character varying, 'CLAIMED'::character varying, 'RUNNING'::character varying, 'SUCCEEDED'::character varying, 'FAILED'::character varying, 'BLOCKED'::character varying, 'TIMED_OUT'::character varying, 'CANCEL_REQUESTED'::character varying, 'CANCELLED'::character varying]::text[])$$),
    ('scheduler_heartbeats','ck_scheduler_heartbeats_metadata_object',
      $$jsonb_typeof(metadata) = 'object'::text$$),
    ('scheduler_heartbeats','ck_scheduler_heartbeats_outcome',
      $$outcome::text = ANY (ARRAY['ENQUEUED'::character varying, 'DEDUPLICATED'::character varying, 'SKIPPED_NOT_SLOT'::character varying, 'FAILED'::character varying]::text[])$$),
    ('job_artifacts','ck_job_artifacts_sha256',$$char_length(sha256::text) = 64$$),
    ('job_artifacts','ck_job_artifacts_size',$$size_bytes >= 0$$),
    ('job_artifacts','ck_job_artifacts_storage_shape',
      $$relative_ref::text <> ''::text AND char_length(sha256::text) = 64 AND size_bytes >= 0$$),
    ('job_artifacts','ck_job_artifacts_validation_metadata_object',
      $$jsonb_typeof(validation_metadata) = 'object'::text$$),
    ('worker_heartbeats','ck_worker_heartbeats_current_shape',
      $$current_job_id IS NULL AND current_attempt_id IS NULL OR current_job_id IS NOT NULL AND current_attempt_id IS NOT NULL$$),
    ('worker_heartbeats','ck_worker_heartbeats_metadata_object',
      $$jsonb_typeof(metadata) = 'object'::text$$),
    ('worker_heartbeats','ck_worker_heartbeats_status',
      $$status::text = ANY (ARRAY['IDLE'::character varying, 'BUSY'::character varying, 'STOPPING'::character varying, 'UNHEALTHY'::character varying]::text[])$$)
), actual AS (
  SELECT t.relname::text, c.conname::text,
         btrim(regexp_replace(pg_get_expr(c.conbin,c.conrelid,true),
                              '[[:space:]]+',' ','g'))
  FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
  WHERE t.relnamespace='public'::regnamespace
    AND t.relname IN ('jobs','job_attempts','job_events','scheduler_heartbeats',
                      'job_artifacts','worker_heartbeats')
    AND c.contype='c'
)
SELECT 'missing-or-wrong|' || table_name || '|' || constraint_name || '|' || expression
FROM (SELECT * FROM expected EXCEPT SELECT * FROM actual) q
UNION ALL
SELECT 'extra-or-wrong|' || relname || '|' || conname || '|' || expression
FROM (SELECT * FROM actual EXCEPT SELECT * FROM expected) q(relname,conname,expression)
ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/check-constraint-mismatches.txt"

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/index-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH expected(table_name,index_name,access_method,is_unique,key_definition,predicate) AS (
  VALUES
    ('jobs','ix_jobs_claim','btree',false,
      'state,next_attempt_at,priority DESC,requested_at,job_id',
      '((state)::text = ''QUEUED''::text)'),
    ('jobs','ix_jobs_lease_expiry','btree',false,'lease_expires_at',
      '(lease_expires_at IS NOT NULL)'),
    ('jobs','ix_jobs_list','btree',false,'requested_at DESC,job_id DESC',''),
    ('jobs','uq_jobs_type_idempotency','btree',true,'job_type,idempotency_key',''),
    ('job_attempts','uq_job_attempts_job_id','btree',true,'job_id,attempt_id',''),
    ('job_attempts','uq_job_attempts_job_number','btree',true,'job_id,attempt_number',''),
    ('job_events','ix_job_events_job_sequence','btree',false,'job_id,sequence',''),
    ('job_events','uq_job_events_job_sequence','btree',true,'job_id,sequence',''),
    ('scheduler_heartbeats','ix_scheduler_heartbeats_tick','btree',false,
      'tick_at DESC,heartbeat_id DESC',''),
    ('job_artifacts','ix_job_artifacts_job','btree',false,'job_id,created_at',''),
    ('job_artifacts','uq_job_artifacts_attempt_ref','btree',true,
      'attempt_id,artifact_type,relative_ref',''),
    ('worker_heartbeats','ix_worker_heartbeats_at','btree',false,'heartbeat_at','')
), actual AS (
  SELECT t.relname::text, x.relname::text, am.amname::text, i.indisunique,
         (SELECT string_agg(pg_get_indexdef(i.indexrelid,n,true),',' ORDER BY n)
          FROM generate_series(1,i.indnkeyatts) n),
         coalesce(pg_get_expr(i.indpred,i.indrelid,true),'')
  FROM pg_index i
  JOIN pg_class t ON t.oid=i.indrelid
  JOIN pg_class x ON x.oid=i.indexrelid
  JOIN pg_am am ON am.oid=x.relam
  WHERE t.relnamespace='public'::regnamespace
    AND t.relname IN ('jobs','job_attempts','job_events','scheduler_heartbeats',
                      'job_artifacts','worker_heartbeats')
    AND NOT EXISTS (
      SELECT 1 FROM pg_constraint c
      WHERE c.conindid=i.indexrelid AND c.contype='p'
    )
)
SELECT 'missing-or-wrong|' || table_name || '|' || index_name || '|' ||
       access_method || '|' || is_unique || '|' || key_definition || '|' || predicate
FROM (SELECT * FROM expected EXCEPT SELECT * FROM actual) q
UNION ALL
SELECT 'extra-or-wrong|' || relname || '|' || index_name || '|' ||
       access_method || '|' || is_unique || '|' || key_definition || '|' || predicate
FROM (SELECT * FROM actual EXCEPT SELECT * FROM expected)
  q(relname,index_name,access_method,is_unique,key_definition,predicate)
ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/index-mismatches.txt"

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/integrity-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
SELECT 'unvalidated|' || conrelid::regclass || '|' || conname
FROM pg_constraint
WHERE connamespace='public'::regnamespace
  AND contype IN ('p','u','f','c','x') AND NOT convalidated
UNION ALL
SELECT 'invalid_index|' || c.relname
FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
JOIN pg_class t ON t.oid=i.indrelid
WHERE t.relnamespace='public'::regnamespace
  AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indislive)
UNION ALL
SELECT 'trigger_missing_or_wrong'
WHERE NOT EXISTS (
  SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
  WHERE t.tgrelid='public.job_events'::regclass
    AND t.tgname='trg_job_events_append_only'
    AND NOT t.tgisinternal AND t.tgenabled='O'
    AND t.tgtype=27
    AND p.oid='public.reject_job_event_mutation()'::regprocedure
)
UNION ALL
SELECT 'unexpected_trigger|' || c.relname || '|' || t.tgname || '|' ||
       t.tgenabled || '|' || t.tgtype || '|' || t.tgfoid::regprocedure::text
FROM pg_trigger t
JOIN pg_class c ON c.oid=t.tgrelid
WHERE c.relnamespace='public'::regnamespace
  AND NOT t.tgisinternal
  AND NOT (
    c.oid='public.job_events'::regclass
    AND t.tgname='trg_job_events_append_only'
    AND t.tgenabled='O'
    AND t.tgtype=27
    AND t.tgfoid='public.reject_job_event_mutation()'::regprocedure
  )
UNION ALL
SELECT 'trigger_function_missing_or_wrong'
WHERE NOT EXISTS (
  SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
  WHERE p.oid='public.reject_job_event_mutation()'::regprocedure
    AND p.prorettype='trigger'::regtype
    AND p.pronargs=0
    AND l.lanname='plpgsql'
    AND pg_get_userbyid(p.proowner)='trading_owner'
    AND p.prokind='f'
    AND p.provolatile='v'
    AND NOT p.prosecdef
    AND NOT p.proleakproof
    AND NOT p.proisstrict
    AND p.proparallel='u'
    AND p.proconfig IS NULL
    AND btrim(regexp_replace(p.prosrc,'[[:space:]]+',' ','g')) =
      'BEGIN RAISE EXCEPTION ''job_events is append-only'' USING ERRCODE = ''55000''; END;'
)
UNION ALL
SELECT 'unexpected_function|' || p.oid::regprocedure::text
FROM pg_proc p
WHERE p.pronamespace='public'::regnamespace
  AND p.oid <> 'public.reject_job_event_mutation()'::regprocedure
ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/integrity-mismatches.txt"

orig_reader_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/orphan-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH checks(name,n) AS (
  SELECT 'job_attempts_without_job',count(*)
  FROM job_attempts a LEFT JOIN jobs j USING(job_id) WHERE j.job_id IS NULL
  UNION ALL
  SELECT 'job_events_without_job',count(*)
  FROM job_events e LEFT JOIN jobs j USING(job_id) WHERE j.job_id IS NULL
  UNION ALL
  SELECT 'job_artifacts_without_job',count(*)
  FROM job_artifacts a LEFT JOIN jobs j USING(job_id) WHERE j.job_id IS NULL
  UNION ALL
  SELECT 'job_events_cross_attempt',count(*)
  FROM job_events e LEFT JOIN job_attempts a
    ON a.job_id=e.job_id AND a.attempt_id=e.attempt_id
  WHERE e.attempt_id IS NOT NULL AND a.attempt_id IS NULL
  UNION ALL
  SELECT 'job_artifacts_cross_attempt',count(*)
  FROM job_artifacts r LEFT JOIN job_attempts a
    ON a.job_id=r.job_id AND a.attempt_id=r.attempt_id
  WHERE a.attempt_id IS NULL
  UNION ALL
  SELECT 'worker_heartbeats_cross_attempt',count(*)
  FROM worker_heartbeats w LEFT JOIN job_attempts a
    ON a.job_id=w.current_job_id AND a.attempt_id=w.current_attempt_id
  WHERE w.current_attempt_id IS NOT NULL AND a.attempt_id IS NULL
)
SELECT name || '|actual=' || n || '|expected=0'
FROM checks WHERE n <> 0 ORDER BY name;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/orphan-mismatches.txt"

capture_public_relkind_mismatches orig_admin_psql \
  "$EVIDENCE_DIR/original-public-relkind-mismatches.txt"
test ! -s "$EVIDENCE_DIR/original-public-relkind-mismatches.txt"
test "${PLAN[EXPECTED_CATALOG_QUERY_ID]}" = PG16_COMPLETE_RELATION_CATALOG_V2
capture_complete_catalog_snapshot orig_admin_psql \
  "$EVIDENCE_DIR/original-complete-catalog.snapshot"
grep -Fxq 'snapshot|query_id=PG16_COMPLETE_RELATION_CATALOG_V2|pg_major=16' \
  "$EVIDENCE_DIR/original-complete-catalog.snapshot"
test "$(grep -c '^relation|' \
  "$EVIDENCE_DIR/original-complete-catalog.snapshot")" -eq 27
test "$(grep -c '^column|' \
  "$EVIDENCE_DIR/original-complete-catalog.snapshot")" -ge 27
test "$(grep -c '^policy-set|' \
  "$EVIDENCE_DIR/original-complete-catalog.snapshot")" -eq 1
grep -Fxq 'sequence-set|count=0|names=' \
  "$EVIDENCE_DIR/original-complete-catalog.snapshot"
original_catalog_sha256="$(sha256sum \
  "$EVIDENCE_DIR/original-complete-catalog.snapshot" | awk '{print $1}')"
test "$original_catalog_sha256" = "${PLAN[EXPECTED_CATALOG_SHA256]}"
printf '%s  %s\n' "$original_catalog_sha256" \
  "$EVIDENCE_DIR/original-complete-catalog.snapshot" \
  > "$EVIDENCE_DIR/original-complete-catalog.sha256"
~~~

The orphan file is empty on success; it no longer emits zero-valued rows and
pretends they are mismatches. Query
PG16_COMPLETE_RELATION_CATALOG_V2 is defined literally above: PostgreSQL 16,
`public, pg_catalog` search path, whitespace-collapsed PostgreSQL deparsing, C
ordering, complete relation access-method/tablespace/security/replication/
partition/options metadata, attnum-ordered column type/default/generated/
identity/collation/storage/compression/statistics metadata, canonical expanded
column ACLs, canonical row-security policy definitions and role sets, every
public class name/kind, an explicit zero-sequence set, PK/unique/FK/check/
exclusion constraints, indexes, noninternal triggers, and public functions. It
covers all 26 application tables plus alembic_version. An independent relkind
gate requires those exact 27 ordinary tables and only their ordinary indexes;
it rejects every sequence, view, materialized view, foreign/composite relation,
partitioned table/index, or other public relkind. The original must match the
independently reviewed pre-run clean-build V2 hash; the isolated snapshot must
match both that hash and the original bytes. A hash learned from this incident
source or its restore is invalid provenance and cannot be approved.

### 5.12 Exact roles, memberships, owners, database/schema privileges, and ACLs

Run as explicit original admin in a read-only transaction. Zero output is the
only pass.

~~~bash
orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/security-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH expected_roles(
  role_name,canlogin,superuser,createdb,createrole,inherit_role,replication,bypassrls
) AS (
  VALUES
    ('trading_owner',true,false,false,false,false,false,false),
    ('trading_migrator',true,false,false,false,false,false,false),
    ('trading_reader',true,false,false,false,false,false,false),
    ('trading_jobs',true,false,false,false,false,false,false)
), expected_db_acl(role_name,privilege_name) AS (
  VALUES
    ('trading_owner','CONNECT'),('trading_owner','CREATE'),
    ('trading_owner','TEMPORARY'),('trading_migrator','CONNECT'),
    ('trading_reader','CONNECT'),('trading_jobs','CONNECT'),
    ('PUBLIC','CONNECT'),('PUBLIC','TEMPORARY')
), actual_db_acl(role_name,privilege_name,is_grantable) AS (
  SELECT CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,
         x.privilege_type,x.is_grantable
  FROM pg_database d
  CROSS JOIN LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) x
  WHERE d.datname=current_database()
), expected_schema_acl(role_name,privilege_name) AS (
  VALUES
    ('trading_owner','USAGE'),('trading_owner','CREATE'),
    ('trading_migrator','USAGE'),('trading_reader','USAGE'),
    ('trading_jobs','USAGE'),('PUBLIC','USAGE')
), actual_schema_acl(role_name,privilege_name,is_grantable) AS (
  SELECT CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,
         x.privilege_type,x.is_grantable
  FROM pg_namespace n
  CROSS JOIN LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) x
  WHERE n.nspname='public'
)
SELECT 'role_attrs|' || e.role_name
FROM expected_roles e LEFT JOIN pg_roles r ON r.rolname=e.role_name
WHERE r.rolname IS NULL OR
  (r.rolcanlogin,r.rolsuper,r.rolcreatedb,r.rolcreaterole,
   r.rolinherit,r.rolreplication,r.rolbypassrls)
  IS DISTINCT FROM
  (e.canlogin,e.superuser,e.createdb,e.createrole,
   e.inherit_role,e.replication,e.bypassrls)

UNION ALL
SELECT 'unexpected_membership|' || member.rolname || '->' || granted.rolname
FROM pg_auth_members m
JOIN pg_roles member ON member.oid=m.member
JOIN pg_roles granted ON granted.oid=m.roleid
WHERE member.rolname IN ('trading_owner','trading_migrator','trading_reader','trading_jobs')
   OR granted.rolname IN ('trading_owner','trading_migrator','trading_reader','trading_jobs')

UNION ALL
SELECT 'database_owner|' || pg_get_userbyid(datdba)
FROM pg_database WHERE datname=current_database()
  AND pg_get_userbyid(datdba) <> 'trading_owner'

UNION ALL
SELECT 'database_acl_missing|' || role_name || '|' || privilege_name
FROM (
  SELECT * FROM expected_db_acl
  EXCEPT
  SELECT role_name,privilege_name FROM actual_db_acl
) q

UNION ALL
SELECT 'database_acl_extra|' || role_name || '|' || privilege_name
FROM (
  SELECT role_name,privilege_name FROM actual_db_acl
  EXCEPT
  SELECT * FROM expected_db_acl
) q

UNION ALL
SELECT 'database_acl_grant_option|' || role_name || '|' || privilege_name
FROM actual_db_acl
WHERE is_grantable

UNION ALL
SELECT 'schema_owner|' || pg_get_userbyid(nspowner)
FROM pg_namespace WHERE nspname='public'
  AND pg_get_userbyid(nspowner) <> 'trading_owner'

UNION ALL
SELECT 'schema_acl_missing|' || role_name || '|' || privilege_name
FROM (
  SELECT * FROM expected_schema_acl
  EXCEPT
  SELECT role_name,privilege_name FROM actual_schema_acl
) q

UNION ALL
SELECT 'schema_acl_extra|' || role_name || '|' || privilege_name
FROM (
  SELECT role_name,privilege_name FROM actual_schema_acl
  EXCEPT
  SELECT * FROM expected_schema_acl
) q

UNION ALL
SELECT 'schema_acl_grant_option|' || role_name || '|' || privilege_name
FROM actual_schema_acl
WHERE is_grantable

UNION ALL
SELECT 'object_owner|' || c.relkind || '|' || c.relname || '|' ||
       pg_get_userbyid(c.relowner)
FROM pg_class c
WHERE c.relnamespace='public'::regnamespace
  AND c.relkind IN ('r','p','S','i')
  AND pg_get_userbyid(c.relowner) <> 'trading_owner'

UNION ALL
SELECT 'function_owner|' || p.oid::regprocedure::text || '|' ||
       pg_get_userbyid(p.proowner)
FROM pg_proc p WHERE p.pronamespace='public'::regnamespace
  AND pg_get_userbyid(p.proowner) <> 'trading_owner'

ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/security-mismatches.txt"

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/app-table-acl-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH tables(table_name,legacy_acl) AS (
  VALUES
    ('assets',true),('market_reports',true),('market_asset_snapshots',true),
    ('decisions',true),('decision_signal_snapshots',true),('signals',true),
    ('capability_evidence',true),('cost_summaries',true),('cost_sessions',true),
    ('system_status_snapshots',true),('migration_runs',true),
    ('migration_source_files',true),('migration_source_chunks',true),
    ('migration_errors',true),('audit_events',true),
    ('decision_field_lineage',true),('cost_session_assets',true),
    ('asset_source_lineage',true),('phase3b_backfill_runs',true),
    ('phase3b_backfill_events',true),('jobs',false),('job_attempts',false),
    ('job_events',false),('scheduler_heartbeats',false),
    ('job_artifacts',false),('worker_heartbeats',false),
    ('alembic_version',true)
), privileges(privilege_name) AS (
  VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),
         ('REFERENCES'),('TRIGGER')
), expected(role_name,table_name,privilege_name,is_grantable) AS (
  SELECT 'trading_owner',table_name,privilege_name,false
  FROM tables CROSS JOIN privileges
  UNION ALL
  SELECT 'trading_migrator',table_name,privilege_name,false
  FROM tables
  CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE')) p(privilege_name)
  WHERE legacy_acl
  UNION ALL
  SELECT 'trading_reader',table_name,'SELECT',false
  FROM tables WHERE legacy_acl
  UNION ALL VALUES
    ('trading_jobs','jobs','SELECT',false),
    ('trading_jobs','jobs','INSERT',false),
    ('trading_jobs','jobs','UPDATE',false),
    ('trading_jobs','job_attempts','SELECT',false),
    ('trading_jobs','job_attempts','INSERT',false),
    ('trading_jobs','job_attempts','UPDATE',false),
    ('trading_jobs','job_events','SELECT',false),
    ('trading_jobs','job_events','INSERT',false),
    ('trading_jobs','scheduler_heartbeats','SELECT',false),
    ('trading_jobs','scheduler_heartbeats','INSERT',false),
    ('trading_jobs','job_artifacts','SELECT',false),
    ('trading_jobs','job_artifacts','INSERT',false),
    ('trading_jobs','worker_heartbeats','SELECT',false),
    ('trading_jobs','worker_heartbeats','INSERT',false),
    ('trading_jobs','worker_heartbeats','UPDATE',false),
    ('trading_jobs','alembic_version','SELECT',false)
), actual(role_name,table_name,privilege_name,is_grantable) AS (
  SELECT CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,
         c.relname::text,x.privilege_type,x.is_grantable
  FROM pg_class c
  CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x
  WHERE c.relnamespace='public'::regnamespace
    AND c.relkind IN ('r','p')
), differences(kind,role_name,table_name,privilege_name,is_grantable) AS (
  SELECT 'missing',role_name,table_name,privilege_name,is_grantable
  FROM (SELECT * FROM expected EXCEPT SELECT * FROM actual) q
  UNION ALL
  SELECT 'extra',role_name,table_name,privilege_name,is_grantable
  FROM (SELECT * FROM actual EXCEPT SELECT * FROM expected) q
), classified AS (
  SELECT CASE
    WHEN kind='extra' AND NOT is_grantable
      AND table_name IN ('jobs','job_attempts','job_events',
                         'scheduler_heartbeats','job_artifacts','worker_heartbeats')
      AND (
        role_name='trading_migrator'
        AND privilege_name IN ('SELECT','INSERT','UPDATE','DELETE')
        OR role_name='trading_reader' AND privilege_name='SELECT'
      )
    THEN 'known-inherited-extra'
    ELSE kind
  END AS classification,
  role_name,table_name,privilege_name,is_grantable
  FROM differences
)
SELECT classification || '|' || role_name || '|' || table_name || '|' ||
       privilege_name || '|grantable=' || is_grantable
FROM classified
ORDER BY 1;
COMMIT;
SQL
grep '^known-inherited-extra|' "$EVIDENCE_DIR/app-table-acl-mismatches.txt" \
  > "$EVIDENCE_DIR/known-inherited-table-acl-leakage.txt" || true
grep -v '^known-inherited-extra|' "$EVIDENCE_DIR/app-table-acl-mismatches.txt" \
  > "$EVIDENCE_DIR/unexpected-table-acl-mismatches.txt" || true
test ! -s "$EVIDENCE_DIR/unexpected-table-acl-mismatches.txt"
test "$(wc -l < "$EVIDENCE_DIR/known-inherited-table-acl-leakage.txt")" -eq 30

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/sequence-acl-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
SELECT 'unexpected_sequence|' || c.relname || '|owner=' ||
       pg_get_userbyid(c.relowner)
FROM pg_class c
WHERE c.relnamespace='public'::regnamespace AND c.relkind='S'
UNION ALL
SELECT 'unexpected_sequence_acl|' || c.relname || '|' ||
       CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END ||
       '|' || x.privilege_type || '|grantable=' || x.is_grantable
FROM pg_class c
CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('S',c.relowner))) x
WHERE c.relnamespace='public'::regnamespace AND c.relkind='S'
ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/sequence-acl-mismatches.txt"

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/function-set-acl-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH expected_functions(function_name) AS (
  VALUES ('reject_job_event_mutation()')
), actual_functions(function_name) AS (
  SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')'
  FROM pg_proc p WHERE p.pronamespace='public'::regnamespace
), expected_acl(function_name,role_name,privilege_name,is_grantable) AS (
  VALUES
    ('reject_job_event_mutation()','trading_owner','EXECUTE',false),
    ('reject_job_event_mutation()','PUBLIC','EXECUTE',false)
), actual_acl(function_name,role_name,privilege_name,is_grantable) AS (
  SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
         CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,
         x.privilege_type,x.is_grantable
  FROM pg_proc p
  CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
  WHERE p.pronamespace='public'::regnamespace
)
SELECT 'missing_function|' || function_name
FROM (SELECT * FROM expected_functions EXCEPT SELECT * FROM actual_functions) q
UNION ALL
SELECT 'extra_function|' || function_name
FROM (SELECT * FROM actual_functions EXCEPT SELECT * FROM expected_functions) q
UNION ALL
SELECT 'missing_acl|' || function_name || '|' || role_name || '|' ||
       privilege_name || '|grantable=' || is_grantable
FROM (SELECT * FROM expected_acl EXCEPT SELECT * FROM actual_acl) q
UNION ALL
SELECT 'extra_acl|' || function_name || '|' || role_name || '|' ||
       privilege_name || '|grantable=' || is_grantable
FROM (SELECT * FROM actual_acl EXCEPT SELECT * FROM expected_acl) q
ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/function-set-acl-mismatches.txt"

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/default-acl-unexpected-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH table_privileges(privilege_name) AS (
  VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),
         ('REFERENCES'),('TRIGGER')
), expected(owner_name,schema_name,objtype,role_name,privilege_name,is_grantable) AS (
  SELECT 'trading_owner','public','r','trading_owner',privilege_name,false
  FROM table_privileges
  UNION ALL VALUES
    ('trading_owner','public','r','trading_migrator','SELECT',false),
    ('trading_owner','public','r','trading_migrator','INSERT',false),
    ('trading_owner','public','r','trading_migrator','UPDATE',false),
    ('trading_owner','public','r','trading_migrator','DELETE',false),
    ('trading_owner','public','r','trading_reader','SELECT',false),
    ('trading_owner','public','S','trading_owner','SELECT',false),
    ('trading_owner','public','S','trading_owner','UPDATE',false),
    ('trading_owner','public','S','trading_owner','USAGE',false),
    ('trading_owner','public','S','trading_migrator','SELECT',false),
    ('trading_owner','public','S','trading_migrator','USAGE',false)
), actual(owner_name,schema_name,objtype,role_name,privilege_name,is_grantable) AS (
  SELECT pg_get_userbyid(d.defaclrole),coalesce(n.nspname,'GLOBAL'),
         d.defaclobjtype,
         CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,
         x.privilege_type,x.is_grantable
  FROM pg_default_acl d
  LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
  CROSS JOIN LATERAL aclexplode(d.defaclacl) x
)
SELECT 'missing|' || owner_name || '|' || schema_name || '|' || objtype || '|' ||
       role_name || '|' || privilege_name || '|grantable=' || is_grantable
FROM (SELECT * FROM expected EXCEPT SELECT * FROM actual) q
UNION ALL
SELECT 'extra|' || owner_name || '|' || schema_name || '|' || objtype || '|' ||
       role_name || '|' || privilege_name || '|grantable=' || is_grantable
FROM (SELECT * FROM actual EXCEPT SELECT * FROM expected) q
ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/default-acl-unexpected-mismatches.txt"

orig_admin_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/known-default-acl-leakage.txt" <<'SQL'
BEGIN READ ONLY;
SELECT pg_get_userbyid(d.defaclrole) || '|' || n.nspname || '|' ||
       d.defaclobjtype || '|' || pg_get_userbyid(x.grantee) || '|' ||
       x.privilege_type || '|grantable=' || x.is_grantable
FROM pg_default_acl d
JOIN pg_namespace n ON n.oid=d.defaclnamespace
CROSS JOIN LATERAL aclexplode(d.defaclacl) x
WHERE d.defaclrole='trading_owner'::regrole
  AND n.nspname='public'
  AND x.grantee <> d.defaclrole
ORDER BY 1;
COMMIT;
SQL
test "$(wc -l < "$EVIDENCE_DIR/known-default-acl-leakage.txt")" -eq 7
RECOVERY_GATE_FAILED=true
~~~

The only accumulated failure is the exact anticipated inheritance of 0001's
non-owner default grants: 30 extra grants on the six 0004 job tables and the
seven non-owner table/sequence default-ACL rows. Exact line counts are safe only
because SQL classification and full-set comparisons constrain every tuple.
Any missing known tuple, unexpected grantee, grant option, role/membership,
owner, database/schema/table/sequence/function/PUBLIC privilege, extra object,
or other default-ACL difference hard-stops. The known evidence is preserved so
the already-approved isolated comparison can finish, followed by the mandatory
clean original stop and final NO-GO. Never repair ACLs interactively or edit an
applied migration.

### 5.13 Post-0004 dump already captured

The collision-checked final 0004 dump, catalog, stat, and SHA-256 were captured
at the start of Section 5.11, before any expected ACL failure could invoke the
controlled failure stop. Do not create a second dump or reuse its names.

No globals/password dump is part of this sub-gate. Role definitions are
captured as nonsecret catalog evidence; credential backup/rotation is separate.

### 5.14 Mechanically bind and verify the isolated target before any restore

The isolated cluster is pre-provisioned; this runbook never initializes it.
It uses its approved Unix socket, non-55432 port, different PGDATA, unique
database, and protected admin file. There is no ambient target. Before the
first libpq connection, file-derived PostgreSQL settings and postmaster argv
must independently prove empty preload/external-command settings, disabled WAL
senders/logical workers/autovacuum/archive mode, and no alternate config-file
override. The sanitized first admin session proves the same effective runtime
state before any create or restore.

~~~bash
test "$ISO_HOST" = "$ISO_SOCKET"
test "$ISO_HOST" != "$ORIG_HOST"
test "$ISO_SOCKET" != "$ORIG_SOCKET"
test "$ISO_PORT" != "$ORIG_PORT"
test "$ISO_PGDATA" != "$ORIG_PGDATA"
test "$ISO_RESTORE_DB" != "$ORIG_DB"
ISO_BOUND_PID=
ISO_BOUND_START_EPOCH=
ISO_BOUND_PROC_START_TICKS=

assert_isolated_preconnection_configuration() {
  local phase="$1"
  local archive_command=
  local archive_library=
  local iso_pid=
  local option=
  local option_name=
  local option_value=
  local expected_value=
  local i=
  local -a pid_lines=()
  local -a argv=()
  [[ "$phase" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]
  assert_no_ambient_postgres_environment
  test "$("$PG_BIN/postgres" -D "$ISO_PGDATA" -C archive_mode)" = off
  archive_command="$("$PG_BIN/postgres" -D "$ISO_PGDATA" \
    -c archive_mode=on -C archive_command)"
  archive_library="$("$PG_BIN/postgres" -D "$ISO_PGDATA" \
    -c archive_mode=on -C archive_library)"
  test -z "$archive_command"
  test -z "$archive_library"
  for option_name in restore_command archive_cleanup_command \
    recovery_end_command primary_conninfo shared_preload_libraries \
    session_preload_libraries local_preload_libraries ssl_passphrase_command \
    external_pid_file
  do
    test -z "$("$PG_BIN/postgres" -D "$ISO_PGDATA" -C "$option_name")"
  done
  test "$("$PG_BIN/postgres" -D "$ISO_PGDATA" \
    -C max_logical_replication_workers)" = 0
  test "$("$PG_BIN/postgres" -D "$ISO_PGDATA" -C max_wal_senders)" = 0
  test "$("$PG_BIN/postgres" -D "$ISO_PGDATA" -C autovacuum)" = off

  test -f "$ISO_PGDATA/postmaster.pid"
  test ! -L "$ISO_PGDATA/postmaster.pid"
  mapfile -t pid_lines < "$ISO_PGDATA/postmaster.pid"
  iso_pid="${pid_lines[0]}"
  [[ "$iso_pid" =~ ^[1-9][0-9]*$ ]]
  kill -0 "$iso_pid"
  test "$(readlink -e -- "/proc/$iso_pid/cwd")" = "$ISO_PGDATA"
  test "$(readlink -e -- "/proc/$iso_pid/exe")" = "$PG_BIN/postgres"
  mapfile -d '' -t argv < "/proc/$iso_pid/cmdline"
  for ((i=0; i<${#argv[@]}; i++)); do
    if test "${argv[i]}" = -c; then
      test $((i+1)) -lt "${#argv[@]}"
      option="${argv[i+1]}"
      i=$((i+1))
    elif [[ "${argv[i]}" == --*=* ]]; then
      option="${argv[i]#--}"
    else
      continue
    fi
    [[ "$option" == *=* ]]
    option_name="${option%%=*}"
    option_name="${option_name//-/_}"
    option_value="${option#*=}"
    case "$option_name" in
      config_file) return 1 ;;
      archive_mode) expected_value=off ;;
      max_logical_replication_workers|max_wal_senders) expected_value=0 ;;
      autovacuum) expected_value=off ;;
      archive_command|archive_library|restore_command|archive_cleanup_command|\
      recovery_end_command|primary_conninfo|shared_preload_libraries|\
      session_preload_libraries|local_preload_libraries|ssl_passphrase_command|\
      external_pid_file) expected_value='' ;;
      *) continue ;;
    esac
    test "$option_value" = "$expected_value"
  done
  printf 'phase=%s|config_safe=true|argv_overrides_safe=true\n' "$phase" \
    > "$EVIDENCE_DIR/isolated-preconnection-settings-$phase.txt"
}

assert_isolated_safe_runtime() {
  local phase="$1"
  [[ "$phase" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]]
  iso_admin_psql -v ON_ERROR_STOP=1 -At \
    > "$EVIDENCE_DIR/isolated-safe-runtime-$phase.txt" <<'SQL'
BEGIN READ ONLY;
SELECT
  current_setting('archive_mode')='off' AND
  current_setting('archive_command') IN ('','(disabled)') AND
  current_setting('archive_library') IN ('','(disabled)') AND
  current_setting('restore_command')='' AND
  current_setting('archive_cleanup_command')='' AND
  current_setting('recovery_end_command')='' AND
  current_setting('primary_conninfo')='' AND
  current_setting('shared_preload_libraries')='' AND
  current_setting('session_preload_libraries')='' AND
  current_setting('local_preload_libraries')='' AND
  current_setting('ssl_passphrase_command')='' AND
  current_setting('external_pid_file')='' AND
  current_setting('max_logical_replication_workers')='0' AND
  current_setting('max_wal_senders')='0' AND
  current_setting('autovacuum')='off' AND
  current_setting('jit')='off' AND
  current_setting('search_path')='pg_catalog,public' AND
  (SELECT source='client' FROM pg_settings
   WHERE name='session_preload_libraries') AND
  (SELECT source='client' FROM pg_settings
   WHERE name='local_preload_libraries') AND
  (SELECT source='client' FROM pg_settings WHERE name='jit') AND
  (SELECT source='client' FROM pg_settings WHERE name='search_path');
COMMIT;
SQL
  test "$(tail -n1 "$EVIDENCE_DIR/isolated-safe-runtime-$phase.txt")" = t
}

assert_isolated_identity() {
  local phase="$1"
  local iso_pid=
  local iso_start_epoch=
  local iso_proc_start_ticks=
  local iso_system_id=
  local found_iso_data_arg=false
  local iso_i=
  local -a iso_pid_lines=()
  local -a iso_postmaster_argv=()

  assert_isolated_preconnection_configuration "$phase"
  test -d "$ISO_PGDATA"
  test ! -L "$ISO_PGDATA"
  test "$(realpath -e -- "$ISO_PGDATA")" = "$ISO_PGDATA"
  test "$(stat -c '%U' "$ISO_PGDATA")" = thenam176
  test "$(stat -c '%a' "$ISO_PGDATA")" = 700
  test -d "$ISO_SOCKET"
  test ! -L "$ISO_SOCKET"
  test "$(realpath -e -- "$ISO_SOCKET")" = "$ISO_SOCKET"
  test "$(stat -c '%U' "$ISO_SOCKET")" = thenam176
  test "$(stat -c '%a' "$ISO_SOCKET")" = 700
  test -z "$(ss -ltnH | awk -v p=":$ISO_PORT" '$4 ~ p"$" {print}')"
  test -S "$ISO_SOCKET/.s.PGSQL.$ISO_PORT"
  test "$(stat -c '%U' "$ISO_SOCKET/.s.PGSQL.$ISO_PORT")" = thenam176

  "$PG_BIN/pg_controldata" -D "$ISO_PGDATA" \
    > "$EVIDENCE_DIR/isolated-pg-controldata-$phase.txt"
  iso_system_id="$(awk -F': *' '/Database system identifier/ {print $2}' \
    "$EVIDENCE_DIR/isolated-pg-controldata-$phase.txt")"
  test "$iso_system_id" = "${PLAN[ISO_SYSTEM_ID]}"
  test "$iso_system_id" != "${PLAN[ORIG_SYSTEM_ID]}"

  run_libpq_client admin "$ISO_ADMIN_PGPASS" recovery-iso-ready \
    "$PG_BIN/pg_isready" \
    --host "$ISO_HOST" --port "$ISO_PORT" \
    --username postgres --dbname "$ISO_ADMIN_DB" \
    > "$EVIDENCE_DIR/isolated-pg-isready-$phase.txt"

  iso_admin_psql -v ON_ERROR_STOP=1 -At \
    > "$EVIDENCE_DIR/isolated-identity-$phase.txt" <<'SQL'
BEGIN READ ONLY;
SELECT current_setting('server_version_num');
SELECT current_setting('data_directory');
SELECT current_setting('port');
SELECT current_setting('listen_addresses');
SELECT current_setting('unix_socket_directories');
SELECT current_database();
SELECT current_user;
SELECT system_identifier FROM pg_control_system();
COMMIT;
SQL
  test "$(sed -n '1p' "$EVIDENCE_DIR/isolated-identity-$phase.txt" | cut -c1-2)" = 16
  test "$(sed -n '2p' "$EVIDENCE_DIR/isolated-identity-$phase.txt")" = "$ISO_PGDATA"
  test "$(sed -n '3p' "$EVIDENCE_DIR/isolated-identity-$phase.txt")" = "$ISO_PORT"
  test -z "$(sed -n '4p' "$EVIDENCE_DIR/isolated-identity-$phase.txt")"
  test "$(sed -n '5p' "$EVIDENCE_DIR/isolated-identity-$phase.txt")" = "$ISO_SOCKET"
  test "$(sed -n '6p' "$EVIDENCE_DIR/isolated-identity-$phase.txt")" = "$ISO_ADMIN_DB"
  test "$(sed -n '7p' "$EVIDENCE_DIR/isolated-identity-$phase.txt")" = postgres
  test "$(sed -n '8p' "$EVIDENCE_DIR/isolated-identity-$phase.txt")" \
    = "${PLAN[ISO_SYSTEM_ID]}"

  test -f "$ISO_PGDATA/postmaster.pid"
  test ! -L "$ISO_PGDATA/postmaster.pid"
  test "$(stat -c '%U' "$ISO_PGDATA/postmaster.pid")" = thenam176
  test "$(stat -c '%a' "$ISO_PGDATA/postmaster.pid")" = 600
  mapfile -t iso_pid_lines < "$ISO_PGDATA/postmaster.pid"
  test "${#iso_pid_lines[@]}" -ge 6
  iso_pid="${iso_pid_lines[0]}"
  iso_start_epoch="${iso_pid_lines[2]}"
  [[ "$iso_pid" =~ ^[1-9][0-9]*$ ]]
  [[ "$iso_start_epoch" =~ ^[1-9][0-9]*$ ]]
  kill -0 "$iso_pid"
  test "${iso_pid_lines[1]}" = "$ISO_PGDATA"
  test "${iso_pid_lines[3]}" = "$ISO_PORT"
  test "${iso_pid_lines[4]}" = "$ISO_SOCKET"
  test "$(readlink -e -- "/proc/$iso_pid/cwd")" = "$ISO_PGDATA"
  test "$(readlink -e -- "/proc/$iso_pid/exe")" = "$PG_BIN/postgres"
  iso_proc_start_ticks="$(awk '{print $22}' "/proc/$iso_pid/stat")"
  [[ "$iso_proc_start_ticks" =~ ^[1-9][0-9]*$ ]]
  mapfile -d '' -t iso_postmaster_argv < "/proc/$iso_pid/cmdline"
  for ((iso_i=0; iso_i<${#iso_postmaster_argv[@]}-1; iso_i++)); do
    if test "${iso_postmaster_argv[iso_i]}" = -D &&
       test "${iso_postmaster_argv[iso_i+1]}" = "$ISO_PGDATA"; then
      found_iso_data_arg=true
    fi
  done
  test "$found_iso_data_arg" = true

  if test -z "$ISO_BOUND_PID"; then
    ISO_BOUND_PID="$iso_pid"
    ISO_BOUND_START_EPOCH="$iso_start_epoch"
    ISO_BOUND_PROC_START_TICKS="$iso_proc_start_ticks"
  else
    test "$iso_pid" = "$ISO_BOUND_PID"
    test "$iso_start_epoch" = "$ISO_BOUND_START_EPOCH"
    test "$iso_proc_start_ticks" = "$ISO_BOUND_PROC_START_TICKS"
  fi
  printf 'pid=%s\npostmaster_start_epoch=%s\nproc_start_ticks=%s\nsystem_id=%s\n' \
    "$iso_pid" "$iso_start_epoch" "$iso_proc_start_ticks" "$iso_system_id" \
    > "$EVIDENCE_DIR/isolated-process-identity-$phase.txt"
  assert_isolated_safe_runtime "$phase"
}

assert_isolated_identity before-create
capture_side_effect_object_mismatches iso_admin_psql empty \
  "$EVIDENCE_DIR/isolated-admin-side-effect-mismatches-before-create.txt"
test ! -s \
  "$EVIDENCE_DIR/isolated-admin-side-effect-mismatches-before-create.txt"

test "$(iso_admin_psql -v ON_ERROR_STOP=1 -Atc \
  "SELECT count(*) FROM pg_database WHERE datname=:'restore_db'" \
  --set=restore_db="$ISO_RESTORE_DB")" = 0
~~~

### 5.15 Create one isolated database, restore, and compare exact gates

~~~bash
assert_approval_current
restore_started_epoch="$(date -u +%s)"
run_libpq_client admin "$ISO_ADMIN_PGPASS" recovery-iso-createdb \
  "$PG_BIN/createdb" -w \
  --host "$ISO_HOST" --port "$ISO_PORT" --username postgres \
  --maintenance-db "$ISO_ADMIN_DB" --template template0 \
  --owner trading_owner "$ISO_RESTORE_DB"

assert_isolated_identity after-create
capture_side_effect_object_mismatches iso_restore_psql empty \
  "$EVIDENCE_DIR/isolated-side-effect-mismatches-after-create.txt"
test ! -s "$EVIDENCE_DIR/isolated-side-effect-mismatches-after-create.txt"
assert_approval_current
assert_isolated_identity immediately-before-restore
capture_side_effect_object_mismatches iso_restore_psql empty \
  "$EVIDENCE_DIR/isolated-side-effect-mismatches-before-restore.txt"
test ! -s "$EVIDENCE_DIR/isolated-side-effect-mismatches-before-restore.txt"
assert_archive_toc_side_effect_free "$EVIDENCE_DIR/final-0004.catalog" 0004 \
  "$EVIDENCE_DIR/final-0004-forbidden-toc-before-restore.txt"
test -f "$FINAL_RESTORE_TOC"
test ! -L "$FINAL_RESTORE_TOC"
test "$(stat -c '%a' "$FINAL_RESTORE_TOC")" = 600
sha256sum -c "$FINAL_RESTORE_TOC_SHA256" \
  > "$EVIDENCE_DIR/final-0004-restore-list-check.txt"
run_libpq_client admin "$ISO_RESTORE_PGPASS" recovery-iso-restore \
  "$PG_BIN/pg_restore" \
  --host "$ISO_HOST" --port "$ISO_PORT" --username postgres \
  --dbname "$ISO_RESTORE_DB" --no-password \
  --exit-on-error --single-transaction --role trading_owner \
  --no-publications --no-subscriptions \
  --use-list "$FINAL_RESTORE_TOC" \
  "$FINAL_DUMP" \
  > "$EVIDENCE_DIR/isolated-restore.stdout" \
  2> "$EVIDENCE_DIR/isolated-restore.stderr"
assert_isolated_identity immediately-after-restore
capture_side_effect_object_mismatches iso_restore_psql 0004 \
  "$EVIDENCE_DIR/isolated-side-effect-mismatches-after-restore.txt"
test ! -s "$EVIDENCE_DIR/isolated-side-effect-mismatches-after-restore.txt"

iso_restore_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/isolated-head-counts.txt" <<'SQL'
BEGIN READ ONLY;
SET LOCAL ROLE trading_reader;
SELECT count(*) || '|' || min(version_num) || '|' || max(version_num)
FROM alembic_version;
SELECT count(*) FROM pg_tables
WHERE schemaname='public' AND tablename <> 'alembic_version';
SELECT
  (SELECT count(*) FROM assets) +
  (SELECT count(*) FROM market_reports) +
  (SELECT count(*) FROM market_asset_snapshots) +
  (SELECT count(*) FROM decisions) +
  (SELECT count(*) FROM signals) +
  (SELECT count(*) FROM capability_evidence) +
  (SELECT count(*) FROM cost_summaries) +
  (SELECT count(*) FROM cost_sessions);
SELECT count(*) FROM migration_errors;
SELECT count(*) FROM jobs;
SELECT count(*) FROM job_attempts;
SELECT count(*) FROM job_events;
SELECT count(*) FROM scheduler_heartbeats;
SELECT count(*) FROM job_artifacts;
SELECT count(*) FROM worker_heartbeats;
COMMIT;
SQL

test "$(sed -n '1p' "$EVIDENCE_DIR/isolated-head-counts.txt")" \
  = "1|0004_durable_research_jobs|0004_durable_research_jobs"
test "$(sed -n '2p' "$EVIDENCE_DIR/isolated-head-counts.txt")" = 26
test "$(sed -n '3p' "$EVIDENCE_DIR/isolated-head-counts.txt")" = 43055
test "$(sed -n '4p' "$EVIDENCE_DIR/isolated-head-counts.txt")" = 222
test -z "$(sed -n '5,10p' "$EVIDENCE_DIR/isolated-head-counts.txt" |
  awk '$1 != 0 {print}')"

capture_data_snapshot() {
  local psql_wrapper="$1"
  local output="$2"
  "$psql_wrapper" -v ON_ERROR_STOP=1 -At > "$output" <<'SQL'
BEGIN READ ONLY;
SET LOCAL ROLE trading_reader;
SELECT 'head|' || count(*) || '|' || min(version_num) || '|' || max(version_num)
FROM alembic_version;
SELECT 'table|' || tablename FROM pg_tables
WHERE schemaname='public' AND tablename <> 'alembic_version'
UNION ALL
SELECT 'count|assets|' || count(*) FROM assets UNION ALL
SELECT 'count|market_reports|' || count(*) FROM market_reports UNION ALL
SELECT 'count|market_asset_snapshots|' || count(*) FROM market_asset_snapshots UNION ALL
SELECT 'count|decisions|' || count(*) FROM decisions UNION ALL
SELECT 'count|decision_signal_snapshots|' || count(*) FROM decision_signal_snapshots UNION ALL
SELECT 'count|signals|' || count(*) FROM signals UNION ALL
SELECT 'count|capability_evidence|' || count(*) FROM capability_evidence UNION ALL
SELECT 'count|cost_summaries|' || count(*) FROM cost_summaries UNION ALL
SELECT 'count|cost_sessions|' || count(*) FROM cost_sessions UNION ALL
SELECT 'count|system_status_snapshots|' || count(*) FROM system_status_snapshots UNION ALL
SELECT 'count|migration_runs|' || count(*) FROM migration_runs UNION ALL
SELECT 'count|migration_source_files|' || count(*) FROM migration_source_files UNION ALL
SELECT 'count|migration_source_chunks|' || count(*) FROM migration_source_chunks UNION ALL
SELECT 'count|migration_errors|' || count(*) FROM migration_errors UNION ALL
SELECT 'count|audit_events|' || count(*) FROM audit_events UNION ALL
SELECT 'count|decision_field_lineage|' || count(*) FROM decision_field_lineage UNION ALL
SELECT 'count|cost_session_assets|' || count(*) FROM cost_session_assets UNION ALL
SELECT 'count|asset_source_lineage|' || count(*) FROM asset_source_lineage UNION ALL
SELECT 'count|phase3b_backfill_runs|' || count(*) FROM phase3b_backfill_runs UNION ALL
SELECT 'count|phase3b_backfill_events|' || count(*) FROM phase3b_backfill_events UNION ALL
SELECT 'count|jobs|' || count(*) FROM jobs UNION ALL
SELECT 'count|job_attempts|' || count(*) FROM job_attempts UNION ALL
SELECT 'count|job_events|' || count(*) FROM job_events UNION ALL
SELECT 'count|scheduler_heartbeats|' || count(*) FROM scheduler_heartbeats UNION ALL
SELECT 'count|job_artifacts|' || count(*) FROM job_artifacts UNION ALL
SELECT 'count|worker_heartbeats|' || count(*) FROM worker_heartbeats
ORDER BY 1;
COMMIT;
SQL
}

capture_security_snapshot() {
  local psql_wrapper="$1"
  local output="$2"
  "$psql_wrapper" -v ON_ERROR_STOP=1 -At > "$output" <<'SQL'
BEGIN READ ONLY;
SELECT 'role|' || rolname || '|' || rolcanlogin || '|' || rolsuper || '|' ||
       rolcreatedb || '|' || rolcreaterole || '|' || rolinherit || '|' ||
       rolreplication || '|' || rolbypassrls
FROM pg_roles WHERE rolname IN
  ('trading_owner','trading_migrator','trading_reader','trading_jobs')
UNION ALL
SELECT 'membership|' || member.rolname || '|' || granted.rolname
FROM pg_auth_members m JOIN pg_roles member ON member.oid=m.member
JOIN pg_roles granted ON granted.oid=m.roleid
WHERE member.rolname IN ('trading_owner','trading_migrator','trading_reader','trading_jobs')
   OR granted.rolname IN ('trading_owner','trading_migrator','trading_reader','trading_jobs')
UNION ALL
SELECT 'schema-owner|' || nspname || '|' || pg_get_userbyid(nspowner)
FROM pg_namespace WHERE nspname='public'
UNION ALL
SELECT 'schema-acl|' || CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END ||
       '|' || x.privilege_type || '|' || x.is_grantable
FROM pg_namespace n
CROSS JOIN LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) x
WHERE n.nspname='public'
UNION ALL
SELECT 'object-owner|' || c.relkind || '|' || c.relname || '|' || pg_get_userbyid(c.relowner)
FROM pg_class c WHERE c.relnamespace='public'::regnamespace
  AND c.relkind IN ('r','p','S','i')
UNION ALL
SELECT 'function-owner|' || p.oid::regprocedure::text || '|' || pg_get_userbyid(p.proowner)
FROM pg_proc p WHERE p.pronamespace='public'::regnamespace
UNION ALL
SELECT 'table-acl|' || c.relname || '|' ||
       CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END ||
       '|' || x.privilege_type || '|' || x.is_grantable
FROM pg_class c
CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) x
WHERE c.relnamespace='public'::regnamespace
  AND c.relkind IN ('r','p')
UNION ALL
SELECT 'sequence-acl|' || c.relname || '|' ||
       CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END ||
       '|' || x.privilege_type || '|' || x.is_grantable
FROM pg_class c
CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('S',c.relowner))) x
WHERE c.relnamespace='public'::regnamespace AND c.relkind='S'
UNION ALL
SELECT 'function-acl|' || p.proname || '(' ||
       pg_get_function_identity_arguments(p.oid) || ')|' ||
       CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END ||
       '|' || x.privilege_type || '|' || x.is_grantable
FROM pg_proc p
CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) x
WHERE p.pronamespace='public'::regnamespace
UNION ALL
SELECT 'default-acl|' || pg_get_userbyid(d.defaclrole) || '|' ||
       coalesce(n.nspname,'GLOBAL') || '|' || d.defaclobjtype || '|' ||
       CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END ||
       '|' || x.privilege_type || '|' || x.is_grantable
FROM pg_default_acl d
LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
CROSS JOIN LATERAL aclexplode(d.defaclacl) x
ORDER BY 1;
COMMIT;
SQL
}

capture_integrity_mismatches() {
  local psql_wrapper="$1"
  local output="$2"
  "$psql_wrapper" -v ON_ERROR_STOP=1 -At > "$output" <<'SQL'
BEGIN READ ONLY;
SELECT 'unvalidated|' || conrelid::regclass || '|' || conname
FROM pg_constraint WHERE connamespace='public'::regnamespace
  AND contype IN ('p','u','f','c','x') AND NOT convalidated
UNION ALL
SELECT 'invalid-index|' || c.relname FROM pg_index i
JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_class t ON t.oid=i.indrelid
WHERE t.relnamespace='public'::regnamespace
  AND (NOT i.indisvalid OR NOT i.indisready OR NOT i.indislive)
UNION ALL
SELECT 'trigger' WHERE NOT EXISTS (
  SELECT 1 FROM pg_trigger t WHERE t.tgrelid='public.job_events'::regclass
    AND t.tgname='trg_job_events_append_only' AND NOT t.tgisinternal
    AND t.tgenabled='O' AND t.tgtype=27
    AND t.tgfoid='public.reject_job_event_mutation()'::regprocedure
)
ORDER BY 1;
COMMIT;
SQL
}

capture_orphan_mismatches() {
  local psql_wrapper="$1"
  local output="$2"
  "$psql_wrapper" -v ON_ERROR_STOP=1 -At > "$output" <<'SQL'
BEGIN READ ONLY;
WITH checks(name,n) AS (
  SELECT 'job_attempts_without_job',count(*) FROM job_attempts a
    LEFT JOIN jobs j USING(job_id) WHERE j.job_id IS NULL UNION ALL
  SELECT 'job_events_without_job',count(*) FROM job_events e
    LEFT JOIN jobs j USING(job_id) WHERE j.job_id IS NULL UNION ALL
  SELECT 'job_artifacts_without_job',count(*) FROM job_artifacts a
    LEFT JOIN jobs j USING(job_id) WHERE j.job_id IS NULL UNION ALL
  SELECT 'job_events_cross_attempt',count(*) FROM job_events e
    LEFT JOIN job_attempts a ON a.job_id=e.job_id AND a.attempt_id=e.attempt_id
    WHERE e.attempt_id IS NOT NULL AND a.attempt_id IS NULL UNION ALL
  SELECT 'job_artifacts_cross_attempt',count(*) FROM job_artifacts r
    LEFT JOIN job_attempts a ON a.job_id=r.job_id AND a.attempt_id=r.attempt_id
    WHERE a.attempt_id IS NULL UNION ALL
  SELECT 'worker_heartbeats_cross_attempt',count(*) FROM worker_heartbeats w
    LEFT JOIN job_attempts a
      ON a.job_id=w.current_job_id AND a.attempt_id=w.current_attempt_id
    WHERE w.current_attempt_id IS NOT NULL AND a.attempt_id IS NULL
)
SELECT name || '|' || n FROM checks WHERE n <> 0 ORDER BY name;
COMMIT;
SQL
}

capture_data_snapshot orig_admin_psql "$EVIDENCE_DIR/original-data.snapshot"
capture_data_snapshot iso_restore_psql "$EVIDENCE_DIR/isolated-data.snapshot"
cmp "$EVIDENCE_DIR/original-data.snapshot" "$EVIDENCE_DIR/isolated-data.snapshot"

capture_complete_catalog_snapshot iso_restore_psql \
  "$EVIDENCE_DIR/isolated-complete-catalog.snapshot"
capture_public_relkind_mismatches iso_restore_psql \
  "$EVIDENCE_DIR/isolated-public-relkind-mismatches.txt"
test ! -s "$EVIDENCE_DIR/isolated-public-relkind-mismatches.txt"
grep -Fxq 'snapshot|query_id=PG16_COMPLETE_RELATION_CATALOG_V2|pg_major=16' \
  "$EVIDENCE_DIR/isolated-complete-catalog.snapshot"
test "$(grep -c '^relation|' \
  "$EVIDENCE_DIR/isolated-complete-catalog.snapshot")" -eq 27
test "$(grep -c '^column|' \
  "$EVIDENCE_DIR/isolated-complete-catalog.snapshot")" -ge 27
test "$(grep -c '^policy-set|' \
  "$EVIDENCE_DIR/isolated-complete-catalog.snapshot")" -eq 1
grep -Fxq 'sequence-set|count=0|names=' \
  "$EVIDENCE_DIR/isolated-complete-catalog.snapshot"
isolated_catalog_sha256="$(sha256sum \
  "$EVIDENCE_DIR/isolated-complete-catalog.snapshot" | awk '{print $1}')"
test "$isolated_catalog_sha256" = "${PLAN[EXPECTED_CATALOG_SHA256]}"
printf '%s  %s\n' "$isolated_catalog_sha256" \
  "$EVIDENCE_DIR/isolated-complete-catalog.snapshot" \
  > "$EVIDENCE_DIR/isolated-complete-catalog.sha256"
cmp "$EVIDENCE_DIR/original-complete-catalog.snapshot" \
  "$EVIDENCE_DIR/isolated-complete-catalog.snapshot"

capture_security_snapshot orig_admin_psql "$EVIDENCE_DIR/original-security.snapshot"
capture_security_snapshot iso_restore_psql "$EVIDENCE_DIR/isolated-security.snapshot"
cmp "$EVIDENCE_DIR/original-security.snapshot" "$EVIDENCE_DIR/isolated-security.snapshot"

capture_integrity_mismatches iso_restore_psql \
  "$EVIDENCE_DIR/isolated-integrity-mismatches.txt"
test ! -s "$EVIDENCE_DIR/isolated-integrity-mismatches.txt"
capture_orphan_mismatches iso_restore_psql \
  "$EVIDENCE_DIR/isolated-orphan-mismatches.txt"
test ! -s "$EVIDENCE_DIR/isolated-orphan-mismatches.txt"

iso_restore_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/isolated-database-acl-mismatches.txt" <<'SQL'
BEGIN READ ONLY;
WITH expected(role_name,privilege_name) AS (
  VALUES ('trading_owner','CONNECT'),('trading_owner','CREATE'),
         ('trading_owner','TEMPORARY'),('PUBLIC','CONNECT'),('PUBLIC','TEMPORARY')
), actual(role_name,privilege_name) AS (
  SELECT CASE WHEN x.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(x.grantee) END,
         x.privilege_type
  FROM pg_database d
  CROSS JOIN LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) x
  WHERE d.datname=current_database()
)
SELECT 'missing|' || role_name || '|' || privilege_name
FROM (SELECT * FROM expected EXCEPT SELECT * FROM actual) q
UNION ALL
SELECT 'extra|' || role_name || '|' || privilege_name
FROM (SELECT * FROM actual EXCEPT SELECT * FROM expected) q
ORDER BY 1;
COMMIT;
SQL
test ! -s "$EVIDENCE_DIR/isolated-database-acl-mismatches.txt"

iso_restore_psql -v ON_ERROR_STOP=1 -At \
  > "$EVIDENCE_DIR/isolated-known-default-acl-leakage.txt" <<'SQL'
BEGIN READ ONLY;
SELECT pg_get_userbyid(d.defaclrole) || '|' || n.nspname || '|' ||
       d.defaclobjtype || '|' || pg_get_userbyid(x.grantee) || '|' ||
       x.privilege_type || '|grantable=' || x.is_grantable
FROM pg_default_acl d JOIN pg_namespace n ON n.oid=d.defaclnamespace
CROSS JOIN LATERAL aclexplode(d.defaclacl) x
WHERE d.defaclrole='trading_owner'::regrole
  AND n.nspname='public'
  AND x.grantee <> d.defaclrole
ORDER BY 1;
COMMIT;
SQL
test "$(wc -l < "$EVIDENCE_DIR/isolated-known-default-acl-leakage.txt")" -eq 7
cmp "$EVIDENCE_DIR/known-default-acl-leakage.txt" \
  "$EVIDENCE_DIR/isolated-known-default-acl-leakage.txt"

capture_side_effect_object_mismatches iso_restore_psql 0004 \
  "$EVIDENCE_DIR/isolated-side-effect-mismatches-after-comparisons.txt"
test ! -s "$EVIDENCE_DIR/isolated-side-effect-mismatches-after-comparisons.txt"
assert_isolated_identity after-comparisons
restore_finished_epoch="$(date -u +%s)"
printf 'start_epoch=%s\nfinish_epoch=%s\nrto_seconds=%s\n' \
  "$restore_started_epoch" "$restore_finished_epoch" \
  "$((restore_finished_epoch - restore_started_epoch))" \
  > "$EVIDENCE_DIR/isolated-rto.txt"
~~~

The archive is created and restored with PostgreSQL 16's explicit
`--no-publications` and `--no-subscriptions` defenses. Its TOC must also contain
no event trigger, logical replication object, foreign server/mapping/table,
procedural language, access method, or extension other than either no `plpgsql`
TOC entries or the single exact built-in `plpgsql` extension/comment pair.
Every other extension form is rejected. The hashed restore list omits only that
exact optional pair, is rechecked immediately before use, and therefore leaves
the already-gated target's exact built-in `plpgsql` state untouched. The 0003
archive permits no public user function or trigger, while 0004 permits only the
reviewed append-only PL/pgSQL function and trigger. The empty isolated database
is gated immediately before restore, and the restored database is gated
immediately afterward and after comparison. Together with zero logical
workers/WAL senders and empty preload settings, restore cannot launch a logical
worker or hidden DDL/DML hook.

The source database ACL is gated exactly in Section 5.12. Because a non-create
archive is restored into a deliberately different database name, the isolated
database itself is gated against fresh PostgreSQL 16 owner/PUBLIC defaults;
schema ownership, role attributes/memberships, object ownership, all 27
table/control-relation ACLs, the empty sequence set, function/PUBLIC ACLs, and
default ACLs are compared byte-for-byte with the source. The exact known
default-ACL leakage is retained in both snapshots and does not abort before the
RTO or clean stop; any difference still hard-stops. No corrective SQL is issued
to make the restore pass.

The isolated database remains retained. Its cleanup or cluster stop uses a
separate approved isolated-environment procedure; no dropdb appears here.

### 5.16 Historical fallback is a separate isolated-only run

Only a distinct dual-reviewed transcript whose decision is
APPROVED_HISTORICAL_0003_ISOLATED_ONLY_WITH_51H_RPO permits the literal
historical dump as the isolated input. Its human reviewers must verify that it
contains no authorization for an original-cluster write.

First isolated state must be exactly head 0003, 20 application tables, 43,055
canonical rows, and 222 quarantine rows. Take a new isolated-0003 dump before
any migration. If separately approved, apply exact 0004 only inside isolation,
then run all 0004 gates. The result retains an approximately 51-hour RPO gap and
can never be called current original truth or used for cutover by this runbook.

### 5.17 Successful end-state: controlled original stop is mandatory

This recovery sub-gate always returns the original cluster to a clean stopped
state. Applications remain inactive.

~~~bash
capture_side_effect_object_mismatches orig_admin_psql 0004 \
  "$EVIDENCE_DIR/source-side-effect-mismatches-before-final-stop.txt"
test ! -s "$EVIDENCE_DIR/source-side-effect-mismatches-before-final-stop.txt"
assert_application_units_inactive before-original-stop
assert_original_path_properties immediately-before-original-stop \
  > "$EVIDENCE_DIR/original-paths-before-stop.txt"
assert_bound_original_postmaster
write_collision_safe_evidence_best_effort \
  "$EVIDENCE_DIR/original-postmaster-before-planned-stop.txt" \
  "pid=$ORIGINAL_BOUND_PID start_epoch=$ORIGINAL_BOUND_START_EPOCH proc_start_ticks=$ORIGINAL_BOUND_PROC_START_TICKS executable=$PG_BIN/postgres cwd=$ORIG_PGDATA port=$ORIG_PORT socket=$ORIG_SOCKET"
ORIGINAL_STOP_ATTEMPTED=true
set +e
"$PG_BIN/pg_ctl" -D "$ORIG_PGDATA" -w -t 120 stop -m fast \
  > "$EVIDENCE_DIR/original-stop.stdout" \
  2> "$EVIDENCE_DIR/original-stop.stderr"
stop_rc=$?
set -e
printf 'exit_code=%s\n' "$stop_rc" > "$EVIDENCE_DIR/original-stop.exit"
if test "$stop_rc" -eq 0; then
  ORIGINAL_STOP_SUCCEEDED=true
fi
test "$stop_rc" -eq 0

set +e
"$PG_BIN/pg_ctl" -D "$ORIG_PGDATA" status \
  > "$EVIDENCE_DIR/pg-ctl-final.txt" 2>&1
final_status_rc=$?
set -e
printf 'exit_code=%s\n' "$final_status_rc" >> "$EVIDENCE_DIR/pg-ctl-final.txt"
test "$final_status_rc" -eq 3
test -z "$(ss -ltnH | awk -v p=":$ORIG_PORT" '$4 ~ p"$" {print}')"
test ! -e "$ORIG_PGDATA/postmaster.pid"
"$PG_BIN/pg_controldata" -D "$ORIG_PGDATA" \
  > "$EVIDENCE_DIR/pg-controldata-final.txt"
grep -Eq '^Database cluster state:[[:space:]]+shut down$' \
  "$EVIDENCE_DIR/pg-controldata-final.txt"
assert_original_path_properties immediately-after-original-stop \
  > "$EVIDENCE_DIR/original-paths-after-stop.txt"
assert_application_units_inactive final-handoff
if test "$RECOVERY_GATE_FAILED" = true; then
  final_decision='NO-GO — RECOVERY SUB-GATE FAILED: exact inherited ACL leakage retained'
  final_exit=1
else
  final_decision='RECOVERY SUB-GATE EVIDENCE READY FOR INDEPENDENT REVIEW'
  final_exit=0
fi
write_collision_safe_evidence_strict \
  "$EVIDENCE_DIR/final-decision.txt" "$final_decision"
RECOVERY_FINISHED=true
trap - EXIT INT TERM
cleanup_secrets
if test "$final_exit" -ne 0; then
  exit "$final_exit"
fi
~~~

No original service is started after this point.

## 6. Non-destructive rollback

Rollback means stop and preserve, never overwrite or rewind in place.

1. Before original start: original PGDATA remains untouched; retain the cold
   copy/evidence and keep applications inactive.
2. Start refused or recovery failed: do not remove postmaster.pid and do not
   retry. Only if PID, postmaster-start epoch, `/proc` start ticks, executable,
   cwd, argv, PGDATA, port, and socket still equal the successful-start binding
   may the one approved stop run. After a proven safe stop, copy/hash/sync into
   the preallocated post-attempt destination. On identity ambiguity or stop
   failure, do not stop a replacement and do not copy; retain incident evidence.
3. Head/count/catalog/integrity/ACL failure: issue no corrective SQL. Take a
   readable evidence dump only if already authorized and safe, then controlled
   stop.
4. Migration failure: rely only on its PostgreSQL transaction. Quarantine and
   screen raw stdout/stderr in the secret directory, retain the exit, scan
   metadata, repeated head, and byte counts only after a clear scan; delete the
   raw files, then stop. Never retry or downgrade.
5. Dump/isolated restore failure: retain .partial/log/isolated target; do not
   reuse names and do not promote.
6. Historical fallback: new isolated target only. Never restore to original.

A stop failure is a new incident stop condition, not permission for kill,
retry, stale-PID deletion, reinitialization, or WAL surgery.

## 7. Evidence and decision

The secret-free bundle contains the stable-FD approved transcript and
hash-bound authenticated change-control export; canonical path, device,
overlap, capacity and destination gates; unit-state assertions; exact
offline/running/stopped cluster identity; synced PGDATA and full pre-start log
manifests; literal 0003 dump checks; one-start/one-stop exits; mechanically
classified protected recovery-log evidence; repeated head;
base and Phase4 counts; exact expected-vs-actual catalog; integrity/orphan/
trigger/function gates; roles/memberships/owners/database/schema/default ACLs;
the clean-build expected-catalog provenance/hash and complete all-27 source and
isolated snapshots; durably synced recovered/final dump catalogs, sizes, hashes,
and sync exits; isolated binding/restore/full comparison; optional post-attempt
preservation with an immutable exit record, verified manifest, and final sync
evidence, or no-copy incident evidence; RPO and measured RTO.

The collision-safe decision artifact is never overwritten. Its only possible
outcome class is:

- RECOVERY SUB-GATE EVIDENCE READY FOR INDEPENDENT REVIEW; or
- NO-GO — RECOVERY SUB-GATE FAILED, with an exact retained reason. If the
  preferred final-decision.txt already exists, the collision-safe failure
  artifact carries the same NO-GO class.

Failure-path decisions are explicitly best effort. Each attempt records the
decision-file and evidence-directory sync return codes in a separate
collision-safe sync-attempt artifact, and a failed sync is never silently
converted to success. The planned terminal decision is strict: its decision
file, sync record, and evidence-directory durability checks must all pass
before `RECOVERY_FINISHED` is set, traps are removed, or secrets are cleaned.

A1 itself remains **NO-GO** even after the first outcome because checksums are
disabled, PITR/WAL recovery and off-host retention are unproven, the
missed-backup alert is absent, the historical fallback has an approximately
51-hour RPO gap, and current default-ACL leakage may require a new forward
migration and a revised expected head.
