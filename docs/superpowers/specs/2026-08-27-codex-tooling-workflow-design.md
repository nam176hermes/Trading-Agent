# Codex tooling workflow design

Status: operator-approved architectural design; implementation not yet authorized.

Baseline commit: `bb176622567d10543454caddae271693a4216aa2`
Baseline tree: `fa603c99335c203ecf1dd159e1faacd9d9b65f61`

## Problem

The trading-agent repository has strong local validation and source/runtime
separation, but its Codex tooling is not yet routed deterministically:

- Serena is installed and enabled, but the Codex thread received an empty tool
  inventory after Serena started from an unrelated temporary working directory.
- Serena's default project metadata location would create an untracked
  `.serena/` directory in the canonical checkout.
- Sentry is available through a read-only skill and configured local
  credentials, but it is easy to misclassify the absence of Sentry MCP tools as
  an installation failure.
- Context7, DeepWiki, Codebase Memory, Graphify, Repomix, Playwright, Sentry,
  and source-local tools overlap unless each has a narrow workflow role.

The result is avoidable tool noise, occasional workspace misbinding, and a risk
that generated documentation or runtime observations are mistaken for source
or release authority.

## Goals

1. Make Serena start against the canonical trading-agent root independently of
   the Codex process working directory.
2. Keep Serena metadata outside the Git checkout.
3. Verify Sentry's existing read-only diagnostic path without creating another
   server or production mutation capability.
4. Document one default Codex workflow with explicit escalation gates.
5. Preserve paper-only validation and the existing remote/runtime approval
   boundaries.

## Non-goals

- No custom Sentry MCP server.
- No provider, broker, exchange, account, order, PostgreSQL, service, scheduler,
  deployment, or live-trading access.
- No production code, dependency, lockfile, CI, formatter, or type-checker
  changes.
- No automatic Git hook, Graphify watcher, Repomix MCP, background service, or
  additional plugin installation.
- No push, pull request, merge, release, or runtime promotion.

## Decision 1: deterministic Serena binding

The global Codex MCP entry will use the exact canonical project path rather
than `--project-from-cwd`:

```toml
[mcp_servers.serena]
command = "serena"
args = [
  "start-mcp-server",
  "--context=codex",
  "--project",
  "/home/thenam176/projects/trading-agent",
]
startup_timeout_sec = 30.0
```

Serena's global metadata location will be moved from
`$projectDir/.serena` to:

```yaml
project_serena_folder_location: "/home/thenam176/.serena/projects/$projectFolderName"
```

The existing LSP backend, `interactive` and `editing` modes, dashboard binding,
and tool set remain unchanged. The change fixes project selection and metadata
placement only.

On the first cold start, Serena may register the canonical project in its own
global configuration. That registration is acceptable because it is global
tool metadata, not repository content. No `.serena/` directory may remain in
the repository.

### Serena verification

Verification has two layers:

1. A direct cold-start probe must report the canonical root and a non-empty
   exposed tool set including `initial_instructions`, `find_symbol`,
   `find_referencing_symbols`, and `get_symbols_overview`.
2. A fresh Codex thread rooted at the canonical checkout must expose
   `mcp__serena__*`; its first Serena call must be `initial_instructions`, then
   `get_current_config` must report the canonical project.

The current thread's tool inventory is immutable after startup, so a fresh
thread is part of verification rather than an implementation workaround.

## Decision 2: keep Sentry read-only and skill-driven

Sentry remains a bundled skill plus deterministic API script. It will not be
wrapped in a custom MCP server.

The validation smoke uses the configured local `SENTRY_AUTH_TOKEN`,
`SENTRY_ORG`, and `SENTRY_PROJECT` and performs one bounded GET-only query:

```text
list unresolved issues, environment=prod, time range=24h, limit=1
```

The smoke proves authentication, endpoint access, response parsing, and local
configuration. It must not print credentials, email addresses, IP addresses,
raw stack traces, request bodies, or unrestricted event payloads. A 401/403 is
reported as an authentication/scope blocker; it is not repaired by requesting
broader write scopes.

The existing application integration remains unchanged: runtime initialization
requires `SENTRY_DSN` and disables default PII. Sentry observations are
diagnostic input only and never establish source correctness, task closure,
release readiness, or runtime authority.

## Decision 3: one routed Codex workflow

`docs/development-workflow.md` will retain its current commands and add a
Codex-specific routing section.

### Default feature lane

```text
Task contract
  -> exact cwd/HEAD/tree/status/instructions
  -> Codebase Memory architecture and blast radius
  -> Serena symbol/caller navigation
  -> rg/source/tests as ground truth
  -> approved design or bounded change description
  -> focused RED test
  -> minimal implementation
  -> focused test + static + contract checks
  -> change-impact review
  -> canonical local gates
  -> candidate SHA/tree handoff
```

### Tool routing

| Need | Primary tool | Required fallback or proof |
| --- | --- | --- |
| Repository architecture and blast radius | Codebase Memory | `rg` and source for coverage gaps |
| Symbol definitions, callers, implementations, rename | Serena | source diff and tests |
| Literal text, config, docs, shell, generated files | `rg` and filesystem | exact file content |
| Current library or SDK behavior | local installed docs, then Context7 | pinned project version |
| External GitHub repository orientation | DeepWiki | exact clone/commit before relying on code |
| Dashboard workflow | Playwright | unit/integration tests plus browser smoke |
| Runtime incident diagnosis | Sentry read-only skill | local reproduction and regression test |
| Offline architecture artifact | Graphify | source/tests; never authority evidence |
| Scoped audit or reviewer handoff | Repomix CLI | explicit includes/excludes and token budget |
| Sensitive diff | Codex Security or independent reviewer | exact candidate SHA/tree |

### Escalation triggers

- UI behavior activates Playwright browser verification.
- Auth, secrets, subprocess, filesystem authority, order intent, risk,
  artifacts, or release code activates security review.
- Cross-component contracts or durable authority changes require a written
  spec and plan.
- Hard multi-hypothesis defects use systematic debugging and a frozen
  root-cause ledger before source edits.
- Graphify and Repomix remain on-demand CLIs; neither runs continuously.

## Files and state affected

Global configuration:

- `/mnt/c/Users/thenam/.codex/config.toml`
- `/home/thenam176/.serena/serena_config.yml`
- Serena-managed metadata under `/home/thenam176/.serena/projects/`

Repository documentation:

- `docs/development-workflow.md`
- this design specification
- the subsequent implementation plan

No production source file is in scope.

## Failure handling and rollback

Before changing a global configuration file, save a task-owned timestamped
copy outside the repository and record its SHA-256. If Serena fails validation:

1. restore the exact saved Codex and Serena configuration bytes;
2. stop any task-owned Serena process;
3. remove only task-owned central Serena metadata created by the failed probe;
4. confirm the canonical repository has no `.serena/` artifact;
5. report the failure without altering another MCP server.

Sentry validation is read-only and requires no data rollback. Authentication or
scope failure leaves the existing environment untouched.

## Validation matrix

| Gate | Expected result |
| --- | --- |
| Global config parse | Codex and Serena configuration load without error |
| Serena cold start | Canonical root, non-empty tool set, no repo `.serena/` |
| Fresh-thread Serena | `initial_instructions` and symbolic tools callable |
| Sentry smoke | One bounded GET succeeds or returns a precise read-only blocker |
| Secret hygiene | No token, DSN, org payload, PII, or stack trace in output/files |
| Documentation | Commands and routing match existing repository boundaries |
| Repository status | Only approved documentation paths differ in the worktree |

Because no production code changes, full `make test-all` is not required for
the configuration repair itself. The smallest relevant documentation and
configuration checks are sufficient; broader gates remain mandatory for later
source features.

## Definition of done

- Serena is deterministically bound to the canonical checkout and stores no
  metadata inside it.
- A fresh Codex thread exposes and successfully calls Serena symbolic tools.
- The Sentry read-only API path is verified without leaking sensitive data or
  adding write authority.
- The development workflow documents the tool-routing and escalation rules.
- Exact configuration backups, validation evidence, and rollback instructions
  are reported.
- No production, remote, database, service, provider, broker, or live state is
  mutated.
