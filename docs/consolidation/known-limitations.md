# Canonical Source Consolidation: Known Limitations

These limitations are intentional boundaries of the approved source
consolidation. They are not silently normalized by the Task 8 equivalence
result.

## Retained architecture boundaries

1. **Separate dependency environments remain.** The root control plane, flat
   research backend, and dashboard retain independent uv/uv/npm lockfiles and
   installation directories. There is no uv workspace, npm workspace, or
   unified dependency graph.
2. **The legacy backend remains flat.** It is preserved below
   `legacy/research-backend` with its existing import, CLI, working-directory,
   and research-only behavior. Repackaging is a separate design and refactor.
3. **Runtime data remains external.** Protected configuration, databases,
   reports, models, signals, scratchpads, run state, and service state are not
   source and are not imported into this Git root.
4. **Old repositories remain read-only rollback sources.** Consolidation does
   not archive, delete, reset, clean, or rewrite them. A later archival
   decision requires separate operator approval.
5. **Release Authority v2 is not implemented.** The existing sealed Phase 4B
   authority remains immutable. This plan supplies source-equivalence input
   to a later, separately reviewed Release Authority v2 plan.
6. **There is no production cutover.** No service, dashboard, scheduler,
   release path, protected config, database, port, or runtime authority points
   at this canonical checkout as a result of consolidation.

## Validation observations

- A clean checkout needs component dependencies installed from the existing
  lockfiles before component validation. Aggregate targets do not install or
  update dependencies automatically.
- The locked dashboard dependency installation reported four registry
  advisories: one low and three moderate. Task 8 did not run an automatic fix,
  change a dependency version, or alter the package lock. Advisory triage and
  any dependency update require separate component review.
- The contract generator retains its existing deprecation warning. Generated
  contracts nevertheless reproduced without drift.
- Dashboard Node tests retain existing module-type warnings. All 76 tests,
  the isolated integration check, typecheck, lint, and production build passed.
- Validation creates ignored virtual environments, dependency directories,
  caches, bytecode, and build output. These are operational artifacts, not
  source, and must be removed before the clean release audit.

None of these observations enables live trading or expands the approved
source-consolidation scope.
