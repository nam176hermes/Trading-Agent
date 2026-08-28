# Codex Tooling Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair Serena's project binding, verify the existing read-only Sentry path, and document one deterministic Codex workflow for trading-agent.

**Architecture:** Serena is repaired only through two global configuration edits: an exact project argument in Codex and a central metadata location in Serena. Sentry remains a bounded GET-only skill/script rather than a new MCP server. Repository changes are documentation-only and preserve the existing Makefile, component boundaries, paper-only gates, and remote/runtime authorization model.

**Tech Stack:** Codex MCP configuration (TOML), Serena 1.7.0 configuration (YAML), Python 3 standard library, bundled Sentry API script, Git, Markdown.

**Spec:** `docs/superpowers/specs/2026-08-27-codex-tooling-workflow-design.md`

## Global Constraints

- Exact source baseline is commit `bb176622567d10543454caddae271693a4216aa2`, tree `fa603c99335c203ecf1dd159e1faacd9d9b65f61`.
- Do not modify production source, dependencies, lockfiles, CI, formatter, type-checker, services, schedulers, databases, provider/broker access, order state, deployment, or live gates.
- Do not add a custom Sentry MCP server, plugin, Git hook, Graphify watcher, Repomix MCP, or background service.
- Sentry validation is GET-only, limit 1, environment `prod`, time range `24h`, query `is:unresolved`; never use `--no-redact` or `--include-entries`.
- Do not print or persist credentials, DSNs, PII, raw stack traces, request bodies, or unrestricted event payloads.
- Do not push, open a pull request, merge, release, deploy, or promote runtime state.
- Back up global configuration before editing and preserve exact rollback bytes plus SHA-256 evidence.
- A fresh Codex thread is required for final Serena tool-inventory verification; do not restart or terminate Codex automatically.

---

### Task 1: Repair Serena project binding and metadata placement

**Files:**
- Modify: `/mnt/c/Users/thenam/.codex/config.toml:111-114`
- Modify: `/home/thenam176/.serena/serena_config.yml:161-164`
- Create outside Git: `/tmp/codex-tooling-workflow-backup.path`
- Create outside Git: timestamped backup directory under `/tmp/codex-tooling-workflow-backup.*`
- Serena-managed output: `/home/thenam176/.serena/projects/trading-agent/`

**Interfaces:**
- Consumes: Serena executable `/home/thenam176/.local/bin/serena`, canonical project `/home/thenam176/projects/trading-agent`.
- Produces: deterministic Serena MCP command, 30-second startup timeout, central project metadata, exact rollback bundle.

- [ ] **Step 1: Record immutable pre-change state**

Run:

```bash
cd /home/thenam176/projects/trading-agent
git rev-parse HEAD
git rev-parse HEAD^{tree}
git status --short --branch
serena --version
sed -n '105,120p' /mnt/c/Users/thenam/.codex/config.toml
rg -n 'project_serena_folder_location|^projects:' /home/thenam176/.serena/serena_config.yml
test ! -e /home/thenam176/projects/trading-agent/.serena
```

Expected:

```text
HEAD bb176622567d10543454caddae271693a4216aa2
tree fa603c99335c203ecf1dd159e1faacd9d9b65f61
Serena 1.7.0
Codex args contain --project-from-cwd
startup_timeout_sec = 15.0
Serena metadata location is $projectDir/.serena
canonical checkout has no .serena path
```

Stop if HEAD/tree differs or the global configuration no longer matches these
anchors; rebase the plan instead of overwriting newer user configuration.

- [ ] **Step 2: Create exact global-config backups and checksums**

Run:

```bash
backup_dir=$(mktemp -d /tmp/codex-tooling-workflow-backup.XXXXXXXXXX)
chmod 0700 "$backup_dir"
printf '%s\n' "$backup_dir" > /tmp/codex-tooling-workflow-backup.path
chmod 0600 /tmp/codex-tooling-workflow-backup.path
cp --preserve=mode,timestamps /mnt/c/Users/thenam/.codex/config.toml "$backup_dir/codex-config.toml"
cp --preserve=mode,timestamps /home/thenam176/.serena/serena_config.yml "$backup_dir/serena-config.yml"
sha256sum "$backup_dir/codex-config.toml" "$backup_dir/serena-config.yml" > "$backup_dir/SHA256SUMS"
sha256sum --check "$backup_dir/SHA256SUMS"
```

Expected: both checksum entries print `OK`; no backup is placed in Git.

- [ ] **Step 3: Replace cwd-dependent Serena MCP arguments**

Use `apply_patch` on `/mnt/c/Users/thenam/.codex/config.toml`:

```diff
 [mcp_servers.serena]
 command = "serena"
-args = ["start-mcp-server", "--context=codex", "--project-from-cwd"]
-startup_timeout_sec = 15.0
+args = ["start-mcp-server", "--context=codex", "--project", "/home/thenam176/projects/trading-agent"]
+startup_timeout_sec = 30.0
```

Do not change another MCP entry.

- [ ] **Step 4: Move Serena metadata outside the repository**

Use `apply_patch` on `/home/thenam176/.serena/serena_config.yml`:

```diff
-project_serena_folder_location: "$projectDir/.serena"
+project_serena_folder_location: "/home/thenam176/.serena/projects/$projectFolderName"
```

Leave `language_backend`, modes, dashboard settings, tool selections, and
registered projects unchanged.

- [ ] **Step 5: Parse the edited Codex TOML and inspect the exact Serena block**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
import tomllib

path = Path('/mnt/c/Users/thenam/.codex/config.toml')
with path.open('rb') as handle:
    config = tomllib.load(handle)
serena = config['mcp_servers']['serena']
assert serena == {
    'command': 'serena',
    'args': [
        'start-mcp-server',
        '--context=codex',
        '--project',
        '/home/thenam176/projects/trading-agent',
    ],
    'startup_timeout_sec': 30.0,
}
print('SERENA_CODEX_CONFIG_PASS')
PY
```

Expected: `SERENA_CODEX_CONFIG_PASS`.

- [ ] **Step 6: Run a cold-start probe from an unrelated working directory**

Run:

```bash
probe_log=$(mktemp /tmp/serena-cold-start.XXXXXXXXXX.log)
chmod 0600 "$probe_log"
cd /tmp
timeout 20s serena start-mcp-server \
  --context=codex \
  --project /home/thenam176/projects/trading-agent \
  </dev/null >"$probe_log" 2>&1
rg -n 'Activating trading-agent at /home/thenam176/projects/trading-agent|Number of exposed tools:|Starting MCP server with .*tools' "$probe_log"
test ! -e /home/thenam176/projects/trading-agent/.serena
test -d /home/thenam176/.serena/projects/trading-agent
```

Expected: canonical project activation, a non-zero exposed-tool count, central
metadata directory present, and no repository `.serena/` path.

- [ ] **Step 7: Verify tool names in the Serena log**

Run:

```bash
latest_serena_log=$(find /home/thenam176/.serena/logs -type f -name 'mcp_*.txt' -printf '%T@ %p\n' | sort -nr | head -n 1 | cut -d' ' -f2-)
for tool in initial_instructions find_symbol find_referencing_symbols get_symbols_overview; do
  rg -q "$tool" "$latest_serena_log"
done
printf '%s\n' SERENA_DIRECT_PASS
```

Expected: `SERENA_DIRECT_PASS`.

- [ ] **Step 8: Roll back immediately if any Serena validation fails**

Only on failure, run:

```bash
backup_dir=$(cat /tmp/codex-tooling-workflow-backup.path)
sha256sum --check "$backup_dir/SHA256SUMS"
cp --preserve=mode,timestamps "$backup_dir/codex-config.toml" /mnt/c/Users/thenam/.codex/config.toml
cp --preserve=mode,timestamps "$backup_dir/serena-config.yml" /home/thenam176/.serena/serena_config.yml
test ! -e /home/thenam176/projects/trading-agent/.serena
```

Do not delete central Serena metadata unless the probe created it and rollback
requires cleanup. If cleanup is necessary, first resolve the exact directory
from the edited configuration and remove only
`/home/thenam176/.serena/projects/trading-agent`.

### Task 2: Verify the existing read-only Sentry diagnostic path

**Files:**
- Read only: `/mnt/c/Users/thenam/.codex/plugins/cache/openai-curated-remote/sentry/0.1.2/skills/sentry/scripts/sentry_api.py`
- Create outside Git: mode-0600 temporary JSON under `/tmp/sentry-readonly-smoke.*.json`

**Interfaces:**
- Consumes: locally configured `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT`, optional `SENTRY_BASE_URL`.
- Produces: bounded authentication/API smoke status without event bodies or production mutation.

- [ ] **Step 1: Verify required environment names without printing values**

Run:

```bash
for name in SENTRY_AUTH_TOKEN SENTRY_ORG SENTRY_PROJECT; do
  test -n "$(printenv "$name")" || { printf '%s\n' "$name is not set" >&2; exit 1; }
  printf '%s\n' "$name=set"
done
```

Expected: three `name=set` lines; no values printed.

- [ ] **Step 2: Confirm the helper advertises read-only commands**

Run:

```bash
python3 /mnt/c/Users/thenam/.codex/plugins/cache/openai-curated-remote/sentry/0.1.2/skills/sentry/scripts/sentry_api.py --help
```

Expected: only `list-issues`, `issue-detail`, `issue-events`, and
`event-detail`; do not pass `--no-redact` or `--include-entries`.

- [ ] **Step 3: Execute one bounded redacted GET smoke**

Run:

```bash
sentry_output=$(mktemp /tmp/sentry-readonly-smoke.XXXXXXXXXX.json)
chmod 0600 "$sentry_output"
printf '%s\n' "$sentry_output" > /tmp/sentry-readonly-smoke.path
chmod 0600 /tmp/sentry-readonly-smoke.path
python3 /mnt/c/Users/thenam/.codex/plugins/cache/openai-curated-remote/sentry/0.1.2/skills/sentry/scripts/sentry_api.py \
  list-issues \
  --environment prod \
  --time-range 24h \
  --limit 1 \
  --query 'is:unresolved' \
  >"$sentry_output"
python3 - "$sentry_output" <<'PY'
import json
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
assert isinstance(data, list)
assert len(data) <= 1
print(f'SENTRY_READ_ONLY_PASS count={len(data)}')
PY
```

Expected: `SENTRY_READ_ONLY_PASS count=0` or `count=1`. Do not display the
temporary JSON content.

- [ ] **Step 4: Remove the temporary Sentry response after validation**

Run:

```bash
sentry_output=$(cat /tmp/sentry-readonly-smoke.path)
rm -f -- "$sentry_output"
rm -f -- /tmp/sentry-readonly-smoke.path
test ! -e "$sentry_output"
```

Expected: the mode-0600 response file no longer exists. A 401/403 is reported
as `SENTRY_AUTH_SCOPE_BLOCKED`; do not request or apply write scopes.

### Task 3: Document the routed Codex development workflow

**Files:**
- Modify: `docs/development-workflow.md:1-28`

**Interfaces:**
- Consumes: existing short-loop commands and the approved design's routing matrix.
- Produces: one canonical, source-controlled workflow for future Codex tasks.

- [ ] **Step 1: Append the exact task contract and default feature lane**

After the existing workspace-doctor paragraph, add:

````markdown

## Codex task contract

Start every non-trivial task by recording the mode, goal, accepted base
commit/tree, allowed paths, forbidden actions, acceptance checks, and whether
any remote or runtime authority exists. Verify the working directory, Git
status, instruction files, and paper-only gates before changing source.

The default feature lane is:

```text
task contract
  -> Codebase Memory architecture and blast radius
  -> Serena symbols, callers, and implementations
  -> rg plus source and tests as ground truth
  -> approved design or bounded change description
  -> focused failing test
  -> minimum implementation
  -> focused test, static checks, and contract checks
  -> change-impact review
  -> canonical local gates
  -> candidate SHA/tree handoff
```
````

- [ ] **Step 2: Append the exact tool-routing table**

Add immediately after the default lane:

```markdown

## Tool routing

| Need | Primary tool | Required proof |
| --- | --- | --- |
| Architecture and blast radius | Codebase Memory | `rg` and source for coverage gaps |
| Symbols, callers, implementations, rename | Serena | source diff and tests |
| Literal text, config, docs, shell, generated files | `rg` and filesystem | exact file content |
| Current library or SDK behavior | installed docs, then Context7 | pinned project version |
| External GitHub repository orientation | DeepWiki | exact clone/commit before relying on code |
| Dashboard behavior | Playwright | tests plus direct browser smoke |
| Runtime incident diagnosis | Sentry read-only skill | local reproduction and regression test |
| Offline architecture artifact | Graphify | source/tests; never authority evidence |
| Scoped audit or reviewer handoff | Repomix CLI | explicit includes/excludes and token budget |
| Sensitive diff | Codex Security or independent review | exact candidate SHA/tree |

Use Graphify and Repomix on demand only. Do not enable watchers, hooks,
provider-backed extraction, database extraction, or persistent MCP servers as
part of the normal feature lane.
```

- [ ] **Step 3: Append escalation and completion rules**

Add:

```markdown

## Escalation and completion

- UI behavior requires Playwright browser verification.
- Auth, secrets, subprocess, filesystem authority, order intent, risk,
  artifacts, or release code requires security review.
- Cross-component contracts or durable authority changes require an approved
  written spec and plan.
- Hard multi-hypothesis defects require reproducible evidence and a frozen
  root-cause ledger before source edits.
- Sentry observations and generated graphs are diagnostic inputs, not source,
  release, remote, or runtime authority.
- Push, pull request, merge, deployment, database/service changes, providers,
  brokers, and live trading always require separate operator authorization.
```

- [ ] **Step 4: Validate the documentation diff**

Run:

```bash
git diff --check
rg -n 'Codex task contract|Codebase Memory|Serena|Context7|DeepWiki|Playwright|Sentry|Graphify|Repomix|separate operator authorization' docs/development-workflow.md
git diff -- docs/development-workflow.md
```

Expected: no whitespace errors; all routed tools and authority boundary are
present; existing development commands remain byte-for-byte unchanged.

- [ ] **Step 5: Commit the workflow documentation**

Run:

```bash
git add docs/development-workflow.md
git diff --cached --check
git commit -m "docs: route Codex development tooling"
```

Expected: one documentation-only commit.

### Task 4: Final verification and handoff

**Files:**
- Read only: global Codex/Serena configuration and repository documentation.

**Interfaces:**
- Consumes: Tasks 1-3 results.
- Produces: exact candidate SHA/tree, direct Serena result, Sentry smoke result, and fresh-thread restart gate.

- [ ] **Step 1: Verify the repository worktree contains only approved documentation history**

Run:

```bash
git status --short --branch
git log --oneline bb176622567d10543454caddae271693a4216aa2..HEAD
git diff --stat bb176622567d10543454caddae271693a4216aa2..HEAD
git diff --name-only bb176622567d10543454caddae271693a4216aa2..HEAD
```

Expected paths:

```text
docs/superpowers/specs/2026-08-27-codex-tooling-workflow-design.md
docs/superpowers/plans/2026-08-27-codex-tooling-workflow.md
docs/development-workflow.md
```

- [ ] **Step 2: Run the smallest relevant repository checks**

Run:

```bash
git diff --check bb176622567d10543454caddae271693a4216aa2..HEAD
uv run python scripts/dev.py doctor
uv run python scripts/generate_contracts.py --check
```

Expected: both commands pass. Do not run `make test-all` because no production
or generated contract source changed.

- [ ] **Step 3: Record exact candidate identity and configuration checksums**

Run:

```bash
git rev-parse HEAD
git rev-parse HEAD^{tree}
sha256sum /mnt/c/Users/thenam/.codex/config.toml /home/thenam176/.serena/serena_config.yml
backup_dir=$(cat /tmp/codex-tooling-workflow-backup.path)
cat "$backup_dir/SHA256SUMS"
```

Report hashes without printing configuration contents or environment values.

- [ ] **Step 4: Stop for a fresh Codex thread**

Report:

```text
SERENA_DIRECT: PASS
SENTRY_READ_ONLY: PASS | AUTH_SCOPE_BLOCKED
WORKFLOW_DOCS: PASS
SERENA_FRESH_THREAD: PENDING_RESTART
REMOTE/RUNTIME MUTATION: NONE
```

Do not restart Codex automatically. Ask the operator to open a fresh thread
rooted at `/home/thenam176/projects/trading-agent`.

- [ ] **Step 5: In the fresh thread, verify Serena before any coding task**

The first Serena call must be `initial_instructions`. Then call
`get_current_config` and one read-only symbol query such as
`get_symbols_overview` for `services/sentry.py`.

Expected:

```text
Serena tools are non-empty.
Active project is /home/thenam176/projects/trading-agent.
Symbol overview returns configure_sentry.
No .serena directory exists in the repository.
```

Only after this step report `SERENA_FRESH_THREAD: PASS` and mark the repair
complete. This verification grants no push, merge, deployment, provider,
broker, database, service, or live-trading authority.
