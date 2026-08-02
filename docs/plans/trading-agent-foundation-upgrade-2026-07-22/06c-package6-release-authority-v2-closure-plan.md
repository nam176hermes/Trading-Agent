# Package 6 Release Authority v2 Closure Implementation Plan

> **For Hermes:** Execute task-by-task with repository-change-safety, TDD, candidate-bound closure, and independent review gates.

**Goal:** Close all six validated Package 6 review findings, retire unsafe same-UID native launch authority from production, restore deterministic test support, and produce a fully verified source candidate ready for a separately authorized independent review.

**Architecture:** Production execution moves behind the fail-closed Release Authority v2 system-manager contract. The legacy native service implementation remains test-only and is excluded from the production ELF. Runtime evidence is one regular-file canonical container created with exclusive descriptor ownership. The same bounded bytes drive publication verification and controller finalization. Runtime activation, PostgreSQL lifecycle, staging, commit, deployment, and live trading remain separately approval-gated.

**Tech stack:** Python 3.11, pytest, C11, Linux descriptor APIs, systemd authority contracts, Make, JSON evidence schemas.

**User authority:** "Đánh giá xem package 6 còn cần làm gì để hoàn thiện, hãy tạo plan và hoàn thiện package 6"

**Safety boundary:** Source and test edits only. No database mutation, service restart, deployment, live execution, live trading, staging, commit, push, or paid review without their separate gates.

---

### Task 1: Restore deterministic test-only native service support

**Objective:** Repair the current canonical-suite setup error without re-enabling the unsafe service in the production binary.

**SwarmBrief:**
```text
GOAL: Build the old protocol service only as test-package6-custodian
SCOPE: Native Makefile, test-only main, native contract tests
DELIVERABLES: native/package6_custodian/tests/test_service_main.c and passing focused tests
PROOF: uv run pytest -q tests/native/test_package6_custodian.py::test_native_helper_and_python_client_exchange_exact_hello_over_socketpair
```

**Files:**
- Create: `native/package6_custodian/tests/test_service_main.c`
- Verify: `native/package6_custodian/Makefile`
- Test: `tests/native/test_package6_custodian.py`
- Test: `tests/foundation/test_package6_custodian_contract.py`

**Steps:**
1. Recover the exact pre-retirement service entrypoint from the immutable historical seal.
2. Verify the recovered SHA-256 against the sealed manifest.
3. Write it only to the test source path.
4. Prove the production ELF excludes service, cgroup, clone, credential, and publication symbols.
5. Run the focused Python/native handshake and full native contract tests.

### Task 2: Close evidence-generation ownership gap

**Objective:** Remove generation-directory ownership from the trust boundary. Publish one regular-file container through a descriptor acquired by exclusive creation before any callback.

**SwarmBrief:**
```text
GOAL: Container publication and rollback touch only the exact created inode
SCOPE: services/paper_runtime/evidence.py and adversarial closure tests
DELIVERABLES: exclusive regular-file create-and-own primitive, BaseException and substitution tests
PROOF: uv run pytest -q tests/foundation/test_package6_controller_closure.py -k 'evidence_container'
```

**Files:**
- Modify: `services/paper_runtime/evidence.py`
- Test: `tests/foundation/test_package6_controller_closure.py`

**Steps:**
1. Add RED tests for mutation after verification, pre-existing names, and incomplete rollback proof.
2. Encode the exact inventory in one canonical JSON container with per-entry size and SHA-256 bindings.
3. Create each publication as an unnamed `O_TMPFILE`, retain a separate descriptor-derived custody authority before linking the final name, and enforce mode `0600`, owner, and link-count checks.
4. Quarantine a linked rollback target with atomic no-replace rename before unlinking it; preserve foreign replacements and return explicit recovery authority when exact cleanup is impossible.
5. Remove the generation-directory publisher and run targeted rollback tests.

### Task 3: Make runtime evidence verification descriptor-pinned

**Objective:** Hash and semantically validate the same bounded bytes read from the already-owned container descriptor.

**SwarmBrief:**
```text
GOAL: One descriptor-bound container snapshot drives digest and semantic verification
SCOPE: runtime evidence reader, publisher callback contract, verifier callsites
DELIVERABLES: snapshot loader, pinned verifier, swap and mutation tests
PROOF: uv run pytest -q tests/foundation/test_package6_controller_closure.py -k 'evidence and (snapshot or swap or mutation or verify)'
```

**Files:**
- Modify: `services/paper_runtime/evidence.py`
- Modify only if required: `services/paper_runtime/__init__.py`
- Test: `tests/foundation/test_package6_controller_closure.py`
- Test: `tests/foundation/test_package6_runtime_integration.py`

**Steps:**
1. Add RED pathname-swap and hash-to-semantic mutation tests.
2. Decode exact inventory once from the owned regular-file descriptor with owner, mode, link-count, size, name, per-entry size, and digest checks.
3. Parse, hash, and semantically verify only the immutable snapshot mapping.
4. Pass the immutable snapshot mapping into publication verification.
5. Re-read and compare the exact container bytes before publication commit.
6. Retain the verified container descriptor, full metadata tuple, and raw-byte digest through final-record and directory fsync; revalidate the exact path identity and bytes in the post-write commit check so mismatch rolls back both GO outputs.

### Task 4: Prove retirement closes native credential and cgroup findings

**Objective:** Demonstrate that the two impossible same-UID primitives are absent from every production artifact and unreachable from production Package 6 authority.

**SwarmBrief:**
```text
GOAL: Unsafe native launch code is test-only and cannot be approved as production runtime authority
SCOPE: production builder, approval contract, source/artifact reachability tests
DELIVERABLES: negative symbol, binary identity, and authority rejection tests
PROOF: make build-package6-custodian && uv run pytest -q tests/foundation/test_package6_custodian_contract.py tests/runtime_release/test_supervisor_v2.py
```

**Files:**
- Modify: `scripts/validate_package6_runtime_approval.py`
- Modify: `schemas/package6-paper-runtime-approval.schema.json`
- Modify if required: `services/paper_runtime/integration.py`
- Test: `tests/foundation/test_package6_runtime_approval.py`
- Test: `tests/runtime_release/test_supervisor_v2.py`
- Test: `tests/foundation/test_package6_custodian_contract.py`

**Steps:**
1. Add RED tests rejecting the retired native helper as runtime launch authority.
2. Bind source readiness to the v2 system-manager supervisor contract with distinct service users, system cgroup ownership, and systemd credential delivery.
3. Keep activation unavailable and launch authorization false until the separately reviewed lifecycle exists.
4. Prove current production binary fails closed and contains no unsafe native runtime symbols.
5. Classify actual runtime activation as `PENDING_APPROVAL`, not source PASS.

### Task 5: Reconfirm low-severity closures

**Objective:** Preserve exact type parity and removal of the forged public verifier.

**SwarmBrief:**
```text
GOAL: bool/float job counts fail and no weak evidence verifier remains exported
SCOPE: validator, schema parity, exports
DELIVERABLES: passing differential and export tests
PROOF: uv run pytest -q tests/foundation/test_package6_runtime_approval.py -k 'expected_job_count or schema' && uv run python -c 'import services.paper_runtime as p; assert not hasattr(p, "verify_evidence_bundle")'
```

**Files:**
- Verify: `scripts/validate_package6_runtime_approval.py`
- Verify: `services/paper_runtime/controller.py`
- Verify: `services/paper_runtime/__init__.py`
- Test: `tests/foundation/test_package6_runtime_approval.py`

### Task 6: Candidate-bound source closure

**Objective:** Produce unchanged-byte evidence for every portable source gate.

**SwarmBrief:**
```text
GOAL: All source, governance, native, focused, finalizer, build, security, and dependency gates pass
SCOPE: Current dirty candidate only
DELIVERABLES: fresh identity, logs, skip inventory, zero failures
PROOF: make test-all; make check-test-skips; make check-critical-coverage; make build-dashboard; make audit-python-source; make audit-dependencies
```

**Steps:**
1. Freeze edits and compute ordered tracked-plus-untracked identity.
2. Run focused Package 6 tests.
3. Run full native tests.
4. Run finalizer and test-governance gates.
5. Run canonical aggregate, dashboard build, security, and dependency gates.
6. Recompute identity and require exact equality.
7. Record runtime activation and disposable PostgreSQL proof as separately approval-gated.

### Task 7: Seal and independent review handoff

**Objective:** Prepare, but do not automatically spend, the final review authority.

**SwarmBrief:**
```text
GOAL: Immutable source seal bound to all closure evidence
SCOPE: Read-only candidate materialization under /tmp
DELIVERABLES: manifest, source archive, evidence hashes, review prompt
PROOF: manifest verification plus unchanged candidate identity
```

**Steps:**
1. Create a new immutable seal outside the repository.
2. Bind exact ordered paths, patch bytes, source archive, gate logs, and manifest digest.
3. Verify seal and live source match.
4. Present one `PACKAGE6_GOAL2_PATCH_V1` review contract containing the exact ordered 42-path inventory, including `docs/implementation/package6-single-container-publication.md`, reviewed patch digest and bytes, matching source diff, seal manifest digest, `seal_integrity=PASS`, `production_authority_status=TEST_ONLY`, empty findings, PASS scope/test fields, and both live approvals false.
5. Keep Package 6 NO-GO until the structured review verdict is PASS.
6. Request separate staging and local-commit Greenlight only after review PASS.
