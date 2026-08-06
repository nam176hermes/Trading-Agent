# Nautilus adoption — Phase 1 offline re-verification

## Scope

This is a fresh, local verification of the already sealed WS01 inputs and
artifacts. It does not fetch upstream, acquire inputs, materialize a toolchain,
or rebuild the engine. The root Python 3.11 dependency graph remains separate
from the isolated CPython 3.12 engine runtime.

**Date:** 2026-08-06
**Checkout:** `a78d0a72332476edbbe00ba40d12cdc8ba9b7566`

## Policy identities

| Policy | SHA-256 |
| --- | --- |
| `engines/nautilus/toolchain-inputs.json` | `bdd7a635f936a46414947e9ffcbb12bd3cf549326adda0ace184f93f0cfbafbe` |
| `engines/nautilus/llvm-toolchain-policy.json` | `7ce6888a582343edc823780485f942c7627f60ce9b37e497c7ce03f403e8d56f` |
| `engines/nautilus/wheel-cache-policy.json` | `f975ff7093ead955ad4667876cd66d768bd68b332e17fb74307135a6b87677f5` |
| `engines/nautilus/engine-build-policy.json` | `e1a9292997b9b4ac821b1292f8e340d92aeffd49422a7b379f7d76dce443b0dd` |

## Results

| Verification | Binding checked | Result |
| --- | --- | --- |
| Provenance | Pinned source identity, legal material, and local policy binding | PASS |
| Rust 1.95.0 | Sealed private input cache and materialized toolchain | PASS |
| LLVM | Sealed cache and materialized LLVM toolchain | PASS |
| Wheel cache | Hash-bound wheel manifest and engine-policy binding | PASS |
| Engine artifact and sealed-input bindings | Offline CPython 3.12 artifact plus wheel/cache, Rust, LLVM, and sandbox bindings | PASS |
| Negative wheel digest | Deliberately incorrect wheel-manifest digest is rejected | PASS |

The verified wheel-cache manifest digest was
`0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b`.

## Selected external artifact generation

The selected sealed generation is
`nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c`.

| Evidence | SHA-256 |
| --- | --- |
| Artifact manifest | `105579383ea3c5e44104bbe162ab78380f7abb5654e15ac3b600beee54ed93d2` |
| Bound input-cache manifest | `ff2e7753974c7b163bd890f9913dbfbb630f80195708ab67d537d72939e0c56b` |
| Bound wheel-cache manifest | `0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b` |
| Sealed engine wheel | `7d3cc69b340536ee6c0e74f4c6954c8a6ed19121df1836a1fab0aad4e43c4f79` |

This generation was built strictly offline from already sealed inputs, then
verified using `--verify --verify-input-bindings --offline`. The prior
`nautilus-1.227.0-cp312-rust-bound` generation remains sealed and unmodified
as the rollback generation; it is not selected by the command template below.

## Offline command template

Substitute only pre-existing, external sealed locations for the angle-bracketed
arguments. These commands are verification-only: do not add acquisition,
materialization, upstream-resolution, or rebuild flags.

```bash
uv run python scripts/verify_nautilus_provenance.py --root .

python3.11 -I scripts/prepare_nautilus_toolchain.py \
  --manifest engines/nautilus/toolchain-inputs.json \
  --cache <sealed-rust-input-cache> \
  --destination <sealed-rust-1.95.0-toolchain> --verify-materialized

python3.11 -I scripts/prepare_nautilus_llvm_toolchain.py \
  --policy engines/nautilus/llvm-toolchain-policy.json \
  --cache <sealed-llvm-input-cache> \
  --destination <sealed-llvm-toolchain> --verify-cache

python3.11 -I scripts/prepare_nautilus_llvm_toolchain.py \
  --policy engines/nautilus/llvm-toolchain-policy.json \
  --cache <sealed-llvm-input-cache> \
  --destination <sealed-llvm-toolchain> --verify-toolchain

python3.11 -I scripts/prepare_nautilus_wheel_cache.py \
  --policy engines/nautilus/wheel-cache-policy.json \
  --engine-policy engines/nautilus/engine-build-policy.json \
  --cache <sealed-wheel-cache> --verify \
  --manifest-sha256 0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b

python3.11 -I scripts/build_nautilus_engine.py \
  --policy engines/nautilus/engine-build-policy.json \
  --python <sealed-cpython-3.12> \
  --artifacts <selected-generation:nautilus-1.227.0-cp312-rust-bound-input-ff2e7753974c> \
  --verify --verify-input-bindings --offline --input-cache <sealed-engine-input-cache> \
  --wheel-cache <sealed-wheel-cache> \
  --wheel-cache-manifest-sha256 0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b \
  --cargo <sealed-rust-1.95.0-cargo> \
  --llvm-toolchain <sealed-llvm-toolchain> --sandbox <approved-sandbox>
```

The provenance command intentionally omits `--verify-upstream`. Refreshing an
upstream reference is a separate reviewed dependency change, not part of this
offline reproducibility gate.

Artifact-only verification is not evidence that the current sealed inputs are
bound to an artifact. The explicit input-binding flag is required for this
gate and fails closed on any cache digest or compiler-identity drift.

## Re-run conditions

Re-run this gate whenever a listed policy digest changes, a sealed input or
artifact is replaced, or the isolated engine runtime changes. A PASS here is
evidence for reproducibility only; it does not authorize a network request,
paper-runtime activation, or any live-trading capability.
