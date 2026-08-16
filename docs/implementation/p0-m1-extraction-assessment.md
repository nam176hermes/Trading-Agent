# P0-M1 optional extraction assessment

## Decision scope

This assessment asks whether moving one pure responsibility out of the already
qualified P0 implementation is demonstrably lower risk than leaving the code
in place. File size alone is not evidence for extraction. The mandatory P0-M1
packet is a guardrail packet, and an extraction would be a separate packet with
stronger qualification.

The reviewed baseline is
`e0baa410cdcf0de4344d58ad82fd8a56788f84df`. The strict hotspot manifest has
three entries, not four:

| Hotspot | Policy | Baseline bytes | Baseline and current Git blob |
| --- | --- | ---: | --- |
| `scripts/t_g03_capability_topology.py` | `FROZEN_FOR_GROWTH` | 362,662 | `e33a9da0f04a936f4482cd8ab56cc9d8326ec94c` |
| `scripts/check_artifact_firewall.py` | `FROZEN_FOR_GROWTH` | 141,810 | `e38a632544590e04700391a86ff297aac0f23286` |
| `scripts/check_p0_ci_closure.py` | `MONITOR` | 43,300 | `9e788e84c15e8c4cedeee71c2a52713cc60d7841` |

`scripts/check_p0_maintainability.py` is the small P0-M1 guardrail, but it is
not a governed hotspot in the manifest and is not an extraction target. This
reconciles the task brief's reference to four files with the authoritative
manifest and plan, which govern the three files above.

The responsibility-boundary document assigns capability/authority
classification, receipt validation and publication, semantic projection, and
topology aggregation to the topology hotspot; evidence custody and artifact
validation/publication to the firewall; and historical closure proof to the
closure checker. The characterization index binds C01-C16 to exact collected
pytest nodes, but it does not by itself prove an untested import shim or a new
module boundary.

## Eligibility gate

A proposal must satisfy every item below. `NOT PROVEN` on any item is a NO-GO.

| ID | Required criterion | Assessment rule |
| --- | --- | --- |
| E01 | pure deterministic computation/data | The moved symbols must have no authority-sensitive effect or policy decision. |
| E02 | no filesystem mutation | No write, create, rename, unlink, chmod, or directory mutation may move. |
| E03 | no descriptor/inode custody | No descriptor, inode identity, lineage, or TOCTOU check may move. |
| E04 | no subprocess | No child process or executable validation may move. |
| E05 | no network | No network access or network-derived authority may move. |
| E06 | no environment authority | No environment variable may grant, select, or describe authority. |
| E07 | no wall-clock authority | No current time or validation-date authority may move. |
| E08 | no receipt publication | No receipt or evidence publication path may move. |
| E09 | no capability classification | No capability/authority state mapping or transition may move. |
| E10 | no PASS/FAIL/DEFERRED decision | No terminal or lane outcome decision may move. |
| E11 | no schema/version/path/error-code change | Values, spelling, locations, exceptions, and externally observed contracts must remain exact. |
| E12 | no semantic-result meaning change | The stable semantic projection and digest meaning must remain exact. |
| E13 | existing characterization already covers it | Exact collected tests must cover both behavior and any compatibility shim/API surface needed by the move. |
| E14 | no more than about 500 moved logical lines | The exact symbol set must remain a small single responsibility. |
| E15 | extraction can preserve the old import/API surface via a shim if required | Existing callers and imports must continue to resolve identically. |

The architectural GO condition is additional: passing E01-E15 is necessary,
but the move must also be proven lower risk than leaving the qualified blob
unchanged.

## Candidate review

### Canonical JSON helpers

Plausible symbols are topology's `canonical_json_bytes` (4 physical lines),
the firewall's `_canonical_json_bytes` (4), and the closure checker's
`_canonical` (5). They are deterministic and individually satisfy E01-E10 and
E14. They are not one interchangeable implementation: the closure form appends
a newline, while the other forms do not. The public topology helper could be
retained as a shim, so E15 appears feasible for that symbol.

The indexed C01/C02 projection proof and C11-C15 artifact proofs exercise
canonical bytes transitively, and other tests call the current topology helper.
No exact indexed node proves the proposed new-module import boundary or the
compatibility shim itself. Therefore E13 is not proven for a move. Consolidating
four or five lines would also modify qualified hotspot blobs and add a reviewed
first-party dependency without reducing an authority-bearing responsibility;
the required lower-risk case is not established. Candidate status: **NO-GO**.

### Digest helpers

Plausible symbols include topology's `closed_node_proof_digest`,
`completeness_sha256`, `payload_sha256`, `native_completeness_sha256`, and
`external_completeness_sha256` (20 physical lines total), plus the firewall's
`semantic_result_sha256` and `manifest_payload_sha256` (11 total).

Although the byte hashing is deterministic and bounded by E14, these helpers
select receipt fields, bind closed-node proof, validate receipt acceptance,
and define stable semantic-result meaning. The exact C01/C02 tests intentionally
prove that meaning, and C05-C13 prove fail-closed receipt/evidence consequences;
that coverage is evidence that these are policy boundaries, not generic digest
utilities. The group fails E08, E10, E11, and E12 and crosses the forbidden
receipt-acceptance and semantic-policy categories. Candidate status:
**NO-GO**.

### Immutable constants and pure schema field sets

Plausible data includes receipt schema strings and key frozensets in topology,
manifest schema strings in the firewall, and `TOP_KEYS`, `ENTRY_KEYS`,
`SAFE_STATES`, and status maps in the closure checker. These are small,
deterministic data and satisfy E01-E08 and E14.

They are nevertheless the executable definitions of schema/version keys,
evidence paths, capability/authority classifications, accepted closure states,
and PASS/FAIL/DEFERRED meaning. Splitting only the apparently neutral values
would still change the import graph of qualified hotspots. C03-C09 and C11-C16
cover consequences, but no exact characterization proves a new constants
module and compatibility surface. The set fails E09-E13, depending on the
specific constant, and crosses the forbidden classification, receipt
acceptance, validation-date, and semantic-policy categories. Candidate status:
**NO-GO**.

### Pure dataclasses

The closest pure data-only types are topology's `_PolicyStageFailure` (5
physical lines), `InventoryRow` (4), and `ClosureRow` (8), and the closure
checker's `_YamlLine` (3). They meet the size limit. `InventoryRow` carries
capability classifications, `ClosureRow` binds historical proof and receipt
acceptance, and `_PolicyStageFailure` carries a public policy class. They fail
E09-E12 as applicable. `_YamlLine` is authority-neutral, but no exact indexed
node characterizes its import/API boundary; moving three private lines offers
no proven risk reduction, so E13 and the architectural GO condition are not
met.

The remaining apparent dataclasses are not pure extraction candidates:
`NativeProbeSession`, `NativeMultiAuthoritySession`,
`ExternalAuthoritySession`, `_ExternalAbsentLineage`, the firewall's `_Leaf`,
`_Directory`, `_LineageEntry`, `_RetainedLineage`, and `_Snapshot`, and the
closure checker's `_ValidationContext` hold descriptors, inode identities,
paths, cleanup/postcheck callbacks, receipt locations, or authority state.
They fail E02, E03, E06, E08, E09, or E10 and cross explicit custody/TOCTOU,
authority-probe, and publication boundaries. Candidate status: **NO-GO**.

## Forbidden extraction categories

The assessment rejects each explicitly forbidden P0-M1 category:

| ID | Forbidden category | Where it is present |
| --- | --- | --- |
| F01 | native candidate publication | Topology native transaction/publication paths. |
| F02 | path custody | Topology and firewall retained path/lineage validation. |
| F03 | TOCTOU-sensitive code | Descriptor-versus-named identity postchecks. |
| F04 | descriptor lifecycle | Native/external sessions and firewall snapshots/lineage. |
| F05 | authority probes | Native and external authority sessions. |
| F06 | external authority checks | External state, lineage, and qualification logic. |
| F07 | classification transitions | Capability/authority code and state mappings. |
| F08 | receipt acceptance | Receipt parsing, completeness digests, and closure proof. |
| F09 | validation-date authority | Sealed Foundation date parsing and policy validation. |
| F10 | semantic policy decisions | Stable semantic projection and PASS/FAIL/DEFERRED meaning. |

No candidate above is approved by carving a few pure-looking expressions out
of one of these authority-bearing responsibilities.

## Verdict

`NO_EXTRACTION_REQUIRED`

The guardrails, responsibility boundaries, and C01-C16 index provide the safe
P0-M1 outcome without changing qualified P0 implementation blobs. A future
extraction should be reconsidered only when a concrete caller-driven boundary,
exact shim characterization, and a lower-risk case exist; it must then run as
the separately qualified optional packet.
