# NautilusTrader modification log

## Current state

No NautilusTrader source is vendored or modified in this repository.

The selected upstream is `nautechsystems/nautilus_trader` tag `v1.227.0`,
resolved to commit `280ae1762df51a492a4ce71506a40b5c8706def5`. The engine will
remain an external, isolated dependency until a later packet adds a separately
reviewed trusted upstream source manifest and extends the verifier. Packet 01B
rejects every vendored source tree, even if it supplies a self-consistent
manifest.

## Modification record format

Every future entry must state the upstream path, rationale, patch identifier,
author, review evidence, and SHA-256 of the patch bytes. A later verifier may
accept a vendored tree only after it validates each change against that trusted
upstream manifest and these records.
