# R2A Fix 4 report — normalized governed endpoint authority

## Candidate

- Parent: `7d617725f4fb90ca6b7c1d3d2fb2a927f80d8b79`.
- Committed paths are limited to the Python extractor, its tests, and this
  report. The R3 draft paths were neither staged nor edited.
- Subject: `fix(p1u): normalize governed endpoint authority`.

## Replacement

The extractor now proves normalized structural fingerprints for every exact
direct-module scope that contains a governed comparison, plus exact
direct-module hashes for `_json_object`, `_read_json`, `_closure_digest`,
`_blocked`, `_PROFILE_SPECS`, and `_PROFILES`. The endpoint fingerprint retains
all AST attributes and normalizes only direct two-subscript `==`/`!=` operators.
It therefore rejects signature, binding, callee, control-flow, movement,
duplication, wrapper, root/field, and mutation drift while allowing raw
operator drift to produce a different syntax fingerprint.

The prior per-comparison receipt table, safe-call table, receiver-taint fixed
point, receipt hashing, mapping-origin checker, and terminal wrapper checker
were deleted. Static source hashes remain data only; the extractor uses no
filesystem, Git, or environment authority.

## RED and focused GREEN

Before production edits, the new structural test failed on the exact Fix3
parameter `set` shadow, parameter `_blocked` shadow, and `and` to `or` control
drift forms. After the replacement, the five-case structural probe passed.

A focused regression selection covering real runtime/closure extraction,
root/origin rebinding and mutation, out-of-scope governed shapes, ordinary
outside-scope literals, and raw operator fingerprint drift passed: `21 passed,
183 deselected`.

`PYTHONDONTWRITEBYTECODE=1 uv run python -m py_compile
scripts/nautilus_pin_inventory/python_extractor.py` and `git diff --check`
passed.

## Boundaries

The four R3 hashes remain:

- `json_extractor.py`: `49e619db50a7ebf43d35825c0262115156ebd2047fb12e103d5158028ec7244a`
- `registry.py`: `df88804b1f55b9a1700f3932d84575a37bd7116a80d5d1cb3907e0e4ad690779`
- `engine.py`: `f03bb204add1a888de1edc68c0f5fee58518b64ad0e6b0581992d6eb0a4f3d1a`
- `test_engine.py`: `2f0ca7fd0bd104c4f6e45f738969abff1fc9ed966ae6c5080651b09e2a24323d`

The recovery stash remains present; inventory remains absent; neither runtime,
remotes, nor the integration reference was changed.

## Remaining gate

The complete non-R3 `test_python.py` selection was started but did not return a
final captured pytest summary in this execution environment. It must be rerun,
then followed by the required exact non-R3 suites and detached clone gate before
review readiness is claimed.
