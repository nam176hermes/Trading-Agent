# P1-U04 X6 stable logical-stage design

## Scope

Repair the exact X6 reproducibility failure without weakening raw wheel equality, native-byte equality, Build A/B process separation, fresh physical staging, source-FD identity checks, Bubblewrap isolation, or the X4 receipt boundary.

This packet does not authorize a third build of the failed candidate, forensic retention, X7 materialization, activation, promotion, network access, broker access, push, merge, deployment, or live trading.

## Frozen failure

Build A and Build B used the same exact X4 source/toolchain/cache/LLVM/Python/Bubblewrap authority and different process/source identities. Production comparison stopped fail-closed:

- ordered wheel member names: equal, 765 members;
- differing members: 17;
- `RECORD`: derived difference;
- native members: 16 differences;
- every differing native member: equal size and exactly 15 differing bytes inside one contiguous 16-byte region;
- the region is a `.rodata` string containing the per-build logical mount token, `stage-8b97e8dd636d141c` versus `stage-58eec2b40e1110b3`;
- all other 748 wheel members: byte-identical.

The raw wheel relationship is therefore correctly rejected. Native code bytes may not be excluded from authority.

## Root cause

`_build_candidate_once` already creates a fresh private physical directory for each build and passes a verified pre-opened stage FD into a new Bubblewrap mount namespace. It also creates a random *logical mount destination* below the external build-root path. Rust embeds that logical source path in 16 native libraries. The random destination changes executable bytes even though the mounted source bytes, toolchain, environment, process, and physical source inode are distinct and exact.

Randomness in a namespace-local path string does not provide Build A/B separation. Separation is already enforced by:

- distinct top-level CLI processes;
- fresh `TemporaryDirectory` physical stages;
- distinct source directory device/inode receipts;
- fresh stage-local venv, Cargo target, dist, home, tmp, and artifact directories;
- verified-FD `--bind-fd` handoff;
- no host materialization of the logical destination;
- sealed disjoint Build A and Build B publication roots.

## Design

Use one fixed policy-conforming logical child name, `stage-0000000000000000`, for every isolated candidate build namespace. Keep all physical staging and receipt identities fresh.

The existing policy accepts `stage-[0-9a-f]{16}` and requires the logical destination to be absent on the host. The fixed name satisfies both rules. Builds remain sequential and run in distinct Bubblewrap namespaces, so the identical path string does not share writable state.

No environment, policy, cache, compiler, dependency, source archive, validator assertion, or semantic comparison is changed. Raw wheel equality remains mandatory.

## TDD and acceptance

Add one regression proving the logical-stage token is the exact fixed policy-conforming value across calls. It must fail against the current random implementation before the source change.

Then require:

- focused regression PASS;
- full portable candidate-closure suite PASS;
- fresh spec review PASS;
- fresh security/replay review PASS;
- failed Build A/B evidence hashes frozen before recoverable cleanup;
- exact-source X4 re-preflight and fresh receipt reviews PASS;
- one new Build A and one new separate-process Build B only;
- raw wheel equality PASS, native inventory equality PASS, and final artifact publication PASS.

## Circuit breaker

Maximum two implementation/review rounds.

- Round 1: stable namespace-local logical path.
- Round 2, only if Round 1 is rejected or still drifts: review an exact Rust `--remap-path-prefix` authority change.

If Round 2 cannot pass, stop with `P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`.
