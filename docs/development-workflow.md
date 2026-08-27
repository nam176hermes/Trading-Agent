# Development workflow

The canonical release and CI entry points remain in the protected `Makefile`.
Use `make test-all` for completion evidence. Use the commands below for the
short feedback loop while editing:

```bash
uv run python scripts/dev.py doctor
uv run python scripts/dev.py static
uv run python scripts/dev.py test tests/path/to/test_file.py
uv run python scripts/dev.py test tests/path/to/test_file.py -k focused_case
uv run python scripts/dev.py test-debug tests/path/to/test_file.py -k focused_case
uv run python scripts/generate_contracts.py --check
```

`test` captures successful output and prints failure context, which keeps local
logs and agent context small. `test-debug` deliberately restores live, verbose
output for diagnosis. Both commands create a private Linux-native temporary
directory and clean it after the child process exits.

`static` runs pinned Ruff and Basedpyright versions over the root production
packages and the legacy backend. Baselines keep the initial adoption bounded;
new diagnostics still fail the command. Baseline changes must be reviewed as
code changes, not regenerated automatically.

The workspace doctor verifies repository location, Git topology, temporary
storage, generated contracts, and the paper-only execution boundary. It does
not authorize deployment, a provider connection, or live/broker execution.
