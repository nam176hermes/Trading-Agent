# P1-U04 X4 Receipt Finalization Architecture

## Status and authorization

This is a new bounded architecture packet following the terminal
`P1_U04_ARCHITECTURE_ESCALATION_REQUIRED` decision recorded at
`e04e13ebb8a4c29b5d76f3b0efb2ab78671e1b5e`. It starts from that exact source
state and is limited to the three Important findings left by the prior packet.
It permits at most two implementation/review rounds.

The packet may change only the candidate X4 receipt validator, the shared
sealed Build A/B result validator, the final publication barrier, the schema-7
consumer validation, and their portable tests. It must not run either native
candidate action, perform X4 re-preflight, mutate external authority or output
roots, alter active/rollback 1.227 authority, activate or promote 1.231, open
X5/U05, use the network, install packages, or accept ambient or caller-selected
compiler/package/cache authority.

## Threat model

The approved threat model is the existing cooperative host: inputs may be
malformed, stale, replaced, replayed, symlinked, hardlinked, or changed at an
explicit validation/publication boundary, but a hostile process running as the
same UID is not assumed to race every instruction. Defending against that
stronger actor would require an immutable snapshot or separately protected
execution identity and is outside this packet.

All named trust boundaries remain fail-closed. The cooperative-host scope does
not permit validation to be skipped, weakened, or replaced by self-asserted
receipt fields.

## Root causes

The prior implementation has three contract mismatches:

1. `_candidate_policy_receipt()` invents a `policy_sha256.toolchain_inputs`
   member, while canonical `p1-u04-x4-authority-preflight-v1` receipts carry
   that digest only at `checks.toolchain_inputs.sha256`. The X4 validator also
   rejects canonical `checks.network_capability` instead of requiring the
   literal `DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL` value.
2. Build B validates sealed A/B results, then separately rereads receipt bytes
   to calculate final digests. Those later bytes are not the bytes returned by
   the validated records, and A/B are not both revalidated immediately before
   final publication.
3. The schema-7 consumer checks receipt mode, canonical JSON, digest, and X4
   digest only. It does not validate the complete three-file Build A/B record
   or cross-bind the candidate, policy, authority, wheel/core, process, and
   source identities to the final reproducibility receipt.

## Canonical X4 receipt contract

The validator must accept exactly the already-issued canonical schema shape;
this packet does not define a replacement producer or schema version.

`policy_sha256` contains exactly these five members:

```text
cargo_registry
engine_build
input_cache
release_provenance
wheel_cache
```

`checks` contains the existing canonical members, including:

```text
network_capability = DISABLED_BY_BUBBLEWRAP_UNSHARE_ALL
toolchain_inputs.result = PASS
toolchain_inputs.exit_code = 0
toolchain_inputs.sha256 = c643e04fc48f8461b0bb87b680252ee873384feacf9306fde344faf5cca7ad93
```

The existing exact checks for canonical JSON bytes, receipt digest, mode
`0400`, task UID, link count `1`, verdict, review round, complete-receipt hash
and size, HEAD/tree, policy values, live external identities, build-parent
state, absent output roots, provenance, host lane, rollback authority, and
root disjointness remain mandatory. Receipt paths never select external
authority.

## Shared sealed Build A/B validation

There must be one production validation contract for a sealed intermediate
build result. The builder and schema-7 consumer may expose local adapters for
their error types, but they must enforce the same literal contract rather than
maintain a weaker downstream subset.

For label `A` or `B`, validation requires:

- the policy-derived `build-a` or `build-b` directory is a task-owned,
  non-symlink directory with mode `0500`;
- the directory contains exactly the candidate wheel, `artifact-core.json`,
  and `build-receipt.json`, with no extra or missing entry;
- all three files are regular, task-owned, single-link, non-symlink files with
  mode `0400`, stable identity during each bounded read, and bounded size;
- wheel bytes and artifact-core bytes match the filename, size, and SHA-256
  records in `build-receipt.json`;
- the receipt is canonical ASCII JSON with schema
  `p1-u04-candidate-build-result-v1`, exact field set, expected label/kind and
  exact three-file list;
- candidate HEAD/tree, the five policy hashes, authority identities,
  sanitized environment digest, wheel/core records, process identity, source
  descriptor identity, and X4 receipt digest satisfy the existing strict
  Build A/B contract;
- the artifact core's wheel record equals the receipt wheel record and the
  actual wheel bytes.

Validation returns one immutable-in-practice value containing the already-read
wheel bytes, parsed artifact core, parsed build receipt, and the SHA-256 of the
exact validated canonical receipt bytes. Callers must not reread a receipt to
derive evidence from different bytes.

## Final publication barrier

Build B continues to execute exactly one native primitive in its own CLI
process and publish its sealed intermediate record before comparison. After
all raw-wheel, native/core, source, process, policy, authority, and environment
comparisons pass, it must perform a final barrier in this order:

1. revalidate the exact X4 receipt for phase `FINAL` and compare the returned
   document with the initially validated X4 document;
2. reload and fully validate sealed Build A and Build B using the shared
   intermediate-result contract;
3. compare those final validated values with the earlier validated A/B values;
4. build the final reproducibility object using only the receipt digests and
   identities returned by that final validation;
5. atomically publish final artifacts without another A/B receipt read.

Any failure before the no-replace final rename leaves final `artifacts`
absent. Build A remains sealed and unchanged. A failed or mutated Build B is
not accepted as final evidence. No validation helper may repair, thaw, or
rewrite an intermediate result.

## Schema-7 downstream contract

The schema-7 materializer must reopen both policy-derived sealed intermediate
directories and apply the full shared Build A/B contract. It then cross-binds:

- exact Build A and Build B receipt SHA-256 values;
- the common X4 receipt SHA-256;
- candidate HEAD/tree;
- the five policy hashes and authority identities;
- sanitized environment digest;
- wheel filename, size, and SHA-256;
- artifact-core filename, size, and SHA-256;
- distinct process identities and distinct source descriptor identities.

The two validated artifact cores must equal each other and the final artifact
manifest core. Their wheel records must equal the final wheel record. The two
intermediate labels, kinds, process identities, and source identities must be
in the expected A/B positions. A directory containing only a minimal
`build-receipt.json`, or a valid receipt beside missing, extra, or mismatched
wheel/core files, must fail.

## TDD and review

RED tests must use literal canonical fixtures, not production helpers to
construct expected schemas. They must first prove:

- the current validator rejects the issued canonical X4 receipt;
- final publication can bind bytes read after the last full A/B validation;
- schema-7 accepts minimal or incompletely cross-bound A/B directories.

GREEN tests must cover canonical X4 acceptance plus rejection of synthetic
policy/toolchain layout, wrong network capability, minimal A/B receipts,
missing/extra files, wrong modes or links, receipt/wheel/core drift, label and
identity substitution, post-validation A/B mutation, and mismatched final
cross-bindings. Existing split-process, one-build-per-action, receipt replay,
forensic, descriptor-closure, and portable candidate-closure tests must remain
green with zero skips and zero xfails.

Implementation verification is portable-only and uses:

```text
TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run pytest -q tests/nautilus_upgrade/test_v1231_candidate_closure.py
```

Fresh spec/code and security/replay reviewers inspect the exact implementation
base-to-candidate range. Any Critical or Important finding after round 2 stops
with `P1_U04_ARCHITECTURE_ESCALATION_REQUIRED`.

## Alternatives rejected

1. Update the X4 receipt producer to match the synthetic validator: rejected
   because the already-issued canonical schema is authoritative.
2. Copy only more receipt shape checks into schema-7: rejected because a
   second partial validator caused the current divergence.
3. Bind final evidence to a fresh raw receipt reread: rejected because it can
   bind bytes that were never fully validated.
4. Add immutable snapshots or a separate protected UID: rejected as outside
   the approved cooperative-host threat model and the smallest repair packet.

## Completion condition

The packet completes only when the exact implementation range passes portable
tests and fresh spec/code plus security/replay review within two rounds. That
authorizes X4 re-preflight only. A new receipt bound to the then-current exact
HEAD/tree and fresh X4 reviews must still pass before Build A or X5 can run.
