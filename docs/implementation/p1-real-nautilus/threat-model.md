# P1 threat model

Trust boundaries are repository source, external sealed closure, hash-bound
inputs, isolated process output, durable ledger, portfolio projection and local
paper custody.

P1 fails closed on digest substitution, symlink/inode replacement, closure
drift, command/event misbinding, event gaps or duplicates, unstable semantic
digests, look-ahead timestamps, Decimal overflow, oversized/short targets,
partial-output success, ambiguous restart, checkpoint/ledger mismatch and paper
split-brain. Bubblewrap disables network; source rules reject network clients.
Unknown fields, floats, compatibility fallback and client-selected execution
authority are forbidden.

Live, broker, exchange, provider, production, deployment and database mutation
are outside scope and remain unauthorized.
