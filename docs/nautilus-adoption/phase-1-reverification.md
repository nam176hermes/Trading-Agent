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

## Execution-simulation runtime closures

Packet 04E0 fix round 4 materialized the selected external generation
`runtime-closure-v8-simulation` from the unchanged sealed
`runtime-closure-v3` file inventory, the selected input-bound engine artifact,
and the repository launcher. The sealed external staging tree passed root
attestation before publication with Linux `renameat2(RENAME_NOREPLACE)`, retained
the same directory identity, and passed independent destination re-attestation
afterward. The destination parent is opened and checked against its preflight
identity, and no replace-on-exists fallback is permitted when the no-clobber
syscall is unavailable. After rename commits, the parent descriptor remains
open while the caller records the published state, checks destination identity,
and completes destination re-attestation; a later descriptor-close error cannot
skip those checks or route the generation through unpublished staging cleanup.
Destination identity is checked against the staged identity immediately before
and after re-attestation. The materializer has no acquisition or build mode. Its
Packet 04E1 retains v8 as the transport-only rollback and preserves v9, v10,
and v11 as rejected forensic generations. The v11 candidate does not satisfy
the fixed-point and trigger-order grammar, so it is not selected authority.
The checked-in source policy SHA-256 is
`cf44792684f720cf6cda42f6de86bf7aadd10abbc437efa112fc37c7952aa740`;
it binds semantic profile `nautilus-execution-simulation-v2` and launcher
SHA-256 `e8012838ba6eca788de98d6520123f769602d28f8f57662395b2e0d54b3dab8f`.
This source policy has not materialized an external closure. V12 is reserved
for Task 4's real execution-parity candidate.

| Closure | Status | Profile | Manifest SHA-256 | Attested closure SHA-256 | Validator |
| --- | --- | --- | --- | --- | --- |
| `runtime-closure-v3` | rollback | `zero-order` | `69cb87568361ccd6324550fb3823956c64e073b4cf09e674d7eb0883f844c044` | `18c9ba4af073ae953e0115f577423348b6d454c158da59cbcbd3c9e34a22856f` | `nautilus-backtest-result-v1` |
| `runtime-closure-v4-simulation` | rejected forensic candidate | `execution-simulation` | `60fa9da972a1bb967f1117318b56e513301e3868536d8efb7f09284b02e5459c` | `f6080176c8a2c742a4f60a92be07a2e9078edaef97572e33c54c4431dd646cf2` | `nautilus-backtest-simulation-result-v1` |
| `runtime-closure-v5-simulation` | rejected forensic candidate | `execution-simulation` | `60fa9da972a1bb967f1117318b56e513301e3868536d8efb7f09284b02e5459c` | `f6080176c8a2c742a4f60a92be07a2e9078edaef97572e33c54c4431dd646cf2` | `nautilus-backtest-simulation-result-v1` |
| `runtime-closure-v6-simulation` | rejected forensic candidate | `execution-simulation` | `60fa9da972a1bb967f1117318b56e513301e3868536d8efb7f09284b02e5459c` | `f6080176c8a2c742a4f60a92be07a2e9078edaef97572e33c54c4431dd646cf2` | `nautilus-backtest-simulation-result-v1` |
| `runtime-closure-v7-simulation` | rejected forensic candidate | `execution-simulation` | `60fa9da972a1bb967f1117318b56e513301e3868536d8efb7f09284b02e5459c` | `f6080176c8a2c742a4f60a92be07a2e9078edaef97572e33c54c4431dd646cf2` | `nautilus-backtest-simulation-result-v1` |
| `runtime-closure-v8-simulation` | transport-only rollback | `execution-simulation` | `60fa9da972a1bb967f1117318b56e513301e3868536d8efb7f09284b02e5459c` | `f6080176c8a2c742a4f60a92be07a2e9078edaef97572e33c54c4431dd646cf2` | `nautilus-backtest-simulation-result-v1` |
| `runtime-closure-v9-simulation` | rejected semantic precision candidate | `execution-simulation` | `2920be39c588d52c54dee735f6bc9dc6507b7650572fa7dcd8a9da84981e90a5` | `30397513e1b4342b4182dfbc143a3b24426e7186f690172bacc0ed7ec9598345` | `nautilus-backtest-simulation-result-v1` |
| `runtime-closure-v10-simulation` | rejected pre-grammar-repair candidate | `execution-simulation` | `f2f16b61f46db2ca86b16dc47a13633a9b3aee3c74ed4ecca084e7c453990d0d` | `fed156160b837b75564de15e50236441851376ea67ac3bc60e58a911c81cd386` | `nautilus-backtest-simulation-result-v1` |
| `runtime-closure-v11-simulation` | rejected fixed-point/trigger-order candidate | `execution-simulation` + `nautilus-execution-simulation-v1` | `a0a327420767fc2ad52bb731b1297b9c6ea85d3965bbf53523cd17ca393b6ceb` | `ce70493ede59e19aeda7496da553ea97f7c9265d91ba95bf8a2b73e295de6597` | `nautilus-backtest-simulation-result-v1` |
| `runtime-closure-v12-simulation` | reserved for Task 4 real execution-parity candidate | — | — | — | — |

The v11 manifest and attestation hashes above are retained as forensic
evidence, but v11 must not be selected, overwritten, or repaired. V4–v7, v9,
and v10 remain rejected; v8 remains the transport-only rollback. V12 remains
reserved and no external v12 directory was created by this source-stabilization
packet. The external closures are not committed and do not authorize a service
start, paper-runtime activation, broker access, or live trading.

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
