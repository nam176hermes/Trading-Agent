# Local phase requirement and acceptance matrix

Reconciled 2026-09-18 against committed candidate
`977ff03dc163e2ef471c095f7e69286ba0a2050e`, tree
`cb4fa39da4ae1e60f8208876d3c1a92bdf5d3335`. The execution continuation adds
uncommitted LOCAL implementation, tests and workflow documents to that parent. The [implementation plan](2026-09-18-local-phase-workflow.md)
owns execution order and authority boundaries. No runtime/source gate is changed
by this matrix. PR #69 is not a prerequisite to local source work.

## Scope and interpretation

The requirement inventory is: the eight nodes of the pinned P1-H/LTS DAG;
P2-00…P2-20 from the P2 source evidence; all derived Pre-P3 prerequisites; and
all 34 accepted P3 V2.1 task cards, including operational cards. Later local-lane
acceptance rows A01–A12 amend the transport/provenance lane, never the economic
criteria. The baseline's approved M7 exception remains exactly scoped.

Source/test paths below were checked in this candidate. Existing source is a
mapped implementation, not a statement that every branch is correct. Existing
tests are test inventory, not a current pass or sufficient coverage. At the initial document checkpoint only E1
was executed; E2–E6 are retained historical/local evidence. The execution
continuation below supersedes that checkpoint for the new source changes,
including real disposable PostgreSQL and native source tests. Full qualification,
protected host activation, provider and remote operations remain unperformed.
Unknown coverage/environment stays UNVERIFIED.

Gap codes are independent and may coexist:

| Code | Meaning | Closure evidence |
| --- | --- | --- |
| I | Missing implementation at a named seam | New behavior through the actual producer/consumer, focused red→green |
| T | Missing tests/proof for a named behavior; T? means sufficiency unverified | Negative and positive tests mapped to the requirement, observed result and skip accounting |
| E | Missing or unverified authorized environment/capability | Current exact host/profile/native/disposable SQL proof; a test double does not close it |
| C | Missing valid certification for the exact source/policy/issuer | Authenticated retained proof and fresh readback; old green logs or a receipt filename are insufficient |
| O | Operational/research outcome not evidenced | Actual approved campaign output, including truthful FAIL/NONE_QUALIFIED |

`mapped; C` never means “implementation missing”. `I/T local` means the existing
hosted/product implementation is present but its new authenticated LOCAL lane
and regression proof are still missing. No omission is counted as PASS. A failed
check remains FAIL; missing and invalid/stale evidence remain distinct reasons.

## Evidence register

| Ref | Evidence inspected and limit |
| --- | --- |
| E0 | Candidate HEAD/tree; clean target before transfer; committed `docs/implementation/project-status.json` and `docs/implementation/p3/p3-source-status.json`. P2 source says IMPLEMENTED, but P1-H/LTS, P2 and Pre-P3 certificates are HELD; all eight P3 gates HELD, blocker `E_P3_PHASE_EXIT_RECEIPT`. This is the stored parent projection, not regenerated dirty-document status. |
| E1 | Fresh `uv run python scripts/dev.py test tests/governance/test_p3_status.py tests/governance/test_p3_source_outputs.py tests/governance/test_pre_p3_provenance.py tests/p3/test_phase_exit.py`: **57 passed**, exit 0, 62.56s. External log `local-phase-baseline-20260918/focused-existing-source.log`. This exercises existing status/closure/numeric/exit rejection behavior, not a LOCAL issuer or real SQL/host qualification. |
| E2 | `m4-m12-20260917/local-validation-977ff03.json`: four scoped check exits 0 on exact parent; prior aggregate FAIL explicitly preserved; hosted-current-head recorded PENDING at capture. No current CI PASS inferred. |
| E3 | `m4-m12-20260917/local-validation-df5f4a3.json`: older aggregate on `1f76222` exit 2, 11001 pass / 2 fail / 251 skip / 50 deselected; missing dashboard production build. Later scoped recovery/build, headless 9, backend 507 with 2 skips, dashboard 199 recorded PASS. These are different runs, not a green aggregate on 977ff03. |
| E4 | `m4-m12-20260917/revision-inventory-result-977ff03dc163e2ef471c095f7e69286ba0a2050e.json`: 2800 days, original 22402 CAS files unchanged, zero validation writes/network, mismatch rejection; official_research=false and protected_custody=false. The result JSON was inspected, not independently replayed over all CAS in this turn. |
| E5 | `m4-m12-20260917/profile-issuance-preparation-20260918-v2.md`: preparation only; retained observation lacked protected profiles/distinct accounts and runner readiness. Older `qualification-inputs.json` predates later operator selections, so do not reuse its null selections as current decisions. Host installation/capability must be reverified at authorized execution; no current approval inferred. |
| E6 | `m4-m12-20260917/HANDOFF.md`, top STOPPED entry: earlier work stopped, CI cancellation requested, source/evidence preserved. Only the present document task resumes; old merge/activation instructions are not active. |
| E7 | `local-phase-baseline-20260918/transfer-receipt.json`: four exact donor hashes, original donor/canonical dirty files and HEAD/status snapshots. Final preservation comparison recorded alongside it. |

All external evidence paths above are relative to
`/home/thenam176/projects/trading-agent-optimization-evidence/`.
These files remain outside Git. Read them in full for an execution decision;
this register is a scope summary, not a signed receipt.

## P1-H and LTS: every qualification DAG node

Authority: `docs/implementation/p1-real-nautilus/lts/p1-engine-lts-policy-v2.json`.
Reuse `scripts/qualify_p1_engine_lts.py::_LOCAL_GATES/qualify` and the Pre-P3
bridge. Its existing local mode returns `P1_H_LOCAL_SOURCE_QUALIFIED` with
`external_evidence=NOT_ASSESSED`; that is explicitly not P1_H_COMPLETE.

| Requirement | Existing source owner | Existing test target | Evidence / gap | Acceptance and plan step |
| --- | --- | --- | --- | --- |
| P1S_SOURCE | `packages/nautilus_upgrade_authority/lts.py` | `tests/nautilus_upgrade/test_lts_policy.py` | E0; C; I/T local | Pinned engine/epoch, class A–D and evidence bindings reject drift; steps 2–3, A02–A04 |
| P1S_RECOVERY | `packages/execution_sandbox` | `tests/execution_sandbox/test_recovery_checkpoint_load.py` | E0; C; I/T local | Old checkpoint requires replay; incompatible epoch rejects; steps 2–3, A02–A04 |
| P1S_GOLDEN | `packages/nautilus_backtest/fixtures.py` | `tests/nautilus_backtest/test_scenarios.py` | E0; C; I/T local | All pinned scenario/event/oracle digests match; steps 2–3, A02–A04 |
| P1N_G1 | `scripts/qualify_pre_p3.py` | `tests/nautilus_upgrade/test_qualify_p1_engine_lts.py` | E0; C; I/T local; E/T? real native proof | Exact native generation/closure proof authenticated; steps 2–3, A02–A04 |
| P1N_E2E | `scripts/qualify_pre_p3.py` | `tests/nautilus_upgrade/test_qualify_p1_engine_lts.py` | E0; C; I/T local; E/T? real native proof | Real native E2E proof and predecessor G1 validated; steps 2–3, A02–A04 |
| P1N_PAPER | `scripts/qualify_pre_p3.py` | `tests/nautilus_backtest/test_paper_compat.py` | E0; C; I/T local; E/T? real native proof | Approved isolated paper/native proof follows E2E and recovery; no activation inferred; steps 2–3, A02–A04 |
| P1H_FOUNDATION | `scripts/qualify_p1_engine_lts.py` | `tests/nautilus_upgrade/test_qualify_p1_engine_lts.py` | E0; C; I/T local | Authenticated source gate evidence bound to all source/recovery/golden dependencies; steps 2–3, A02–A04 |
| P1O_ACCEPT | `scripts/qualify_p1_engine_lts.py` | `tests/nautilus_upgrade/test_qualify_p1_engine_lts.py` | E0; C; I/T local | Independent operator ACCEPT binds the full DAG and exact source; no self-issued replacement; steps 2–3, A02–A04 |

Also retain classifier tests `tests/nautilus_upgrade/test_p1_h_impact_cli.py`,
lifecycle tests `test_p1_h_lifecycle.py`, engine-session/runtime-contract tests
listed by `_LOCAL_GATES`, and `tests/paper_runtime/test_engine_session_port.py`.
`P1_LTS_READY` requires complete nested P1-H plus the pinned historical P1
certificate, not replacement of that historical certificate.

## P2: every source work package

Authority: `docs/implementation/p2-data-platform-evidence.md` and the source/PIT/
runtime checks in `scripts/qualify_pre_p3.py::p2_source_v2/p2_runtime_v2/p2_final_v2`.
These rows map existing code; E0's missing v2 certificate is not evidence that
P2 ingestion, PIT or storage must be implemented again.

| Requirement | Existing source / document | Existing test target | Evidence / gap | Acceptance and plan step |
| --- | --- | --- | --- | --- |
| P2-00 Rebaseline | `docs/implementation/p2-data-platform-evidence.md` | `tests/data_platform/test_certification.py` | E0/E3; mapped; C; T? complete coverage | Exact candidate identity retained; steps 3, 5 |
| P2-01 Boundaries/safety | `AGENTS.md` | `tests/data_platform/test_contracts.py` | E0/E3; mapped; C; T? complete coverage | Independent component graphs; paper-only authority; steps 3, 5 |
| P2-02 Arrow/PIT contract | `packages/data_catalog/schema_registry.py` | `tests/data_platform/test_contracts.py` | E0/E3; mapped; C; T? complete coverage | Stable fields and explicit PIT cutoff semantics; steps 3, 5 |
| P2-03 Security master | `packages/security_master/resolver.py` | `tests/security_master/test_resolver.py` | E0/E3; mapped; C; T? complete coverage | Append-only revision resolution; no future-known facts; steps 3, 5 |
| P2-04 Corporate actions | `packages/data_normalization.py` | `tests/data_platform/test_normalization.py` | E0/E3; mapped; C; T? complete coverage | Immutable raw values, PIT factor selection and exact arithmetic; steps 3, 5 |
| P2-05 CAS | `packages/data_catalog/artifact_store.py` | `tests/data_platform/test_storage_query.py` | E0/E3; mapped; C; T? complete coverage | Private content addressing rejects changed/missing bytes; steps 3, 5 |
| P2-06 Snapshots | `packages/data_catalog/v2.py` | `tests/data_platform/test_storage_query.py` | E0/E3; mapped; C; T? complete coverage | Immutable partition/snapshot identity; retain v3 owner for current inputs; steps 3, 5 |
| P2-07 Parquet | `packages/data_catalog/parquet.py` | `tests/security_master/test_parquet_snapshot.py` | E0/E3; mapped; C; T? complete coverage | Deterministic bytes/schema and lineage; steps 3, 5 |
| P2-08 Provider boundary | `packages/data_providers.py` | `tests/data_platform/test_provider_quality.py` | E0/E3; mapped; C; T? complete coverage | Injected provider returns bounded raw evidence and receipt; steps 3, 5 |
| P2-09 Provider-free fixture | `services/market_data/fixture.py` | `tests/data_platform/test_provider_quality.py` | E0/E3; mapped; C; T? complete coverage | Deterministic fixture proves contract without network authority; steps 3, 5 |
| P2-10 Quality/conflict | `packages/data_quality.py` | `tests/data_platform/test_provider_quality.py` | E0/E3; mapped; C; T? complete coverage | OHLCV validation and explicit priority; ambiguity rejects; steps 3, 5 |
| P2-11 PIT snapshot | `packages/data_catalog/v3.py` | `tests/data_platform/test_revision_chains.py` | E0/E3; mapped; C; T? complete coverage | T2 snapshot excludes correction first known at T3; steps 3, 5 |
| P2-12 Query parity | `packages/data_query/__init__.py` | `tests/data_platform/test_storage_query.py` | E0/E3; mapped; C; T? complete coverage | PyArrow/Polars/DuckDB select identical PIT rows; steps 3, 5 |
| P2-13 Qlib | `packages/data_projections.py` | `tests/data_platform/test_projections_documents_scale.py` | E0/E3; mapped; C; T? complete coverage | Canonical deterministic CSV; reject invalid numeric features; steps 3, 5 |
| P2-14 P1 projection | `packages/security_master/projector.py` | `tests/security_master/test_p1_projection.py` | E0/E3; mapped; C; T? complete coverage | Nautilus artifacts use selected security-master revisions; steps 3, 5 |
| P2-15 Document registry | `packages/data_documents.py` | `tests/data_platform/test_projections_documents_scale.py` | E0/E3; mapped; C; T? complete coverage | Immutable documents with valid PIT/encoding; steps 3, 5 |
| P2-16 Local PageIndex | `packages/data_documents.py` | `tests/data_platform/test_projections_documents_scale.py` | E0/E3; mapped; C; T? complete coverage | Deterministic local retrieval seam; steps 3, 5 |
| P2-17 Recall/MRR | `packages/data_documents.py` | `tests/data_platform/test_projections_documents_scale.py` | E0/E3; mapped; C; T? complete coverage | Benchmark computes observed recall/MRR; steps 3, 5 |
| P2-18 Projection receipt | `packages/security_master/projector.py` | `tests/security_master/test_p1_projection.py` | E0/E3; mapped; C; T? complete coverage | Reject stale/ambiguous/unrepresentable input; bind receipt to exact artifacts; steps 3, 5 |
| P2-19 Iceberg gate | `packages/data_scale.py` | `tests/data_platform/test_projections_documents_scale.py` | E0/E3; mapped; C; T? complete coverage | Keep dependency absent until measured trigger passes; steps 3, 5 |
| P2-20 Certification/runbook | `scripts/certify_p2_data_platform.py` | `tests/data_platform/test_certification.py` | E0/E3; mapped; C; T? complete coverage | Three-of-three subprocess replay equal; preserve PIT/runtime gates; steps 3, 5 |

P2 runtime is a separate requirement: `tests/security_master/test_postgres_runtime.py`,
`alembic/versions/0019_p2_security_master.py`, and `make test-p2-runtime-postgres`
under a current approved disposable PG16 fixture. Gap **E/C**, plus **I/T local
issuer integration**, not a missing SQL source migration. Do not run the installed
DB or widen fixture validity to obtain a green result.

PIT subrequirements are individually enumerated in
`docs/implementation/pre-p3/p2-pit-adversarial-suite-v1.json`. Its
`required_cutoff_rule` requires an event at T1, correction known at T3, and a
query at T2 selecting only T1. The mapped test bodies were read in this turn:

| Required PIT case | Exact mapped test node | What its assertions prove | Remaining gap / acceptance |
| --- | --- | --- | --- |
| PRICE_BAR_REVISION | `tests/data_platform/test_revision_chains.py::test_t2_snapshot_never_sees_correction_first_known_at_t3` | T2 selects original, T3 correction; different snapshot digests | C: execute/retain exact-candidate proof |
| SYMBOL_MAPPING_CHANGE | `tests/security_master/test_resolver.py::test_symbol_mapping_lookup_keys_cannot_change_within_fact` | Changed lookup keys in the same fact reject | T: mapped node lacks a valid symbol transition resolved on both sides of cutoff; add/reuse that test, then certify |
| SECURITY_METADATA_CORRECTION | `tests/security_master/test_resolver.py::test_resolver_lineage_uses_strict_recorded_at_not_evidence_known_at` | Pre-correction query returns root; correction visible at its recorded_at; invalid ordering rejects | C: execute/retain exact-candidate proof |
| CORPORATE_ACTION | `tests/security_master/test_p1_projection.py::test_rejects_stale_ambiguous_or_unrepresentable_inputs` | Parameterized unsafe projection, including corporate action, rejects | T: mapped node alone lacks before/after action-known cutoff selection; retain fail-closed P1 projection and add/reuse temporal P2 test |
| LISTING_DELISTING | `tests/security_master/test_models.py::test_corporate_action_union_covers_raw_p2_facts_only` | Payload union parses delisting and rejects invalid effective_to | T: parsing is not listing/delisting temporal visibility; add/reuse resolver/snapshot cutoff proof |
| PROVIDER_CONFLICT | `tests/data_platform/test_provider_quality.py::test_provider_conflicts_use_explicit_priority_and_fail_on_same_rank_ambiguity` | Priority choice and equal-rank ambiguity rejection | T: required cutoff rule is not asserted by this node; add/reuse late-known conflict selection proof |
| LATE_ARRIVING_DATA | `tests/security_master/test_parquet_snapshot.py::test_snapshot_rejects_revision_recorded_after_the_knowledge_cutoff` | known_at < cutoff < recorded_at is rejected during materialization | C: execute/retain exact-candidate proof |

The four **T** findings concern the manifest-selected proof, not a claim that
no other test anywhere covers the behavior or that the product is broken.
Step 3 must first look for an existing complete case, then add only missing
assertions. Keep all seven cases and the accepted cutoff rule unchanged.
`packages/alpha_lifecycle/pit_suite.py` must preserve collection/report/CAS
readback and source/environment binding. Counting seven PASS flags is not enough.
All cases additionally need I/T for LOCAL issuer metadata and current C proof.

## Pre-P3: every gate and its certification boundary

| Requirement | Existing producer / validator | Existing test target | Evidence / gap | Acceptance and plan step |
| --- | --- | --- | --- | --- |
| HWC_SOURCE_READY / HWC_PORTABLE_QUALIFIED | `packages/hwc_status.py` | `tests/hwc/test_headless_portable.py` | Source proofs present; E0 HELD, E3 build failure/recovery; C/E, I/T local | Authenticate headless/recovery/boundary proofs; no dashboard build omission; steps 2–3, 5 |
| P1_H_COMPLETE | `scripts/qualify_pre_p3.py` | `tests/nautilus_upgrade/test_qualify_p1_engine_lts.py` | E0; C/E, I/T local | All eight nodes above; validate retained external proof; steps 2–3, 5 |
| P1_LTS_READY | `packages/nautilus_upgrade_authority/lts.py` | `tests/nautilus_upgrade/test_lts_policy.py` | E0; C/E, I/T local | Nested P1-H and pinned P1 source binding agree; steps 2–3, 5 |
| P2_SOURCE_COMPLETE | `scripts/qualify_pre_p3.py` | `tests/data_platform/test_adversarial_suite_manifest.py` | E0; C, I/T local | All P2 source tests, executed PIT artifacts and 3x replay; steps 2–3, 5 |
| P2_RUNTIME_QUALIFIED | `scripts/qualify_pre_p3.py` | `tests/security_master/test_postgres_runtime.py` | E0/E5; E/C, I/T local | Real PG16 fixture proof with current approval and cleanup; steps 2–3, 5 |
| P2_QUALIFIED | `packages/pre_p3_provenance.py` | `tests/governance/test_pre_p3_qualifier.py` | E0/E1; C, I/T local | Both P2 receipts bind same source and compatible issuer; reread bytes; steps 2–3, 5 |
| ALPHA_REGISTRY_FOUNDATION | `packages/alpha_lifecycle/registry.py` | `tests/alpha_lifecycle/test_alpha_registry.py` | E0; C, I/T local | Append/replay lineage tests and frozen artifact digest; steps 2–3, 5 |
| P3_BASELINES_FROZEN | `packages/alpha_lifecycle/baselines.py` | `tests/alpha_lifecycle/test_baselines.py` | E0; C, I/T local | Exact baseline policy and numerical conventions remain pinned; steps 2–3, 5 |
| P3_EVALUATION_PROTOCOL_FROZEN | `packages/alpha_lifecycle/protocol.py` | `tests/alpha_lifecycle/test_protocol.py` | E0; C, I/T local | C01–C16 unchanged, no window/threshold tuning; steps 2–3, 5 |
| PRE_P3_READY / candidate + promotion | `packages/project_status.py` | `tests/governance/test_pre_p3_provenance.py` | E0/E1; C, I/T local | Derive local readiness without hosted promotion; hosted readiness still requires it; steps 2–3, 5 |

## P3: all 34 V2.1 task cards

Authority: external accepted pack
`/home/thenam176/projects/p3-v21-reference/pack/p3-v2.1/07-TASK-CARDS.md`,
linked by `docs/implementation/p3/decision-record-v21.json` and the integration
map. Historical pack NOT_STARTED labels are planning labels, not a current
implementation verdict. All listed source-task file anchors exist on 977ff03.
The table maps current paths and extends the baseline acceptance with the local
lane. E1 applies only to the four explicitly tested files; other tests below
were located, not executed in this turn.

| Task / requirement | Existing source owner | Existing test target | Evidence / gap | Observable acceptance / plan step |
| --- | --- | --- | --- | --- |
| T-P3-000 Baseline/map | `docs/implementation/p3/source-integration-map-v21.json` | `tests/governance/test_p3_baseline.py` | E0/E7; documentation reconciled | Accepted 977ff03 plus donor provenance; step 1 |
| T-P3-001 Strict contracts | `packages/alpha_lifecycle/contracts/authority.py` | `tests/p3/test_contracts.py` | mapped; I/T local; C | Version LOCAL/GITHUB without altering old digests; step 2 |
| T-P3-002 Policies/epochs | `packages/alpha_lifecycle/epoch.py` | `tests/p3/test_policy_freeze.py` | mapped; C; T? | Pinned policy/epochs unchanged, exposure retained; steps 2, 5 |
| T-P3-010 Bounded archive acquisition | `packages/alpha_lifecycle/acquisition.py` | `tests/p3/test_acquisition.py` | E4; mapped; C | Keep 2799 daily + sole approved monthly row; no widened fallback; steps 5–6 |
| T-P3-011 Seal data/P2 snapshots | `scripts/seal_p3_dataset.py` | `tests/p3/test_research_input_revision.py` | E4; mapped; C, E protected custody | 2800-day exact-commit inventory readback; preserve original CAS; steps 5–6 |
| T-P3-012 Folds/training regimes | `packages/alpha_lifecycle/folds.py` | `tests/p3/test_regimes.py` | mapped; C/O; T? | Fixed folds and training-only thresholds, no lookahead; steps 5–6 |
| T-P3-020 Trace/metrics | `packages/alpha_lifecycle/execution_trace.py` | `tests/p3/test_metric_goldens.py` | mapped; C/O; T? | Frozen metrics goldens and trace retain exact arithmetic; steps 5–6 |
| T-P3-021 Complete baseline pack | `packages/alpha_lifecycle/baseline_campaign.py` | `tests/p3/test_baseline_pack.py` | mapped; C/O; T? | All baselines, context resets and selection replay; steps 5–6 |
| T-P3-022 A0–A3/perturbations | `packages/alpha_lifecycle/candidates.py` | `tests/p3/test_candidates.py` | mapped; C/O; T? | Exact disclosed family and fixed perturbations; steps 5–6 |
| T-P3-023 Robustness/capacity | `packages/alpha_lifecycle/robustness.py` | `tests/p3/test_robustness.py` | mapped; C/O; T? | All trials/outcomes retained, capacity thresholds unchanged; steps 5–6 |
| T-P3-030 Deterministic evaluator | `packages/alpha_lifecycle/evaluation.py` | `tests/p3/test_evaluation.py` | mapped; E/C/O; T? | Isolation and deterministic evaluation over exact inputs; steps 4–6 |
| T-P3-031 Independent replicas/sandbox | `packages/alpha_lifecycle/sandbox.py` | `tests/p3/test_replica_execution.py` | mapped; E/C; T? real host | Real independent processes, pinned startup and replay; steps 4–6 |
| T-P3-032 Frozen C01–C16 | `packages/alpha_lifecycle/qualification.py` | `tests/p3/test_qualification.py` | mapped; C/O; T? | Read actual retained evidence through unchanged protocol; steps 3, 6 |
| T-P3-040 Domain/lifecycle | `packages/alpha_lifecycle/lifecycle.py` | `tests/p3/test_lifecycle.py` | mapped; C; T? | Pure transitions replay complete registry lineage; steps 4–6 |
| T-P3-041 DB capability migration | `services/job_store/p3_sql.py` | `tests/p3/test_sql_authority.py` | mapped; I/T persisted LOCAL, E/C | Forward migration only; exact catalog, roles and cross-lane fences; step 4 |
| T-P3-042 Atomic publication/recovery | `services/job_store/p3_publication_repository.py` | `tests/p3/test_publication_recovery.py` | mapped; I/T LOCAL, E/C | No duplicate event/result on crash/retry; same transaction/lease; steps 4–4a |
| T-P3-043 Job API/worker | `services/job_worker/results.py` | `tests/p3/test_job_api.py` | mapped; I/T LOCAL, E/C | One durable owner; admission/result validation agree on lane; step 4 |
| T-P3-044 Projection/trial accounting | `packages/alpha_lifecycle/projection.py` | `tests/p3/test_projection.py` | mapped; C/O; T? | Read-only projection retains all successes/failures/trials; steps 4a, 6 |
| T-P3-050 Reference/native adapter | `services/job_worker/p3_native_runner.py` | `tests/p3/test_native_contract.py` | mapped; E/C/O; T? real native | Pinned next-open reference, six genuine native processes and retained parity; steps 4–6 |
| T-P3-051 One-primary custody | `packages/alpha_lifecycle/holdout.py` | `tests/p3/test_holdout_admission.py` | mapped; I/T cross-lane, E/C | One experiment across issuers, never replacement primary/re-disclosure; step 4 |
| T-P3-052 Authority workflow | `packages/alpha_lifecycle/authority.py` | `tests/p3/test_authority.py` | hosted mapped; I/T LOCAL, E/C | Protected local adapter feeds same job/session; hosted negatives intact; steps 2–4 |
| T-P3-053 Status/provenance/exclusions | `packages/p3_status.py` | `tests/governance/test_p3_status.py` | E1; I/T LOCAL and exit bundle, C | Separate local economic/technical/published states; exact closure rules; step 4a |
| T-P3-054 Narrative/integrated tests | `tests/p3/test_plan_invariants.py` | `tests/governance/test_p3_source_outputs.py` | E1/E3; T?/C, aggregate not PASS | Docs/generators agree; all source gates at freeze; steps 1, 5 |
| T-P3-060 Freeze/requalify M1 | `scripts/qualify_pre_p3.py` | `tests/governance/test_pre_p3_qualifier.py` | E0/E2/E3; I/T LOCAL, E/C | Exact clean source qualified; published promotion remains independent; steps 5–6 |
| T-P3-061 Official inputs/custodian | `services/job_worker/p3_custodian_profile.py` | `tests/p3/test_custodian_profile.py` | E4/E5; E/C/O | Research inventory exists; independently qualified custody/holdout still required; step 6 |
| T-P3-062A Official baselines/select | `packages/alpha_lifecycle/baseline_campaign.py` | `tests/p3/test_baseline_operation.py` | E0/E5; I/T LOCAL, E/C/O | Actual Job API baseline run and retained selected baseline; step 6 |
| T-P3-062B Register entire family | `services/job_worker/p3_publication_producer.py` | `tests/p3/test_register_operation.py` | E0/E5; I/T LOCAL, E/C/O | Durable complete-family registration before candidate results; step 6 |
| T-P3-062C Official C1 A0 | `packages/alpha_lifecycle/qualification.py` | `tests/p3/test_oos_operation.py` | E0/E5; I/T LOCAL, E/C/O | Retain A0 outcome and complete evidence under same source; step 6 |
| T-P3-063 Official C2 A1–A3 | `packages/alpha_lifecycle/qualification.py` | `tests/p3/test_oos_registration_validation.py` | E0/E5; I/T LOCAL, E/C/O | All three candidates and failed trials disclosed; step 6 |
| T-P3-064 One primary from OOS | `packages/alpha_lifecycle/primary_selection.py` | `tests/p3/test_primary_selection.py` | E0/E5; E/C/O | Deterministic SELECTED or NONE_QUALIFIED before holdout; step 6 |
| T-P3-070 Authorize historical holdout | `services/job_worker/p3_holdout_release.py` | `tests/p3/test_session_holdout_claim.py` | E0/E5; I/T cross-lane, E/C/O | Fresh independent approval and one durable disclosure claim; step 6 |
| T-P3-071 Holdout/reference | `packages/alpha_lifecycle/holdout.py` | `tests/p3/test_holdout_view.py` | E0/E5; E/C/O | Retained released view, recomputation and exact input commitment; step 6 |
| T-P3-072 Native parity | `services/job_worker/p3_native_spawn.py` | `tests/p3/test_native_spawn.py` | E0/E5; E/C/O | Genuine pinned native evidence and independent replicas; step 6 |
| T-P3-073 Exit/provenance closure | `services/job_worker/p3_publication_producer.py` | `tests/p3/test_phase_exit.py` | E1; I/T receipt producer/local status; E/C/O | Committed outcome → authenticated bundle → status, no post-holdout code change; steps 4a, 6–7 |

T-P3-053/T-P3-073 gap is concrete: `PhaseExitReceipt` exists in
`packages/p3_provenance.py`, but PHASE_EXIT's command validator is
`p3-publication-exit-v1`. The latter returns PublicationProposal. A parser for
`p3-phase-exit-v1` does not supply the absent committed-publication → phase-receipt
producer. The current workflow uploads terminal JSON, not an attested exit
receipt. Add this producer and lane-aware status before official holdout.

P3 numerical acceptance remains the pinned policy, including positive base and
double-cost/delayed returns, excess return >= 0.02, maximum drawdown <= 0.25,
capacity median <= 0.02 and peak <= 0.10, native positive return/excess/drawdown,
and parity/replay/primary/identity (13 exit checks). `evaluate_phase_exit` is the
existing owner. E1 covers numeric and retained-evidence rejection tests; it does
not establish a profitable alpha. Historical holdout remains access-controlled,
not informationally blind; native parity remains synthetic accounting parity.

## Local lane acceptance: executable completion criteria

Every row references an implementation step and its exact focused command in
the plan. New tests are required; existing named tests are extension points.

| ID | Criterion | Proof first / failure case | Required evidence and current gap | Step |
| --- | --- | --- | --- | --- |
| A01 | Work starts on 977ff03; donor/canonical edits preserved | Changed donor bytes, candidate drift or unexpected target dirt blocks copy | E7 plus final hash comparison; document transfer only | 1 |
| A02 | Explicit authenticated LOCAL/GITHUB identity across contracts | Forged JSON, wrong host/boot/source/policy, revoked/expired run, issuer/attempt collision; legacy digest regression | New contract/authority tests and real protected issuer readback; I/T/E/C | 2 |
| A03 | Full HWC/P1-H/LTS/P2/Pre-P3 local DAG works without GitHub variables | Remove any predecessor, swap source/issuer, stale proof, replace retained bytes with PASS list | Actual retained proof readback; local P1 mode alone insufficient; I/T/E/C | 3 |
| A04 | Local readiness never grants published readiness | Local proof supplied to hosted receipt/promotion validator rejects | Both local-success/hosted-HELD and unchanged hosted negative tests; I/T/C | 3, 4a |
| A05 | Same authority decision at enqueue, worker and custodian | Partial migration, source drift, revoked lease, wrong-role or wrong-host admission | Profile/coordinator/custodian tests plus actual host proof; I/T/E/C | 4 |
| A06 | Durable identity/recovery preserves single holdout across lanes | Concurrent LOCAL/GITHUB claims; crash after consume/SQL commit; expired attempt replay | Real disposable PG migration/concurrency/recovery evidence, exact catalog; I/T/E/C | 4 |
| A07 | Producer creates exit certificate only after durable readback | Child PASS/proposal, missing CAS, stale registry head, wrong result/attempt rejects | Commit→readback→bundle→status test; resumable exact export without duplicate publication; I/T/C | 4a |
| A08 | Local technical, economic and published verdicts stay separate | FAIL, NONE_QUALIFIED, HELD, absent/invalid/stale evidence never become success | Versioned receipt/status tests, 13 frozen checks, explicit blockers; I/T/C/O | 4a |
| A09 | Hosted provenance and source closure remain strict | Wrong-ref attestation, invalid nearest promotion, malformed output, broad exclusion, issuer swap rejects | E1 is partial existing proof; extend promotion/output/workflow regressions; I/T/C | 4a |
| A10 | Frozen source passes applicable full gates and review | Missing .next, hidden skip, unavailable review or changed candidate prevents qualification | Build then test-all; static/contracts; security/concurrency/recovery evidence; E2/E3 are historical partial evidence; T?/C | 5 |
| A11 | One actual local P3 campaign path, no GitHub variables | Missing authority prevents execution; failed/no alpha preserves honest outcome, no threshold tuning | Real HWC/P1/P2/Pre-P3, Job API/worker, custody/native and terminal evidence on approved host; E/C/O plus local I/T | 6 |
| A12 | Publication/external review separate from local completion | Local completion cannot declare merge/CI/external approval or authorize next-phase expansion | Separate authorization, genuine hosted qualification and resolved review on exact published source; C, not needed for this document task | 7 |

## Execution ordering and final verdicts

Reuse the mapped product code. Implement identity/authentication first, migrate
proof aggregation and durable consumers, then complete exit receipt/status
before freezing source. Run focused checks, freeze/full gates, obtain separately
authorized host qualification, then run the real campaign. Publish only at the
phase boundary with separate authorization. This order does not require merging
PR #69 and does not relax current hosted gates while the local lane is incomplete.

Historical baseline verdict before implementation: documentation baseline reconciled; existing focused checks
PASS in the limited E1 scope; new LOCAL lane NOT IMPLEMENTED/NOT TESTED; full
candidate qualification HELD; host readiness UNVERIFIED in this turn; economic
objective NOT EVIDENCED; independent security review NOT REVIEWED; remote status
not refreshed; runtime/broker/live/production authority remains false. Source
and tests in this turn do not issue any receipt or activate research execution.


## Execution continuation: source integration evidence

The earlier rows map the unchanged requirements; this delta updates the local
implementation/test gaps without turning E/C/O gaps into source defects. Logs
are retained under external `local-phase-integration-20260918/`; the final manifest
records exact candidate identity, commands, exits, skips and remaining boundaries.

| Scope / acceptance | New source and exercised seam | Evidence / remaining gap |
| --- | --- | --- |
| P1-H/LTS nodes; A02–A04 | `local_pre_p3.py` authenticates full gate DAG; existing `validate_complete_receipt` recomputes native and external leaf bindings; `qualify_pre_p3.py` retains observed selectors/results | Focused P1 and prerequisite tests; actual native/external/operator certificate E/C remains independent |
| P2 temporal facts; A03–A04 | Existing PIT selectors now assert T1/T2/T3 visibility for symbol, listing, corporate-action and provider corrections; provider conflict accepts an explicit known-at cutoff | `pit-green.log`: 58 passed; immutable manifest, three actual P2 certification subprocesses covered by producer test |
| HWC/Pre-P3; A02–A04 | Existing headless/contract/boundary validators reused; protected shared proof store preserves private producer CAS; ten gate bindings and canonical collection/execution evidence | Positive/negative owner and DAG composition tests; installed root approvals and actual per-host certificates remain E/C |
| P3 request/profile/session/custodian; A05–A06 | Discriminated LOCAL schemas, fixed root operation request, current local readiness, fresh SQL admission, exact session identity and custodian intent; campaign CLI uses the same authorization adapter | `local-driver-green.log`: 63 passed, 2 capability skips; LOCAL/GITHUB registration and holdout calculation children plus output rejection (declared sandbox/authority doubles). Anonymous-view capability was separately exercised in `sealed-view-real.log`. Protected cross-UID host still E/C |
| Durable identity and one disclosure; A06 | New forward migration `0031_p3_local_identity`, canonical identity overloads on reviewed claim/start/control/finalize/recover functions, same global disclosure ledger | `local-postgres-governed-final.log`: 2 passed on real PostgreSQL plus exact portable deselection inventory; one claim winner, both cross-lane second disclosures rejected, expired/recovery/no-retry and exact role/catalog rejection; disposable only |
| P3 integration/result readback; A05–A07 | Versioned local fixture plan/authorization/receipt; retained three native streams and full local SQL coverage; host profile rejects mixed lanes | `local-integration-readback.log`: 123 passed; `final-readback-acceptance.log`: 144 passed; `native-source-final.log`: 28 passed using cached, hash-checked Nautilus 1.231.0 wheels; source-native tests do not certify protected host custody |
| Phase-exit; A07–A09 | Current committed SQL reader, exact attempt/output/head, historical review, immutable closure receipt; restartable external export; preserve FAIL/NONE_QUALIFIED/HELD; independent local/published status | `phase-authenticated-composition.log`: 5 passed for export/auth/readback and revoked/expired/source/host denial; SQL current-head/role rejection, status and terminal tests; independent review and authenticated actual economic result remain E/C/O |
| Source closure and publication; A09/A12 | Legacy receipt canonical bytes and hosted attestation/promotion remain unchanged; LOCAL status is opt-in and external | Static/contracts and full regression commands recorded in final manifest; no hosted PASS, push or merge inferred |
| Host activation and actual qualification; A10–A11 | Concrete paths, file custody, command order and forward-only rollback in `docs/operations/local-phase-qualification.md` | Preparation only; protected host activation and independent approvals are required before actual local qualification/research |


## Development verification checkpoint

The source remains uncommitted on parent `977ff03`. The root regression ran with
private native custody and the canonical portable marker selection:
`uv run python scripts/dev.py test --native-custody tests -m 'not runtime_postgres and not host_coupled'`.
It completed with **11,177 passed, 251 skipped, 51 deselected**, exit 0, in 4061.78s.
This was a development run with repairs in progress, not a frozen-candidate
certificate. The final parser/import repairs and newly parameterized LOCAL driver
cases have fresh focused proof: `projection-repair-regression.log` (1,569 passed,
245 skipped), `local-authority-final.log` (181 passed, 1 capability skip), and
`local-driver-green.log` (63 passed, 2 capability skips). The PostgreSQL and
anonymous-view capabilities were exercised separately; no skipped check is
silently counted as a passed host qualification.

Backend: 507 passed, 2 skipped. Dashboard: 199 passed, plus security/mode integration,
typecheck, lint and production build passed. Both Python type-check groups and
lint passed; generated API/P1 contracts, HWC boundaries/status, secret hygiene,
maintainability and repository audit passed in their recorded scopes.

`make test-all` and `make check-contracts` remain **BLOCKED at the clean-source
P1 lineage gate**. Their failure records are retained. Separate owner checks do
not turn either aggregate into PASS. A local commit was requested for review;
no answer or commit was present when this checkpoint was written.

| Axis | Current verdict | Remaining closure |
| --- | --- | --- |
| I | Local source seams implemented, including campaign CLI and phase export | Resolve findings from independent review before activation |
| T | Development regression and focused negative/positive checks passed; skips retained | Frozen-candidate full gates and actual protected host execution |
| E | Disposable PostgreSQL and native source capability exercised | Protected profiles, approved distinct accounts/credentials and host custody absent/unqualified |
| C | HELD; independent security review NOT REVIEWED | Clean committed candidate, actual per-gate and external/operator receipts, current root approval/readback |
| O | NOT RUN | Approved research campaign and truthful economic outcome; holdout only under its independent approval |

The external `local-phase-integration-20260918/RESULT.json` and `FINAL.md` retain
source hashes, exact commands, superseded failures, host observation, preservation
checks and the pending authority boundaries. No official qualification, campaign,
push/merge, installed database/service change or live authority is inferred.
