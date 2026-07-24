# Phase 4B verifier protected-directory correction

Captured: 2026-07-13 EDT.

## Root cause and correction

The installed-authority verifier requires EUID 1000, but it attempted to use
`find` to enumerate `/home/thenam176/.local/share/trading-agent/research-input`
and `/etc/trading-agent/research-input-manifests`. Both roots are intentionally
root-owned mode `0711`, so the runtime identity may traverse a known path but
may not list directory entries.

The EUID-1000 verifier now attests the owner and mode of both protected roots,
the fixed lock file's type, link safety, owner and mode, and, when
`--require-semantic` is supplied, the known `phase4-v1.json` authority plus the
existing application semantic attestation. It does not enumerate either
protected root.

The root-only provisioning boundary is unchanged. `provision-root.sh` still
creates both roots as root-owned `0711` and `validate_semantic_tree` still uses
`find` to enforce exact type, link, ownership and mode policy across both
trees. Extra unreferenced semantic files therefore remain a root-provisioning
concern, not EUID-1000 verifier authority.

## RED and GREEN evidence

Focused RED, before the verifier change:

```text
uv run pytest -q tests/runtime_release/test_provision_script.py::test_protected_semantic_roots_are_enumerated_only_by_root_installer
FAILED tests/runtime_release/test_provision_script.py::test_protected_semantic_roots_are_enumerated_only_by_root_installer
AssertionError: assert 'find "$SEMANTIC_INPUT_ROOT"' not in verifier
1 failed in 0.14s
```

Focused GREEN after removing only the user-verifier enumeration:

```text
uv run pytest -q tests/runtime_release/test_provision_script.py::test_protected_semantic_roots_are_enumerated_only_by_root_installer
1 passed in 0.02s
```

Required regression checks:

```text
uv run pytest -q tests/runtime_release/test_provision_script.py
18 passed in 15.09s

uv run pytest -q tests/runtime_release/test_provision_script.py tests/runtime_release/test_provisioning_generators.py tests/jobs/test_semantic_manifest_builder.py tests/runtime_release/test_semantic.py tests/runtime_release/test_config.py
95 passed in 15.76s

bash -n ops/phase4b/verify-installed.sh
exit 0

bash -n ops/phase4b/provision-root.sh
exit 0

git diff --check
exit 0
```

## Installed-authority verifier outcome

The approved command was run read-only against the installed authority:

```text
bash ops/phase4b/verify-installed.sh /home/thenam176/.cache/trading-agent-phase4b-stage-fdc085a0-41f055b4-sealed-final f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c
phase 4b installed authority verification rejected
exit 2
```

The protected-root listing defect is removed from the verifier source, but the
read-only command did not end with the required success message. Its generic
rejection does not identify the failing later gate. Per the approved boundary,
that rejection was not repaired or investigated through mutating actions. It
remains the blocker to Canonical Source Consolidation Task 0.

## Runtime safety and rollback

This correction changed source, tests and documentation only. It did not use
sudo; mutate `/opt`, `/etc`, `/run`, runtime data, systemd, processes, services,
timers, databases or linked source repositories; create the canonical root;
or change paper mode or either live-trading gate.

Rollback is a source revert of the commit containing this correction. No
runtime rollback is needed because no installed or runtime state was changed.
