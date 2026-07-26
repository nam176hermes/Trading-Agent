# Foundation Release Host Final

## Executive result

Package 01 was executed from a standalone external clone rooted at the assessed
commit `e8166622a181307c5aa5869f5900d9845f294e83`. The source result under
evaluation is `798bb88ed70ee27a90abc2c0ec328236487b1674` on
`hermes/foundation-p01-20260723`.

All required Package 01 acceptance commands are green:

```text
TMPDIR=/tmp make ci
exit 0

make audit-release
result=PASS

make test-runtime-release-host
1 passed, 1 skipped, 244 deselected
exit 0

make test-runtime-release
244 passed, 2 deselected
exit 0
```

The operator explicitly approved the exact dashboard update to
`next@16.2.11`. The package manifest and lockfile now resolve Next, `@next/env`,
and the Next SWC artifacts at 16.2.11. Fresh `npm audit` and full CI both report
zero dashboard vulnerabilities.

## Completed controls

- Complete production wheel closure from the canonical lockfile.
- Sealed external wheelhouse with deterministic provenance.
- Offline and no-index dependency installation.
- No global UV cache authority in the host proof.
- Copied virtual environment with no symlink.
- No editable install.
- No checkout or wheelhouse path in runtime `sys.path`.
- Relocatable package imports after release rename.
- Exact approved `next@16.2.11` dependency and lockfile change only.
- Paper-only boundary preserved.

## Fresh verification

```text
uv run pytest -q \
  tests/runtime_release/test_wheelhouse_preparation.py \
  tests/runtime_release/test_offline_wheelhouse.py
8 passed

make prepare-runtime-release-wheelhouse
aggregate_sha256=bcee824c80dd1f967cdb450e72dfaf3ea889f4aa5275754c35216d073c7f6938
artifacts=21
reused=true

make test-runtime-release
244 passed, 2 deselected

TMPDIR=/tmp make ci
root: 2066 passed, 226 skipped, 11 deselected, 1 warning
backend: 247 passed, 2 skipped
dashboard: 158 passed; typecheck, lint and build passed
Python production dependency audits: no known vulnerabilities
dashboard npm audit: 0 vulnerabilities
exit 0

make audit-release
head=798bb88ed70ee27a90abc2c0ec328236487b1674
status=clean
result=PASS

make test-runtime-release-host
1 passed, 1 skipped, 244 deselected
exit 0
```

Two source-only test-isolation defects were repaired: historical evidence now
compares its fixed canonical source path rather than the current clone path,
and the FIFO hygiene case uses a Linux temporary filesystem. The preserved
backend required `TMPDIR=/tmp` for authoritative POSIX ownership and mode
semantics; no backend source was changed.

## Required final commands

```bash
make audit-release
make test-runtime-release-host
make test-runtime-release
make ci
git diff --check
```

## Review limitation

The bounded Hermes worker committed the original Package 01 chain and
isolated-worktree fixes but stalled before returning its final report. A second
bounded Hermes invocation applied the approved Next files but also stalled at
its internal approval boundary. Its npm 10 lockfile output was rejected because
it removed unrelated Sharp metadata. The controller regenerated the lockfile
from a clean standalone clone with npm 11.18.0, verified that the diff was
limited to Next artifacts, and ran every acceptance command independently.
Hermes output is not counted as acceptance evidence.

## Decision

```text
GO - HOST RELEASE PROOF PASSED
```

The release is offline, symlink-free, relocatable, and runnable on the actual
host. Package 01 is closed. At this checkpoint Package 02 had not started and
still required separate exact approval. A later commit-bound disposable run
retained successful restore-semantic evidence, but not an immutable transcript
binding every final command result. Package 02 therefore remains
`PENDING_APPROVAL` in the executable closure matrix. Neither result authorizes
production PostgreSQL access or any active runtime operation. See
`foundation-postgres-approval-evidence.md`.

Superseding note, 2026-07-26: a new source-bound disposable run added a
protected complete transcript, restore proof and cleanup evidence. The current
closure matrix now records Package 02 as `PASS`.
