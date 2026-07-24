# Phase 4B immutable release evidence

Captured: 2026-07-13 UTC.

The independently reviewed immutable authorities are:

- application: `fdc085a05019d700ccbce59370941e2c97ef899a`;
- backend: `41f055b48033714c660f44cc20498b7545366e75`;
- interpreter identity: `CPython 3.11.15` for both releases.

The final user-owned offline stage is:

```text
/home/thenam176/.cache/trading-agent-phase4b-stage-fdc085a0-41f055b4-sealed-final
```

The previous stage was revoked after pre-provisioning reconciliation found that
an empty historical PostgreSQL status table was exposed as current `0/0`
order/trade counts. Contract `2.0.0` preserves those unavailable values as
required nullable fields.

The builder exports exact Git objects, runs only a bootstrapped standard-library
builder before attestation, performs dependency sync with `UV_OFFLINE=1`, and
does not copy either worktree. A standalone verifier is pinned independently
and runs through `/usr/bin/python3 -I`; staged Python or helper code is never
executed until both releases pass complete-set attestation.

Final staging authority:

```text
metadata SHA-256: f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c
application manifest canonical digest: 9f7676bc8db90c09675d040947ec24938d3834f6cc243ee1d4c07bf07bdfbd0a
application manifest file SHA-256: d0791df780730ead6204fd7d1d261838a8b289c43611b3ab5e05f4791e0275fe
backend manifest canonical digest: 36c77df39a260c5ea1badfdf9f0091cd9930caba019e8c970b2f7cc3aaa07718
backend manifest file SHA-256: cddbed79fd1e5768b61780a62b2159bafd664139a8210fd8b28f734fd31978a9
command manifest SHA-256: 5a1add2bd74454abfde59e99b4ee49d6c0d4564f2544e7e56749030764a99f78
runtime authority SHA-256: 75442a9b43424ac9e2f0d8227a662c3af4cc3d24f46c40109881e4b3ed7cbe4d <!-- gitleaks:allow -->
standalone verifier SHA-256: 8f7cf1bc3161f64e2f9814547c4ccd8a30d67a9bade1268e79767d2e965ca5d5
```

The canonical digest intentionally excludes the manifest newline; the file
digest covers the exact stored bytes. The 5.1 GiB stage has zero symlinks,
special files, hard-linked regular files, `.pyc` or `__pycache__`.

After its final seal, this stage was audited only with `/usr/bin/python3 -I`
running the external standalone verifier plus `find`, `stat`, `sha256sum`,
`du` and `jq`. Both release trees and all nine unit hashes passed. No
interpreter or helper inside the sealed stage was executed afterward.
Production `/opt` and system services were not changed.

The metadata binds `seal_version=1` and the exact absolute stage path, so a
digest cannot be replayed against a different staging directory.

Revoked and retained only as evidence:

- `stage-c5707b4-3093542` / `a06a1160...`;
- `stage-527f5306-07621814` / `7aeaa300...`, mutated after review by seven
  Python import-cache files;
- `stage-527f5306-07621814-sealed-final` / `7aeaa300...`, superseded because
  the deterministic metadata did not bind its stage path.
- `stage-527f5306-07621814-sealed-final-v2` / `75f80edf...`, superseded by
  principal-bound Job API authentication, root-owned provisioning snapshots,
  and the execution-off backend default.
- `trading-agent-phase4b-stage-9c6b08c1-41f055b4-sealed-final` /
  `27a83612...`, superseded by the PostgreSQL-backed Control API, its dedicated
  read-only role, and current protected safety-evidence binding.
- `trading-agent-phase4b-stage-5eb40054-41f055b4-sealed-final` /
  `1435864c...`, revoked because missing current order/trade authority was
  represented as `0/0` instead of unknown.

The seven files share timestamp `2026-07-12 16:12:54 -0400` and correspond to
an import of `packages.runtime_release` after the old metadata was emitted.
No retained shell history identifies the exact review command, so no command is
invented. Final-seal ordering and the no-post-exec rule prevent recurrence.
