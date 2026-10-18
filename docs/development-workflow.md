# Development workflow

Root and nested `AGENTS.md` govern this workflow. Read only the section needed
for the task. Historical specs, prompts, receipts, and task matrices describe
their accepted scope; they are not startup instructions or new authority.

[North Star V4.0](NORTH-STAR-V4.md) is the canonical product direction, adopted
on 2026-10-15. Start new product planning from V4 and its NS milestones; use
section 15 for precedence and retained constraints. Existing accepted task
plans and P3 qualification contracts retain their exact scope. Roadmap adoption
does not authorize implementation, deployment or live activation by itself.

## Local development, publication by phase

The operator approved the
[phase workflow](superpowers/specs/2026-09-18-local-phase-workflow.md) on
2026-09-18. Build and review a phase locally, freeze and qualify its candidate,
then publish one main batch for hosted verification and external AI review.
Resolve findings before finalizing the next phase plan. Corrective publication
batches are allowed when review finds a material defect.

Use small local changes and authorized local commits for recovery; do not push
after every change. Run focused affected checks during implementation, integration
checks at milestones, and the applicable full gates at candidate freeze. Reuse
evidence only when its source, policy, inputs, environment and authority remain
valid. A failure must retain its original result even after a later repair.

Keep local source completion, local qualification, economic/product acceptance,
publication and external review as separate verdicts. P3 pipeline completion
does not prove an alpha passed economic acceptance. Preserve the North Star
phase prerequisites and holdout rules.

The authenticated local qualification lane needs the source changes in the
[implementation plan](superpowers/plans/2026-09-18-local-phase-workflow.md).
It is not active yet. Existing formal gates remain enforced; their HELD status
does not require a push before continuing ordinary local development. Never
fake GitHub environment variables or issue a PASS receipt from this policy.

Push/PR/merge and protected host activation still require separate authority.
Do not change CI triggers, branch protection or research acceptance thresholds
merely to reduce development feedback time.

## Bootstrap and task classes

Record one compact contract: outcome, component, accepted HEAD/tree and dirty
paths, allowed changes, prohibited actions, risk, acceptance checks, and authority.
Inspect the active instruction hierarchy, affected implementation, and closest
tests. The [README project map](../README.md#project-map) provides orientation;
load relevant ADRs only when a boundary is involved. Do not scan all docs,
dependencies, Git history, or tests at startup. Use the workspace doctor when
the environment is new or suspect, not as a repeated status command.

These are **workflow classes**, not the machine-enforced P1-H change classes in
`scripts/classify_p1_h_impact.py`. They cannot override that classifier or CI.
Classify by consequence, not line count; a one-line live-gate edit is critical.

| Class | Repository examples | Process |
| --- | --- | --- |
| A: mechanical | Prose, labels, formatting with no semantic effect | Inspect → edit → targeted verification; no agent or separate plan |
| B: bounded engineering | Isolated pure transformation or UI presentation behavior | Trace → short seam/files/behavior/checks description → test-first implementation → component checks |
| C: complex | Cross-component contracts, architecture, multi-system defects | Reconnaissance → approved spec/plan → review where justified → freeze → implement → integration verification |
| D: critical | Money/risk, order state, auth/credentials, operator authority, persistence, migration, concurrency, idempotency, checkpoint/recovery, kill switches | Explicit invariants/failure matrix, approved authority design, security review and independent review where valuable, full applicable verification; no quota shortcuts |

Choose one applicable implementation/review workflow within the selected lane;
do not stack Code Work and the full Superpowers lifecycle for the same stages.
A bounded task's
contract and change description can be one message. Reuse an accepted plan;
revise it only for new evidence or scope. Complex architecture and durable
authority still require an approved written spec and implementation plan.
User authorization persists; do not ask again for the same authorized work.

## Model routing

Recommendations are selection guidance, not runtime configuration or permission
to override a selected model. Verify availability in the current host/model
selector; preserve operator choices. Do not create another thread or agent merely
to switch models. Within a session, use tools for deterministic work and reuse
context; route the next task at its start.

Default to the operator's Astra High selection; no automatic downshift for routine
work. High effort does not require extra agents, broader tests or longer plans.
Desktop configuration and an existing thread's model/effort are
different inputs: inspect runtime metadata before attributing behavior to a
default. Editing instructions or config does not switch the current turn.
Use only models available in the current selector; no per-model quota exchange
rate or measured savings percentage has been established.

| Task | Suggested model | Reasoning | Reason |
| --- | --- | --- | --- |
| A/B edit, exact search, focused checks | Current selection; Astra High default | Preserve selected effort | Keep scope and output small; tools do deterministic work |
| C architecture or ambiguous defect | Current selection; Astra High default | high | Resolve the named cross-system uncertainty |
| D money, security, persistence, concurrency or recovery | Current selection; Astra High default | high | Preserve adversarial checks and explicit invariants |
| Independent review when explicitly requested | Operator-selected available model | Match the risk | Independent verdict from source and evidence |

Use `xhigh` or `max` only with a named unresolved risk or failed focused
investigation. `ultra` is not a repository default and never grants delegation
authority. Once the cause/design is settled, route mechanical follow-through
at the next supported selection point; do not claim an in-turn model change.
Being inside a trading repository does not make every documentation or isolated
UI edit critical. Classify the changed behavior and its affected dependencies.

## Implementation limits

Complete the requested behavior using an existing path, stdlib or installed
dependency before adding a helper or abstraction. A new layer needs a concrete
caller and a demonstrated gap; a second hypothetical use case is insufficient.
Extend an existing regression test when it covers the failure. New cases should
test distinct behavior or invariants, not repeat the implementation or inflate
the test count. Preserve required adversarial/concurrency/recovery matrices.

Once acceptance checks pass, hand off. Do not add speculative hardening, general
frameworks, cleanup or another review round to prolong a completed task. Record
out-of-scope findings in the existing task/report without implementing them.

## Tools and context reuse

| Need | Tool / evidence |
| --- | --- |
| Architecture/blast radius | Codebase Memory, then relevant source confirmation; index only if missing/stale |
| Symbols/callers/rename | Serena after initial instructions and activation, when available; source/tests verify results |
| Literal text, config, docs, shell | Scoped `rg`, known paths, bounded line ranges |
| Version-sensitive APIs | Installed docs, then Context7/official docs matched to the pinned version |
| Dashboard behavior | Direct Playwright smoke plus tests; local isolated test server only |
| Sensitive diff | Codex Security; unavailable scope is NOT REVIEWED/held; independent review separately labeled |
| External repository/incident | DeepWiki or read-only Sentry when relevant; reproduce against exact source |
| Offline map/packaged handoff | Graphify/Repomix on demand with explicit scope/output budget; never authority evidence |

Do not force every task through graph → symbols → text when one tool answers
the question. If a configured tool is unavailable, use the supported fallback
and record the limitation once. Do not enable watchers, hooks, provider-backed
extraction, database extraction, or persistent servers for routine source work.

### Project tool and skill selection

The project `.codex/config.toml` sets defaults for **new trusted-project sessions**.
It keeps the current machine's MCP installations and credentials outside Git.
For the filtered Trading-Agent CLI session, start from this checkout with:

```bash
python3 scripts/codex_project.py
```

The launcher applies only this project's explicit tool exclusions and preserves
user-level skill denials. Codex CLI 0.147.0 was observed to read project skill
settings but still include those skills in its prompt; the launcher passes them
as CLI overrides, whose effect was verified in the actual rendered prompt.
It does not edit global config or uninstall anything. Existing desktop sessions
and workspace-managed plugin installations are not changed by this command.
The exclusions use this host's installed skill paths; recheck them after a skill
upgrade or move. Do not claim a cached plugin was removed from the desktop merely
because its project configuration says disabled.

Serena must activate the current checkout (`--project-from-cwd`), never a fixed
main path while editing a worktree. Verify the active root before trusting symbols.
Configured, callable, exercised and reviewed are different states; record only
the state demonstrated in the current session. Check Codebase Memory coverage
and source freshness before citing its graph; a graph edge is a navigation hint.

| Task | Load when needed | Avoid by default |
| --- | --- | --- |
| Bounded implementation | Code Work **or** the relevant Superpowers stage; existing repo tests | A second planner/executor/reviewer lifecycle |
| Independent audit/review | Code Verification; Repo Researcher for architecture | Calling a self-review independent, automatic subagents |
| Authority/security changes | Codex Security scoped review and negative tests | Treating missing review as PASS |
| Decimal, serialization, canonicalization or provenance invariants | `property-based-testing`; existing Hypothesis strategies and test wrapper | A new test library, tautologies, broad `assume()` filtering, automatic effort changes |
| Failing GitHub Actions checks | `gh-fix-ci` for the exact PR/head/run, then the authorized fix | Re-running unchanged HELD gates, treating local results as hosted proof |
| PR review comments | `gh-address-comments` for explicitly selected scope, with the collection checks below | Auto-posting replies, resolving threads, or claiming an incomplete fetch is exhaustive |
| Dashboard behavior | Installed Next.js guide, Playwright; React skill for a real React issue | New app builders and multiple design/style skills |
| Python/data implementation | Installed APIs; Context7 for an unresolved version detail | Scientific/ML skills merely because the repo trades |
| P4–P8 experiment | Named scientific/forecasting skill after an accepted experiment | Loading optional research stacks for Core work |
| External code reference | DeepWiki on demand; pinned official source | Repeated whole-repository dumps |
| Reports/visuals | The matching document/plotting skill when an artifact is requested | Slide/template/LaTeX plugins during routine source work |

For normal implementation, prefer Code Work as the single workflow; choose a
Superpowers stage for a concrete debugging/design gap or an explicit request.
Ponytail governs simplicity, not the removal of required checks. Repository rules
and the operator's selection take precedence over a skill's `effort` hint,
automatic delegation, unconditional planning or elevated-permission instructions.
Reuse existing authorization; preserve separate approval for remote writes.
`planning-contract` is for requested planning or an unresolved authority design,
not an extra approval loop around an already accepted implementation plan.

Hypothesis is already pinned in the root dev group. Start with the existing
[Decimal properties](../tests/domain/test_decimal_properties.py),
[ledger replay](../tests/event_ledger/test_replay.py) or the owning component's
closest test; load only the needed property-testing reference.

For PR evidence, bind collection to the destination repository, PR number and
current head SHA. The approved shared `gh-address-comments` patch fixes fork
selection and unequal pagination; five offline checks passed on the installed
file. Threads over 100 comments explicitly fail until all nested pages are fetched
with read-only queries. This is not live GitHub validation. Check IDs, cursors and
completeness before a review-closure claim; no reply or resolution is implied.

Keep relevant scientific/ML and artifact skills task-specific. The project
excludes unrelated life-science/clinical/lab/physical-science skills, marketing
styles, personal profiling, redundant web/output workflows and the duplicate
user PDF skill. It also excludes the installed skills of Canva, CircleCI,
Hyperframes, Supabase and Android testing. Core engineering, quantitative
libraries, optional forecasting, document/PDF reading and host tools remain.
These are skill exclusions, not deletion of libraries or a blanket science ban.
A smaller catalog is not a measured quality or quota improvement.

The [tooling assessment](audits/skills-plugins-mcp-2026-09-26.md) records source
evidence, callable/configured differences, shared-tool defects and pending fixes.
Consult it for those issues, not as mandatory startup context.

DeepWiki, Sequential Thinking and four artifact plugins are disabled in the project defaults, not
uninstalled globally. Re-enable the specific capability for a task that needs it.
Codebase Memory, Serena, Context7, Playwright and read-only GitHub remain the
engineering tool set when available. Workspace-managed connector availability
can differ from local plugin configuration; do not claim every unused skill was
unloaded. The current skill catalog size is not a measured token or quota cost.

Claude imports the canonical root rules from `CLAUDE.md`; path-scoped rules point
to the two component `AGENTS.md` files. Keep one owner for each rule. Claude
permission settings (`.claude/settings.json`) are operator-local and not committed;
the root "Ask first" rules apply regardless of any permission mode. Permission
patterns are an extra guard, not an OS sandbox or proof that arbitrary test code
is safe. Session/user/managed overrides must be inspected separately.

Keep the feedback loop explicit: inspect → reproduce → smallest fix → focused
check → applicable gates → evidence. Do not attach full suites to SessionStart,
PostToolUse or Stop hooks, or use a hook to retry HELD qualification indefinitely.
Run the fast harness regression after changing agent configuration:

```bash
uv run python scripts/dev.py test tests/foundation/test_agent_harness.py tests/foundation/test_dev_commands.py
```

Batch independent reads/searches. Keep mutations, dependent commands, approvals,
and checks sharing caches, ports, databases, or output paths sequential.
Read unchanged files once per needed scope; re-read for changed bytes, omitted
context, contradictory evidence, or independent safety review. Preserve enough
surrounding code to trace all callers and failure paths.

Keep a small in-session evidence ledger:
`path/range or command | revision/diff/environment | finding/result | invalidation`.
Refresh Git identity after external edits, integration, before handoff or an
authority action; not at every search. Do not reuse a test if its transitive
inputs, dependencies, fixtures, environment, or required authority have changed.
Official receipts keep their stronger source-binding rules, including docs changes.

Bound output at the command, not just the tool response limit. Use filenames
first, then relevant ranges; save large diagnostics privately and expose
failures, counts, exit status, and log path. Do not truncate away failures.
After truncation, narrow the request instead of replaying the same dump.
Discover tools by exact capability, not by listing the entire tool catalog.

For long jobs, retain the process ID and wait for completion/output; do not
restart the command. Prefer event-driven waits or 30–60 second waits, respecting
tool limits and user responsiveness. Back off unchanged polls. Tool timeouts
are not test failures; inspect the running process before retrying.

## Delegation and handoff

No automatic delegation. Explicit operator direction is required. Normally use
one main agent and zero to two focused subagents; exceed two only for explicitly
approved independent workstreams. Configured capacity is not a target.
Do not delegate mechanical work, serial dependencies, or already-loaded work
that would repeat reconnaissance. Reuse a reviewer for a related delta.

Delegate independent hypotheses, isolated modules, or a safety review whose
separation materially improves confidence. One writer per surface; parent owns
integration and final verification. Share common facts once. Use bounded context
instead of full history when sufficient. Reviewers must inspect relevant primary
evidence and choose their verdict independently; do not supply the author's
conclusion as the expected answer. No automatic reviewer-of-reviewer.

```text
TASK / RISK: independently answerable question; workflow class
SCOPE / FILES: allowed paths and exact base/candidate/dirty state
CONSTRAINTS: authority, invariants, prohibited actions, write owner
KNOWN FACTS: verified paths/ranges and environment facts
DECISIONS: accepted contract/rationale, not an expected review verdict
OPEN QUESTIONS: gaps this worker owns
CHECKS: executed commands, scope, result, environment, invalidation
EXPECTED OUTPUT: finding | file:line | evidence | impact | smallest action
DO NOT RE-READ: unchanged orientation already summarized; safety evidence exempt
STOP: scope/authority boundary, time budget, escalation condition
```

Return blockers and actionable evidence, not whole-file summaries. Run a
correctness/specification review and a separate simplicity check; separate
questions do not require separate agents. Reopen review for changed risk or
an unresolved finding, not to accumulate agreement.

## Feedback commands and test escalation

The protected Makefile remains authoritative. Existing root feedback commands:

```bash
uv run python scripts/dev.py doctor
uv run python scripts/dev.py static
uv run python scripts/dev.py test tests/path/to/test_file.py
uv run python scripts/dev.py test --native-custody tests/path/to/custody_test.py
uv run python scripts/dev.py test tests/path/to/test_file.py -k focused_case
uv run python scripts/dev.py test-debug tests/path/to/test_file.py -k focused_case
uv run python scripts/generate_contracts.py --check
```

Replace the example path with a discovered test. `test` explicitly captures
output despite root pytest's `-s`; `test-debug` enables verbose live diagnostics.
Both own private Linux-native temporary storage and cleanup. Neither replaces
native capability, skip-governance, coverage, host, or runtime qualification.
`doctor` checks generated-file presence, not content freshness; generation
`--check` proves freshness for that generator, not all `make check-contracts` gates.

`static` runs pinned Ruff on root production paths and Basedpyright against
configured root and legacy scopes. Scope and reviewed baselines limit what a
pass proves. Do not broaden, suppress, or regenerate baselines to pass.
Run dependency commands only in the owning component when required; do not
reinstall unchanged dependencies as reconnaissance.

`--native-custody` builds and validates the native extension in that private
directory, or validates the complete caller-supplied path/digest pair. SIGTERM
terminates the owned test process group before cleanup.
Place this harness option **before** the test path; a trailing option is rejected
before pytest starts. This prevents a misplaced option from becoming a pytest
argument and obscuring the missing-custody diagnosis.

Error-level diagnostics outside the baseline fail `static`. Zero reported errors
is not zero typing debt. Both component results are reported even when one fails.
The same static command runs in CI.

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

| Change / claim | Required progression |
| --- | --- |
| Docs only, no behavior/generated inputs | Diff, links/commands and safety-instruction review; exercise a helper only if its documented invocation changed or is uncertain; no automatic doctor/static/product suite |
| B root behavior | Failing focused check → fix → related tests → affected subsystem/static checks; contract checks if producers/consumers changed |
| Dashboard source | Focused feedback if supported, then nested AGENTS' `npm test`, typecheck, lint, build; direct Playwright for browser behavior. `npm test` runs the entire inventory; do not assume Jest-style file filtering |
| Legacy behavior | Closest tests in its own uv environment, then complete component tests and applicable static checks |
| C/D, shared infrastructure, schema/migrations, dependencies, security, concurrency, execution/recovery/persistence, or broad source completion | During edits: related negative/adversarial tests and affected components. At candidate freeze: `make test-all` and `make build-dashboard`, plus applicable security, coverage/skip governance, integration and authorized native/runtime gates |
| Source qualification / release | Canonical local gates and genuine hosted `make ci-portable NONINTERACTIVE=1`; `make audit-release` only on a clean release candidate; required authority proofs remain separate |

At candidate freeze, prepare `make build-dashboard` before `make test-all` when
headless tests need a production build. The aggregate already includes audit,
contract, backend and dashboard checks; do not rerun covered checks without
changed inputs or a distinct evidence need. Hosted
`make ci-portable NONINTERACTIVE=1` belongs to publication verification and
requires genuine hosted identity, not a locally supplied run number.

`make test-all` includes audit, contract checks, root/backend/dashboard tests,
dashboard typecheck and lint; dashboard build is separate. Avoid running each
leaf again after a successful aggregate on equivalent inputs. Keep any full
suite or independent gate required by an accepted spec/CI even when focused
tests passed. Do not change protected gates to deduplicate them.

### Before an expensive check

For a full suite or a command known/expected to take more than ten minutes,
record one line in the existing progress update or evidence ledger:
`check | changed input or required claim | prior evidence | prerequisites`.
This is a scheduling decision, not an extra permission step or a new report.

- Inspect the actual target before scheduling it. In the 4a1b722 candidate,
  `make check-test-skips` executes root, backend and dashboard suites again.
  `test-all` does not replace this gate, and this gate is not a cheap report parser.
- Check known prerequisites first: correct standalone checkout when required,
  installed frozen environments, dashboard build if consumed, native capability,
  and applicable protected authority. Never invent an approval to run a test.
- If the last run failed because authority/environment was absent and that input
  is unchanged, retain the blocker. Continue independent authorized source work;
  defer the blocked gate until its prerequisites change. A diagnostic rerun needs
  a specific new observation to collect. Do not claim deferred qualification PASS.
- Do not rerun covered leaf checks after an aggregate PASS on equivalent inputs.
  A new user status question or compaction does not invalidate retained evidence.
  Revalidate identity and label the original run time instead of calling it fresh.
- After an aggregate failure, reproduce/fix the failed scope first. Rerun the
  required aggregate when the repaired candidate is ready; do not loop the full
  suite to discover each next fix. Source/environment changes still invalidate
  affected checks and exact-source official receipts.
- Do not run competing aggregate jobs against shared builds, caches, ports or
  fixtures. Preserve the running command/process and inspect its eventual result.

Do not bypass a required gate with private Make targets, old reports or an
unreviewed result cache. Deduplicating test execution inside protected tooling is
a separate implementation change requiring its own review and verification.

Hosted `ci-portable` requires real `GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT`,
`RUNNER_TEMP`, and valid Foundation provenance. `NONINTERACTIVE=1` supplies none.
Missing context is blocked qualification, not a reason to invent values or run
private targets directly. Host/PostgreSQL lanes require their own explicit
authority; missing/skipped proof stays held. Source validation does not authorize
service startup, provider calls, brokers, or live trading.

## Debugging and resumption

On failure, preserve command, identity/environment, exit status and evidence.
Classify test/product/environment/authority failure; isolate one hypothesis
and prove it with a focused check before editing. Hard multi-hypothesis defects
need a frozen root-cause ledger before source changes. After two unsuccessful
hypothesis/fix cycles, or earlier for ambiguous critical behavior, stop
speculative edits and escalate investigation. Do not retry an unchanged command
without new evidence. Once diagnosis is deterministic, narrow the work; preserve
the operator's model and effort selection.

At a pause, use the handoff template in the final message or existing task plan,
adding completed work, pending process IDs, failures, and next action. Do not
add a second permanent context file. On resume, verify cwd, HEAD/tree, dirty
paths and required authority, compare changed files to the handoff, and reload
only invalidated facts. Receipts and old green CI are never current proof alone.
Clean only task-owned artifacts, preserving shared environments.

Final evidence separates local changes/checks, source qualification, official
ledger status, remote/main/CI, and runtime/live authority. Push, PR, merge,
deployment, databases/services, providers, brokers, Release Authority v2, and
production cutover still require separate operator authorization.
