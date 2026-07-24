# Foundation Release Tamper Evidence

## Wheelhouse verifier

`tests/runtime_release/test_offline_wheelhouse.py` covers these fail-closed cases:

- Wheel content changed after manifest creation.
- Locked wheel removed.
- Lockfile version changed.
- Symlink added.
- Unexpected file added.
- Wheel made writable after sealing.

Focused result:

```text
6 passed
exit 0
```

The verifier checks regular-file type, ownership, link count, permissions, filename, wheel metadata, package and version, lockfile URL and hash, file size, artifact SHA-256 and aggregate SHA-256.

## Release verifier

The runtime-release suite also covers source-file tampering, extra source files and unapproved tracked symlinks. The host proof rejects every symlink in the built artifact and scans release files for the external wheelhouse path.

## Missing artifact behavior

The missing-artifact unit case fails during wheelhouse verification. The release builder verifies the inventory before creating a promotable target, and failed builds remove staging output. There is no online fallback.

## Network policy

Wheelhouse installation includes all of:

```text
--offline
--no-index
--find-links
--no-cache
--require-hashes
--only-binary=:all:
```

The proof is UV policy enforcement rather than an operating-system network namespace. The builder cannot silently consult an index because index lookup and network use are both disabled.

## Reviewer status

The requested independent subagent review did not run. It returned `HTTP 404: No active credentials for provider: openai`. This result is not counted as review evidence. Controller review and executable checks remain the available evidence until an independent reviewer is authenticated.
