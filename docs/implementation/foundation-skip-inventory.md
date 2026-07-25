# Foundation test skip inventory

Date: 2026-07-24
Owner: Foundation maintainers
Canonical inventory: `tests/skip-allowlist.yaml`
Canonical gate: `make check-test-skips`

## Decision policy

A skip or deselection is allowed only when its exact component, test node ID, outcome and normalized runtime reason are present in the committed allowlist. The gate rejects:

- a skipped or deselected node that is absent from the allowlist;
- an allowlist node that is no longer skipped or deselected;
- a skip that changes into deselection, or a deselection that changes into skip;
- every collected test that remains `not_run`;
- an individual collected test removed without an explicit deselection observation;
- an expired `review_by` date;
- `UNKNOWN` as a reason category;
- a security-critical entry without an explicit reason and approval record type;
- duplicate component and node ID pairs;
- duplicate runtime observations for one governed key;
- a runtime skip or deselection reason that drifts from the reviewed reason;
- malformed fields or an unapproved category;
- a suite failure while collecting runtime observations.

The allowlist is YAML by filename and strict JSON by syntax. JSON is a YAML 1.2 subset and keeps the checker dependency-free.

## Measured inventory

The live root measurement found 227 skips, one more than the supplied 226-skip baseline. The committed inventory follows live node-level evidence rather than the stale aggregate.

| Component | Skipped | Deselected | Managed entries |
|---|---:|---:|---:|
| Root Python | 227 | 13 | 240 |
| Legacy research backend | 2 | 0 | 2 |
| Dashboard | 0 | 0 | 0 |
| Total | 229 | 13 | 242 |

| Reason category | Entries |
|---|---:|
| `DISPOSABLE_POSTGRES_REQUIRED` | 238 |
| `PROVIDER_CREDENTIAL_REQUIRED` | 2 |
| `MISSING_HOST_CAPABILITY` | 2 |
| `UNKNOWN` | 0 |

All 242 component and node ID pairs are unique. There are 238 security-critical entries. Every security-critical entry has an explicit reason and non-`NONE` approval record type. Current review deadline: `2026-10-31`.

Dashboard currently has no skipped or deselected test. `apps/dashboard/tests/test-inventory.json` defines the recursive inventory consumed by both `npm test` and the governance runner. Every `*.test.mjs` and `*.integration.sh` file must be classified and observed; any future skip is unapproved by default.

## Required fields

Every entry contains exactly the governance fields requested by the package:

- `test_node_id`
- `component`
- `outcome`
- `reason_category`
- `reason`
- `owner`
- `approval_record_type`
- `required_binary_or_service`
- `target_phase`
- `review_by`
- `security_critical`
- `allowed_in_ci`

## Runtime classification

The checker does not infer approval during normal CI. Runtime observations are matched against the committed record. Classification logic exists only for the explicit bootstrap command and never mutates the allowlist during a normal gate.

```bash
uv run python scripts/check_test_governance.py \
  --bootstrap-allowlist tests/skip-allowlist.yaml
```

Bootstrap output containing `UNKNOWN` fails. Reviewers must inspect ownership, reason, approval metadata, target phase and review date before committing any generated candidate.

## Reports

Default evidence root:

```text
/tmp/trading-agent-test-evidence/test-governance/
```

Files include:

- `root-raw.json`
- `legacy-raw.json`
- `dashboard-raw.json`
- `test-governance.json`
- `test-governance-error.json` when policy evaluation fails
- per-component command logs

The merged report distinguishes passed, failed, skipped, deselected, not-run and approval-blocked observations. Each run removes stale merged and raw JSON before collection. A failure writes current error evidence instead of leaving a previous successful report available for upload. Evidence directories must be real current-user-owned private directories. Report creation, replacement, cleanup and fsync are bound to a validated stable directory descriptor. Reports remain outside the checkout because the repository secret-hygiene scanner intentionally scans ignored and untracked files.

Override the evidence destination when needed:

```bash
make check-test-skips TEST_EVIDENCE_DIR=/absolute/private/path
```

## Review procedure

1. Run `make check-test-skips` on the proposed tree.
2. Inspect every new or changed node in the machine report.
3. Reject attempts to convert a failure into a skip or hide a node through selection changes.
4. Confirm owner, approved category, required environment, target phase and review date.
5. For security-critical nodes, require an explicit approval reason and record type.
6. Remove stale entries when the underlying test runs again.
7. Commit the allowlist change with the test or environment change that requires it.
