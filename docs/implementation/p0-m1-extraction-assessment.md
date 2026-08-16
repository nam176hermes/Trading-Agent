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
pytest nodes. Existing tests also exercise the current helper and parser
surfaces transitively or directly; a compatibility shim kept at those surfaces
would therefore be exercised. Characterization is evidence about preserved
behavior and API reachability, not a requirement to predict a future module
location.

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
| E13 | existing characterization already covers it | Existing exact tests must exercise the behavior and old API surface through which a compatibility shim would be reached; they need not pre-prove a future module path. |
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
canonical bytes transitively, and existing tests call the current topology
helper directly and traverse the firewall and closure helpers through their
current public behavior. A shim left at each old surface would be exercised, so
E13 is satisfied for a behavior-preserving move; no test must know the future
module's location in advance. E11 still requires preserving the newline
difference and exact byte spelling. Consolidating four- or five-line helpers
would modify qualified hotspot blobs and add a reviewed first-party
dependency without reducing an authority-bearing responsibility or a
demonstrated maintenance risk. The required lower-risk case is not established.
Candidate status: **NO-GO**.

### Digest helpers

Each digest candidate requires its own assessment. None of these pure
calculations publishes a receipt or decides PASS/FAIL/DEFERRED merely because a
caller later uses its result for validation.

| Symbol | Individual assessment | Existing characterization | Decision |
| --- | --- | --- | --- |
| topology `closed_node_proof_digest` (4 physical lines) | Pure deterministic digest of `_closed_node_proof_payload`; E01-E12 and E14 can be preserved, and a same-name shim is feasible under E15. Moving it alone would leave its payload construction behind and add a dependency without isolating a responsibility. | Portable-defect closure tests call it directly and exercise the current surface, satisfying E13 for a shim. | Technically plausible, but **NO-GO** because no lower-risk or maintenance benefit is demonstrated. |
| topology `completeness_sha256` (4) | Pure deterministic field selection and digest; it does not publish or accept a receipt and does not decide an outcome. Exact field selection remains an E11 contract that a shim can preserve. | Topology receipt construction/parsing tests call it directly and traverse it through existing receipt behavior, satisfying E13. | **NO-GO**: moving four lines changes a qualified blob/import graph without reducing risk. |
| topology `payload_sha256` (2) | Pure deterministic digest excluding `receipt_sha256`; E01-E12 and E14 can be preserved, with a feasible old-name shim. | Topology and firewall tests call it directly and exercise receipt round trips, satisfying E13. | **NO-GO**: a two-line move has no demonstrated maintenance or risk benefit. |
| topology `native_completeness_sha256` (5) | Pure deterministic native-receipt digest; no publication or terminal decision occurs in this helper. E11 requires exact exclusion fields, which a shim can preserve. | Native receipt tests call it directly and exercise parsing/construction, satisfying E13. | **NO-GO**: no cohesive responsibility or lower-risk case is created by moving five lines. |
| topology `external_completeness_sha256` (5) | Pure deterministic external-receipt digest; no publication, authority probe, classification transition, or terminal decision occurs here. E11 can be preserved by a shim. | External authority receipt tests call it directly and exercise parsing/construction, satisfying E13. | **NO-GO**: no demonstrated maintenance benefit offsets changing the qualified hotspot/import graph. |
| firewall `manifest_payload_sha256` (4) | Pure deterministic copy/blank-field/digest computation; it does not publish or accept the manifest. E11 and E15 can be preserved. | Artifact-firewall tests call it directly and validators traverse it, satisfying E13. | **NO-GO**: moving four lines provides no lower-risk boundary. |
| firewall `semantic_result_sha256` (7) | The hashing is pure, but this symbol invokes `_stable_semantic_projection`; moving the symbol as a unit would move or couple across the semantic-policy decision that defines result meaning. Extracting that policy is forbidden by F10 and cannot be treated as a generic digest-only move under E12. | Direct artifact-firewall tests strongly characterize stable semantic meaning and run-metadata exclusion. | **NO-GO** because the candidate is policy-coupled; extracting only a wrapper has no standalone responsibility, while extracting its projection crosses F10. |

The first six rows are not rejected under E08 or E10: their callers perform
publication, acceptance, or fail-closed decisions. Their NO-GO result follows
from the architectural requirement to prove that changing qualified blobs is
lower risk than leaving these two-to-five-line calculations in place.

### Immutable constants and pure schema field sets

Plausible data includes receipt schema strings and key frozensets in topology,
manifest schema strings in the firewall, and `TOP_KEYS`, `ENTRY_KEYS`,
`SAFE_STATES`, and status maps in the closure checker. These are small,
deterministic data and satisfy E01-E08 and E14.

They are nevertheless the executable definitions of schema/version keys,
evidence paths, capability/authority classifications, accepted closure states,
and PASS/FAIL/DEFERRED meaning. Splitting only the apparently neutral values
would still change the import graph of qualified hotspots. C03-C09 and C11-C16
exercise their consequences through the current surfaces, so a preserved
old-name shim would be traversed. Policy-bearing constants fail E09-E12,
depending on the specific constant, and cross the forbidden classification,
receipt-acceptance, validation-date, or semantic-policy categories. Neutral
schema strings and field sets have no demonstrated cohesive maintenance or
lower-risk benefit when moved alone. Candidate status: **NO-GO**.

### Pure dataclasses

The pure immutable data-only types are topology's `_PolicyStageFailure` (5
physical lines), `InventoryRow` (4), and `ClosureRow` (8), plus the closure
checker's `_ValidationContext` (11) and `_YamlLine` (3). These containers do not
themselves classify, accept, publish, mutate, or decide an outcome; their
callers do. Their exact field shapes can be preserved under E11/E12, they meet
E14, and old-name aliases are feasible under E15. Existing topology, closure,
workflow, and diagnostic tests instantiate or traverse the current types, so
those aliases would be exercised and E13 is satisfied. Each data shape exists
only to connect adjacent parser/validator logic in its current file. Moving
three to eleven lines adds an import boundary without isolating a reusable
responsibility or reducing a demonstrated maintenance risk. Candidate status:
**NO-GO** under the architectural GO condition, not because the containers
perform their callers' authority behavior.

The remaining apparent dataclasses are not pure extraction candidates:
topology's `_RetainedClosureArtifacts` and `_RetainedNativeArtifacts` retain
directory and leaf descriptors plus inode identity snapshots; its
`NativeProbeSession`, `NativeMultiAuthoritySession`,
`ExternalAuthoritySession`, and `_ExternalAbsentLineage` retain descriptors,
named identities, paths, postchecks, cleanup callbacks, or authority state.
The firewall's `_Leaf`, `_Directory`, `_LineageEntry`, `_RetainedLineage`, and
`_Snapshot` retain and manage descriptor/identity lifecycles. Its
`_TreeIdentity` has no open descriptor itself, but its directory and leaf inode
identity maps are custody evidence, so it still fails E03. The closure
checker has no comparable descriptor-custody dataclass. Every type in this
paragraph fails E03 because the type itself retains descriptors, inode identity
evidence, or callbacks that preserve and recheck that custody; several also
sit inside the explicitly forbidden authority-probe and publication paths.
Candidate status: **NO-GO**.

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
