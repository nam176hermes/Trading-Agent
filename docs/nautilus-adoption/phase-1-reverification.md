# Nautilus adoption — Phase 1 offline re-verification

## Scope

This is a fresh, local verification of the already sealed WS01 inputs and
artifacts. It does not fetch upstream, acquire inputs, materialize a toolchain,
or rebuild the engine. The root Python 3.11 dependency graph remains separate
from the isolated CPython 3.12 engine runtime.

**Date:** 2026-08-06
**Checkout:** `53b78e8f561746612f7993b1144c74c44b6b1ef7`

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
| Engine artifact and sealed-input bindings | Offline CPython 3.12 artifact plus wheel/cache, Rust, LLVM, and sandbox bindings | FAIL — input-cache manifest binding drift |
| Negative wheel digest | Deliberately incorrect wheel-manifest digest is rejected | PASS |

The verified wheel-cache manifest digest was
`0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b`.

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
  --python <sealed-cpython-3.12> --artifacts <sealed-engine-artifacts> \
  --verify --verify-input-bindings --offline --input-cache <sealed-engine-input-cache> \
  --wheel-cache <sealed-wheel-cache> \
  --wheel-cache-manifest-sha256 0a7693f27a384925698dde2818abd70b894524bae341a62de0ef8f17500d108b \
  --cargo <sealed-rust-1.95.0-cargo> \
  --llvm-toolchain <sealed-llvm-toolchain> --sandbox <approved-sandbox>
```

The provenance command intentionally omits `--verify-upstream`. Refreshing an
upstream reference is a separate reviewed dependency change, not part of this
offline reproducibility gate.

The artifact and input-binding command fails closed for the current external
cache because the sealed artifact references a different input-cache manifest.
This document does not treat artifact-only verification as evidence that the
current sealed inputs are bound to that artifact. Re-materializing an external
input cache or rebuilding an artifact is outside this read-only packet and
requires a separately authorized remediation.

## Re-run conditions

Re-run this gate whenever a listed policy digest changes, a sealed input or
artifact is replaced, or the isolated engine runtime changes. A PASS here is
evidence for reproducibility only; it does not authorize a network request,
paper-runtime activation, or any live-trading capability.
