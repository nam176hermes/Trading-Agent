# Synthetic rehearsal provenance

This route exercises paper infrastructure only. The seed-9 fixture was historically
screened and provides no economic or real-holdout evidence. Its generator, seed,
3166-row digest, segment boundaries and policy are fixed. It must never be labelled
as Binance archive data, historical vintage verification, or an official producer run.

`prepare_p3_synthetic_rehearsal.py --output-root <new-absolute-directory>` creates
all three segments and an independently read-back backup before outcomes exist.
It emits a proposal only. The source must be clean and unchanged throughout.
Qualification, an actual PIT run, custody, reviews, and workflow admission are
separate prerequisites. A source change requires fresh HWC, Q, S6, promotion and
integration fixture evidence before runtime rehearsal.

## Receipt review contract

Before approving a `p3-github-synthetic-inputs-v1` receipt, the independent reviewer
must verify the exact source, InputSet, policy, generation, commitment, environment,
integration receipt, actual GitHub run/attempt and expiry. The InputSet must carry
that generation reference and a synthetic PIT proof pointing to that commitment.
The reviewer must run `validate_synthetic_commitment` and
`validate_synthetic_dataset` for **RESEARCH, HOLDOUT and BUFFER**, against the exact
proposed generation, source and policy, and retain the commands and results.
This reconstructs the partition/day-receipt chains and fixed rows before approval.
Backup creation also reconstructs all segments and reads back every object.

Parent admission intentionally reads commitment metadata rather than sealed
HOLDOUT/BUFFER rows. Its trust therefore depends on that exact reviewed commitment;
metadata-only checks are insufficient evidence for issuing the receipt. Every
parent/session fence re-reads the protected receipt and enforces the binding in
both directions. Genuine GITHUB legacy REHEARSAL receipts with market InputSets
are rejected. Recorded-API fixtures remain test evidence only. OFFICIAL, LOCAL and
FAKE execution cannot admit the typed synthetic route.

## Custody and execution

The custodian must take exactly the ordered 365 HOLDOUT row references followed by
the BUFFER row reference from the reviewed synthetic commitment's datasets. Before
sealing, compare that inventory with the reconstructed datasets. The existing
`seal_custody_rows` reads those rows, encrypts the complete canonical bundle, decrypts
and compares it, reads back its backup, then builds the custody attestation.
`CustodianAttestation.holdout_commitment` hashes the ordered row references,
plaintext-bundle digest, ciphertext reference, access policy and principals.
A matching dataset reference alone is insufficient. Retain an explicit comparison
of the attested row inventory with those exact generated dataset rows before
approving the HOLDOUT operation; do not manufacture or reuse a custody receipt.

The host helper and same-owner HOLDOUT → PARITY → PHASE_EXIT runtime sequence,
including expiry, cancellation, owner loss and no-replay proofs, require their own
fresh review and execution. They are not established by these source tests.
Synthetic v2 readback remains historical rehearsal evidence; v1 phase-exit export
and official promotion refuse it. Keep all authority flags false. Real-data
eligibility and consumption review remain mandatory for any official campaign;
NONE_QUALIFIED is a terminal result and never opens holdout.
