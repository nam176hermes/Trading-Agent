# M7 research candidate and integration order

## Scope

The approved exception in `m7-monthly-exception-decision.json` admits exactly
the February 8, 2018 row from Binance's February 2018 BTCUSDT daily-bar monthly
archive. The daily archive remains invalid. This adds no automatic fallback,
new instrument, research day, holdout access or execution authority.

The adapter retains the original ZIP and CHECKSUM, exact URL/member/row identity,
observation and fetch timestamps, and parser version. It validates all 28 monthly
rows before retaining the selected row. The 2,799 daily inputs keep their existing
normalization. The monthly receipt and provider query identify their distinct
source explicitly. No synthetic daily ZIP is created.

The daily and monthly February 8 rows differ only in closing timestamp; the other
11 fields match. Passing the accepted daily-bar validation does not independently
establish intraday completeness. Publication time remains unknown, and these
archives remain retrospective observations.

## Review finding addressed

The acquisition-to-inventory CLI previously could read/materialize input using
uncommitted source and then label the inventory with the old committed HEAD.
`seal_p3_dataset.py --acquisition-refs` now requires a clean committed checkout
before input reads, captures that identity, and checks again before publishing
the revision inventory. Dirty source or a changed commit is rejected. A failed
mid-run attempt may retain preparation artifacts but does not issue its inventory.

Regression coverage includes dirty-source rejection before input access,
source-change rejection, the complete CLI bridge, full monthly revision replay,
missing-day rejection, malformed monthly bytes and forged provenance.

## Dataset and source binding

The retained research package covers 2018-01-01 through 2025-08-31: 2,800 unique
days, with 2,799 daily acquisitions and one explicitly approved monthly row.
The existing dataset artifact is
`74538543ea149fdfc820e3e0c69b3d129663efdac492a6b239d1912c8831e1a9`.

Freeze the source first. Construct `P3ResearchRevisionInventory` from the retained
entries using the exact committed `canonical_source_identity` and the canonical
JSON policy digest (distinct from the policy file-byte pin). Retain the inventory
in a new private CAS overlay and use the original CAS as a read-only input.
Run `validate_revision_inventory` with the existing query/dataset reference;
verify both the source and policy match and the original package remains unchanged.
No download or fresh materialization is needed when parser and retained bytes
are unchanged. Review evidence and data remain outside Git.

An inventory binds an exact commit/tree. After integration or a squash merge,
rebind through a newly constructed inventory and validate again for the actual
committed source; never edit the old artifact or present candidate evidence as
main/host qualification.

## HWC integration decision

PR #63 imports evidence for `8672cfe58fc1adbfe84df266e16f19d20674e78c`.
It does not qualify this M7 source/policy change. Keep all missing gates HELD.

Recommended sequence, subject to separate merge decisions:

1. Review PR #63 and M7 in parallel; keep #63 unmerged while deciding the final
   source candidate. M7 starts from main after #62, without an active HWC receipt.
2. If M7 is merged first, do not merge #63's older receipt afterward. Supersede
   that import only with operator approval, qualify M7 on protected main, verify
   its fresh attestation, and prepare an import for that exact source closure.
3. If #63 is merged first, refresh the M7 integration branch from that main and
   explicitly retire the now-stale active HWC receipt as part of the M7 change.
   Preserve the signed bytes in evidence/history, regenerate HELD status, and
   qualify the merged M7 source without an active stale receipt. Its later import
   must again pass validation after commit.

Do not carry an obsolete active receipt into M7 qualification: stale receipt
bytes participate in the source closure, recreating the replacement loop fixed
by #62. Do not modify receipt bytes, loosen closure rules, or claim that CI for
one source qualifies another. No PR merge, protected-host qualification, SQL
mutation, service change, official OOS/HOLDOUT/PARITY/PHASE_EXIT or live operation
is authorized by this document.
