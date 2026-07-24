# Research Backend Instructions

This directory is a preserved, flat research-backend component inside the
canonical source root. Keep its imports, CLI contracts, schemas, and working-
directory behavior stable unless a separately reviewed backend refactor says
otherwise.

## Environment and dependencies

- Use Python 3.11 exactly as constrained by this component's `pyproject.toml`.
- Run uv from this directory with this component's `uv.lock`:

  ```bash
  uv sync --frozen --extra test
  uv run --frozen --extra test pytest -q
  ```

- Do not merge this dependency graph into the root environment, turn the
  repository into a uv workspace, hand-edit `uv.lock`, or import core code to
  bypass an existing boundary.
- Preserve flat imports. Do not package or relocate modules as incidental
  cleanup.

## Safety boundary

- Paper mode only. Keep all live gates false.
- Tests and local research must not probe an exchange, broker, account,
  positions, balances, orders, or any active external execution route.
- Never submit, cancel, modify, or simulate through an external broker.
- Do not load production credentials or protected runtime state during tests.
- Keep models, reports, signals, decisions, memory, scratchpads, job artifacts,
  databases, `.env` files, caches, and virtual environments outside Git.
- Runtime and data roots are external configuration, never paths relative to
  this source directory.

## Integration boundary

The core/control plane must not import this flat backend in process, and this
backend must not import the core in process. Use the existing PostgreSQL,
protected-file, Control API, and Job API contracts. Do not add a production
mutation route.

Ask first before dependency changes, runtime or database changes, migrations,
persistent processes, service/scheduler changes, exchange access, root
commands, remote mutations, or production deployment.
