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

If a later packet vendors source, it must switch the metadata mode to
`vendored_source`, add every regular source file to `FILE_MANIFEST.json`, retain
the upstream license, and record each patch in `MODIFICATIONS.md`. The checker
rejects unlisted, changed, special, or symbolic-link source entries.

## Verification

Run:

```bash
uv run python scripts/verify_nautilus_provenance.py --root .
```

The verifier is offline: it validates the recorded identity, notices, license
digest, distribution mode and, when present, the complete vendored file
manifest. Network retrieval and engine build belong to packet 01C.
