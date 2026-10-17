"""Offline bootstrap ordering check; the existing PG hostile-operator test remains unchanged."""
import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize("migration_fails", [False, True])
def test_online_bootstrap_fences_catalog_resolution_and_preserves_public_schema(migration_fails):
    path = Path(__file__).parents[2] / "alembic/env.py"
    tree = ast.parse(path.read_bytes())
    functions = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)], type_ignores=[])

    class Connection:
        search_path = "public, pg_catalog"
        options = {}
        failed = False

        def exec_driver_sql(self, sql, parameters=None):
            if self.failed:
                raise RuntimeError("transaction aborted; original migration error must survive")
            prior = self.search_path
            if "set_config" in sql:
                self.search_path = parameters[0] if parameters else "pg_catalog, public"
            return SimpleNamespace(scalar_one=lambda: prior)

        def get_execution_options(self):
            return dict(self.options)

        def execution_options(self, **options):
            self.options.update(options)
            return self

    connection = Connection()

    def migrate():
        assert connection.search_path.split(", ")[0] == "pg_catalog"
        assert connection.options.get("schema_translate_map") == {None: "public"}
        if migration_fails:
            connection.failed = True
            raise LookupError("original migration failure")

    context = SimpleNamespace(configure=lambda **kw: None,
        begin_transaction=nullcontext, run_migrations=migrate)
    namespace = {"config": SimpleNamespace(attributes={"connection": connection}),
                 "context": context, "target_metadata": None}
    exec(compile(functions, str(path), "exec"), namespace)
    if migration_fails:
        with pytest.raises(LookupError, match="original migration failure"):
            namespace["run_migrations_online"]()
    else:
        namespace["run_migrations_online"]()
        assert connection.search_path == "public, pg_catalog"
    assert connection.options.get("schema_translate_map") is None
