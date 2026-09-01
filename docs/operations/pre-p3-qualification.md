# Pre-P3 qualification and promotion runbook

## Authority boundary

All commands are source-only or use the separately approved disposable
PostgreSQL 16 fixture. They do not accept a DSN, contact a provider or broker,
enable network trading, or alter live/production authority. Every v2 receipt
keeps network, broker, production, and live authority `false`.

Do not invent `GITHUB_RUN_ID` or `GITHUB_RUN_ATTEMPT`. V2 issuance requires the
real values supplied by the protected qualification or Foundation workflow.
Keep private/native evidence outside Git.

## 1. Qualify one clean candidate commit

Run from the clean committed candidate `Q`. Keep outputs in a private external
directory until review:

```bash
export PRE_P3_RECEIPT_DIR=/absolute/private/pre-p3-receipts

make qualify-p1-engine-lts-final \
  P1_LTS_FOUNDATION_RECEIPT=/absolute/private/p1-foundation.json \
  P1_LTS_NATIVE_RECEIPT=/absolute/private/p1-native.json \
  P1_LTS_OPERATOR_RECEIPT=/absolute/private/p1-operator.json \
  > "$PRE_P3_RECEIPT_DIR/p1-lts-native.json"

uv run python scripts/qualify_pre_p3.py p1-bridge-v2 \
  --p1-lts "$PRE_P3_RECEIPT_DIR/p1-lts-native.json" \
  --p1-h-output "$PRE_P3_RECEIPT_DIR/p1-h-complete-v2.json" \
  --p1-lts-output "$PRE_P3_RECEIPT_DIR/p1-lts-ready-v2.json"

uv run python scripts/qualify_pre_p3.py p2-source-v2 \
  --output "$PRE_P3_RECEIPT_DIR/p2-source-complete-v2.json"

uv run python scripts/qualify_pre_p3.py p3-foundation-v2 \
  --output-dir "$PRE_P3_RECEIPT_DIR"
```

P1-H remains held until all exact external proofs exist. `DEFERRED`,
`UNAVAILABLE`, missing, malformed, mixed-source, or authority-bearing evidence
never becomes PASS.

## 2. Run the separately approved P2 runtime proof

Only after the protected GREEN fixture plan grants the exact operation
`p2-security-master-runtime-green-v1`:

```bash
uv run python scripts/qualify_pre_p3.py p2-fixture-v1 \
  --output-dir /absolute/private/pre-p3-p2-fixture \
  --operator nam176hermes \
  --reviewer distinct-governance-reviewer

export DISPOSABLE_PG_GREEN_APPROVAL_RECORD=/absolute/private/pre-p3-p2-fixture/p2-disposable-postgres-approval-v1.json
export DISPOSABLE_PG_GREEN_FIXTURE_PLAN=/absolute/private/pre-p3-p2-fixture/p2-disposable-postgres-fixture-plan-v1.json

uv run python scripts/qualify_pre_p3.py p2-runtime-v2 \
  --output "$PRE_P3_RECEIPT_DIR/p2-runtime-qualified-v2.json"

uv run python scripts/qualify_pre_p3.py p2-final-v2 \
  --source "$PRE_P3_RECEIPT_DIR/p2-source-complete-v2.json" \
  --runtime "$PRE_P3_RECEIPT_DIR/p2-runtime-qualified-v2.json" \
  --output "$PRE_P3_RECEIPT_DIR/p2-qualified-v2.json"
```

Never substitute an existing, production, operator, or remotely hosted
database. The approval and fixture plan are source-bound, expire after two
hours, require distinct operator/reviewer identities, and must remain outside
Git with mode `0600`. Start the self-hosted runner from the shell that exports
the two paths above; the P1 lane explicitly removes them and the P2 lane alone
consumes them. Missing or invalid approval produces no runtime receipt.

## 3. Certify the candidate, without granting P3

Record the protected-main base and intended promotion mechanism. The legacy
directory is read only to preserve hashes of the original v1 evidence; those
receipts are not re-attributed to `Q`.

```bash
uv run python scripts/qualify_pre_p3.py candidate-v2 \
  --receipt-dir "$PRE_P3_RECEIPT_DIR" \
  --legacy-receipt-dir docs/implementation/pre-p3/receipts \
  --base-sha "<protected-main-base-sha>" \
  --promotion-type SQUASH \
  --output "$PRE_P3_RECEIPT_DIR/pre-p3-candidate-v2.json"
```

Review and copy the eight `*-v2.json` gate receipts plus
`pre-p3-candidate-v2.json` into
`docs/implementation/pre-p3/receipts/`. Do not modify or delete the v1
receipts. Generate the candidate projection and verify it:

```bash
uv run python scripts/derive_project_status.py \
  --write docs/implementation/project-status.json
make check-project-status
```

At this stage the eight component gates may be PASS, but `PRE_P3_READY` and
`p3_alpha_development_allowed` must remain `HELD`/`false` because protected
main has not yet been proven.

## 4. Promote and capture protected-main provenance

Candidate pull-request CI must pass under the distinct
`verify-pull_request` context. Promote through the reviewed mechanism. On the
protected-main push, the read-only Foundation workflow recomputes the current
closure and uploads exactly one `pre-p3-promotion-*` artifact if the candidate
certificate is present. It does not commit, push, write the repository, or
rerun expensive runtime qualification.

The artifact is valid only when the promoted commit's canonical closure equals
`Q`, the declared base is an ancestor, every receipt still validates, and the
destination is `nam176hermes/Trading-Agent:refs/heads/main`. Its run binding
must also match the observed `GITHUB_REPOSITORY`, `GITHUB_REF`, `GITHUB_SHA`,
`GITHUB_WORKFLOW_REF`, and `GITHUB_WORKFLOW_SHA`; a fork or branch artifact is
not interchangeable with protected-main evidence.

## 5. Record the reviewed promotion and derive final status

Review the artifact, then add it through a separate receipt-only pull request
at this exact path:

```text
docs/implementation/pre-p3/promotions/<promoted-main-sha>-v1.json
```

Regenerate (do not hand-edit) project status:

```bash
uv run python scripts/derive_project_status.py \
  --write docs/implementation/project-status.json
make check-project-status
make ci
```

Final protected main is acceptable only when canonical derivation reports
`P1_H_COMPLETE`, `P1_LTS_READY`, `P2_RUNTIME_QUALIFIED`, `P2_QUALIFIED`, and
`PRE_P3_READY` as PASS, with `p3_alpha_development_allowed=true`.
`LIVE_ELIGIBLE`, `LIVE_ENABLED`, network, broker, and production authority must
remain false.
