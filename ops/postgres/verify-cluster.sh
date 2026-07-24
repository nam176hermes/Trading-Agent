#!/usr/bin/env bash
set -euo pipefail

readonly host=127.0.0.1
readonly port=55432
readonly data_dir="${HOME}/.local/share/trading-agent/postgres/16/trading-agent"

test -x /usr/lib/postgresql/16/bin/postgres
test -x /usr/lib/postgresql/16/bin/initdb
test -x /usr/lib/postgresql/16/bin/pg_ctl
test "$(stat -c '%a' "$data_dir")" = 700

pg_isready -h "$host" -p "$port"

listeners=$(ss -ltnH | awk -v port=":${port}" '$4 ~ port "$" {print $4}')
test "$listeners" = "${host}:${port}"

printf 'postgres_version=%s\n' "$(/usr/lib/postgresql/16/bin/postgres --version)"
printf 'cluster_host=%s\ncluster_port=%s\n' "$host" "$port"
printf 'listener=%s\n' "$listeners"
printf 'data_dir_mode=%s\n' "$(stat -c '%a' "$data_dir")"
