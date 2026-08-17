
# P1 Nautilus v1.231 Upstream Provenance and Copy/Adapt Log Template

## Release authority

```yaml
engine_name: nautilus_trader
engine_version: 1.231.0
runtime_family: cython-v1
release_tag: v1.231.0
release_tag_object: d3e1685e979925d7b0ffacd1b3f442547686e18f
upstream_commit: 27a8e54e7ac3c57d6cbf8891f0283dfbaee97317
official_sdist_sha256: 142dde40e77339745aa5fe6bcbb3de5624cee087f526879da00f127df077530f
official_cp312_linux_wheel: nautilus_trader-1.231.0-cp312-cp312-manylinux_2_35_x86_64.whl
official_cp312_linux_wheel_sha256: 8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216
release_manifest_sha256: <fill>
sigstore_bundle_sha256: <fill>
intoto_attestation_sha256: <fill>
primary_build_source: <commit-archive | official-sdist>
independent_cross_check: <fill>
candidate_closure_schema: 7
candidate_closure_digest: <fill>
p1_product_closure_schema: 8
p1_product_closure_digest: <fill>
```

Record verification commands, exit codes, tool identities and whether each upstream signature/attestation is verified, unsigned or unavailable. Never describe an unsigned tag as signed.

## Copied/adapted code entry

```yaml
entry_id: P1-UPSTREAM-<NNN>
task_id: <TASK_ID>
local_path: <path>
local_symbol: <symbol>
upstream_repo: nautechsystems/nautilus_trader
upstream_tag: v1.231.0
upstream_commit: 27a8e54e7ac3c57d6cbf8891f0283dfbaee97317
upstream_path: <path>
upstream_symbol_or_lines: <symbol/range>
mode: <copied | adapted | pattern-only>
reason: <why copying is safer than reimplementation>
functional_changes:
  - <change>
safety_changes:
  - <change>
tests:
  - <test path/name>
reviewed_by:
  spec: <reviewer/verdict>
  security: <reviewer/verdict>
local_commit: <sha>
```

## Prohibited entries

- moving branch or `latest` references;
- copied code without exact upstream commit/path;
- whole-repository vendoring;
- adapter/network/live code outside P1 scope;
- code copied from v2 into the Cython-v1 runtime without an approved compatibility ADR;
- hidden changes to source authority or toolchain policy.
