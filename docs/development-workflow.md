# Development workflow

The canonical release and CI entry points remain in the protected `Makefile`.
Use `make test-all` for completion evidence. Use the commands below for the
short feedback loop while editing:

```bash
uv run python scripts/dev.py doctor
uv run python scripts/dev.py static
uv run python scripts/dev.py test tests/path/to/test_file.py
uv run python scripts/dev.py test --native-custody tests/path/to/custody_test.py
uv run python scripts/dev.py test tests/path/to/test_file.py -k focused_case
uv run python scripts/dev.py test-debug tests/path/to/test_file.py -k focused_case
uv run python scripts/generate_contracts.py --check
```

`test` captures successful output and prints failure context, which keeps local
logs and agent context small. `test-debug` deliberately restores live, verbose
output for diagnosis. Both commands create a private Linux-native temporary
directory and clean it after the child process exits. `--native-custody` builds
and validates the native extension in that private directory, or validates the
complete caller-supplied path/digest pair. SIGTERM terminates the owned test
process group before cleanup.

`static` runs pinned Ruff and Basedpyright versions over the root production
packages and the legacy backend. Baselines keep the initial adoption bounded;
error-level diagnostics outside the baseline fail the command. Zero reported
errors is not zero typing debt. Both component results are reported even when
one fails. The same static command runs in CI.

After a verified refactor, remove only obsolete entries with:

```bash
uv run python scripts/prune_python_baselines.py core
uv run python scripts/prune_python_baselines.py legacy
uv run python scripts/dev.py static
```

The pruning command intersects the previous and current diagnostic multisets;
it cannot add debt or collapse distinct occurrences of the same diagnostic.
Review its diff as source. Do not use unbounded baseline regeneration to make
a gate pass. The legacy component keeps its separate dependency and test graph.

Hosted CI runs backend and dashboard tests through the governance topology
owner; the common prerequisite target does not run them a second time.
`make test-all` retains its local component checks.

The workspace doctor verifies repository location, Git topology, temporary
storage, generated contracts, and the paper-only execution boundary. It does
not authorize deployment, a provider connection, or live/broker execution.

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
