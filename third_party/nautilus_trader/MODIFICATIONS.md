# NautilusTrader modification log

## Current state

No NautilusTrader source is vendored or modified in this repository.

The selected upstream is `nautechsystems/nautilus_trader` tag `v1.227.0`,
resolved to commit `280ae1762df51a492a4ce71506a40b5c8706def5`. The engine will
remain an external, isolated dependency until a later packet explicitly changes
`distribution_mode` to `vendored_source`, records the complete source manifest,
and documents every patch here.

## Modification record format

Every future entry must state the upstream path, rationale, patch identifier,
author, review evidence, and SHA-256 of the patch bytes. Unrecorded changes are
rejected by `scripts/verify_nautilus_provenance.py`.
