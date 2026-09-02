# HWC implementation decisions v1

## T-HWC-100B recovery seam

The frozen intent schema records the desired file digest but intentionally does
not retain raw kill-switch reasons or desired bytes. Therefore the state-store
accepts the policy-frozen activation bytes, and the journal accepts the
idempotency digest needed to locate an applied record, as explicit arguments.
Each value is checked against the immutable intent before use.

Recovery of an activation that stopped before source mutation requires the same
authenticated request to reproduce byte-identical policy output. Recovery never
guesses or persists the raw reason. A post-mutation retry can instead prove the
result through the desired file digest. For clear, `desired_file_sha256` binds
the prior kill-switch bytes that must become the command-specific tombstone.

This is the minimal fail-closed resolution of the plan's incompatible
`activate_kill_switch(intent)` / `create_applied(applied)` signatures and its
requirement for exact crash recovery without raw reason persistence.
