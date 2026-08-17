
# Codex Task Packet Template — P1 Nautilus v1.231

Replace every `<...>` field from the accepted replacement plan. Never send a whole milestone to one implementer.

```text
You are the implementation subagent for <TASK_ID> — <TITLE>.

Repository: nam176hermes/Trading-Agent
Accepted parent SHA: <PARENT_SHA>
Worktree: <ABSOLUTE_WORKTREE_PATH>
Required model: <MODEL>
Required reasoning: <REASONING>

Exact engine authority for this task:
- rollback: 1.227.0 / 280ae1762df51a492a4ce71506a40b5c8706def5 / <ROLLBACK_CLOSURE_DIGEST>
- candidate/active: 1.231.0 / 27a8e54e7ac3c57d6cbf8891f0283dfbaee97317 / <V1231_CLOSURE_DIGEST>
- closure schema: <6 ROLLBACK | 7 ACTIVE>

Read before editing:
1. AGENTS.md and nested AGENTS.md
2. replacement P1 plan
3. <TASK_ID> section only
4. accepted ADR/threat model/ownership/provenance/API contract
5. relevant tests and accepted dependency SHAs

Goal: <GOAL>
In scope: <IN_SCOPE>
Out of scope: <OUT_OF_SCOPE>
Owned files: <OWNED_FILES>
Do not modify: <FORBIDDEN_FILES>
Dependencies: <DEPENDENCY_SHAS>

Non-negotiable invariants:
- root Python must not import nautilus_trader
- no mixed v1/v2 runtime in one process
- no network, credentials, live adapters, leverage or shorting
- no client-controlled executable/profile/version/entrypoint/path
- no moving version/tag/branch/range or ambient toolchain/package authority
- Decimal strings only; no float
- present-but-invalid authority is failure
- v1.227 rollback cannot be overwritten or used as active v1.231 proof
- no frozen-launcher growth
- no weakening/skipping to make tests green
- do not push, merge, deploy or alter branch protection

TDD procedure:
1. Write focused failing test/check.
2. Run and capture intended failure.
3. Implement smallest coherent change.
4. Run focused tests.
5. Run relevant regressions and authority checks.
6. Run maintainability/boundary checks.
7. Inspect full diff, generated hashes and `git diff --check`.
8. Commit once with <COMMIT_MESSAGE>.

Return:
TASK:
PARENT SHA:
HEAD SHA:
EXTERNAL AUTHORITY DIGESTS:
FILES CHANGED:
RED TEST + EXPECTED FAILURE:
IMPLEMENTATION SUMMARY:
FOCUSED TESTS + EXIT:
REGRESSION TESTS + EXIT:
BOUNDARY/AUTHORITY CHECKS + EXIT:
KNOWN LIMITATIONS:
AUTHORITY CLASSIFICATION:
COMMIT:
READY FOR SPEC REVIEW: YES/NO
```

## Spec review packet

```text
Review <TASK_ID> for exact replacement-plan compliance.
Compare task, accepted parent, external authority digests, actual diff and fresh output.
Find missing requirements, extra scope, weakened invariants, wrong ownership,
missing failure tests and undocumented schema/version changes.
Return PASS only if fully compliant.
```

## Security/code-quality review packet

```text
Adversarially review <TASK_ID> for provenance substitution, moving authority,
TOCTOU, symlink/inode replacement, ambient dependency/toolchain fallback,
mixed runtime state, canonical parsing, Decimal precision, look-ahead, native
event truthfulness, sequence/digest binding, replay/accounting, cleanup,
rollback isolation and responsibility leakage.
Return PASS only with no unresolved HIGH/CRITICAL issue.
```
