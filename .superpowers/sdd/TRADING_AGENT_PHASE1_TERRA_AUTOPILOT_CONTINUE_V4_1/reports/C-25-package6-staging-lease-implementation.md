# C-25 Package-6 staging lease implementation

Implemented the approved P6 test-only staging lease slice.  The private helper
issues a direct `/tmp` child with mode `0700`, verifies the trusted `/tmp`
contract and issued identity, and cleans only through revalidated no-follow
directory descriptors.  Approval, runtime-controller, controller-closure, and
v2 runtime-config fixtures explicitly thread the outer lease; no production,
workflow, validator, authority, or live surface changed.

RED: under an isolated non-`/tmp` pytest root, the pre-change approval fixture
failed in `build_staging_release_authority_v2()` with the generic
`StagingAuthorityError`.  GREEN: focused approval and v2 configuration tests
passed (`139 passed`).  The root-replacement regression proves cleanup refuses
and retains a symlink replacement; the consumer regression permits only the
four approved test modules.

Controller-closure material construction remains blocked locally by the
pre-existing unavailable native descriptor-custody extension; no native build
was performed.  `make audit` and secret hygiene passed.  Contract checking is
blocked by the absent dashboard `openapi-typescript` executable.
