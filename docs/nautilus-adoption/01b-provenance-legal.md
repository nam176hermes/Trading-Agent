# Nautilus adoption — 01B provenance and legal boundary

## Selected upstream

This packet pins official NautilusTrader upstream tag `v1.227.0` to immutable
commit `280ae1762df51a492a4ce71506a40b5c8706def5`. The tag object is separately
recorded as `0ccb5b55879c072a6e07fc7cbe5297c53c378107`, so a future build must
verify tag-to-commit resolution rather than rely on a mutable branch or version
range.

The selected release is listed by the upstream project as an immutable release;
this repository nevertheless treats the recorded commit and file digests—not
the release name—as the authority.

## Distribution boundary

`distribution_mode` is currently `external_pinned_upstream`. There is no
vendored NautilusTrader source in this repository, no engine dependency added
to an existing Python 3.11 graph, and no build/runtime activation.

Packet 01B accepts only `external_pinned_upstream`; any source directory,
symbolic link, special file, or extra vendor entry is rejected. A later packet
that vendors source must first add a separately reviewed trusted upstream file
manifest and patch-record verifier. It must not switch `distribution_mode` or
claim that a self-authored manifest proves source provenance.

## Verification

Run:

```bash
uv run python scripts/verify_nautilus_provenance.py --root .
```

Without `--verify-upstream`, the verifier checks local, reviewed bytes only.
`--verify-upstream` additionally resolves the exact tag object and peeled commit
against the official remote. Network retrieval of an engine artifact and engine
build still belong to packet 01C.
