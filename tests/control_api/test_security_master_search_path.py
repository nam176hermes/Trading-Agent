import importlib.util
from pathlib import Path
from types import SimpleNamespace
import re


def test_security_master_migration_preserves_catalog_first_caller_search_path(monkeypatch):
    path = Path(__file__).parents[2] / "alembic/versions/0019_p2_security_master.py"
    spec = importlib.util.spec_from_file_location("security_master_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class Connection:
        search_path = "pg_catalog, public"

        def exec_driver_sql(self, statement, parameters=None, **kwargs):
            prior = self.search_path
            paths = re.findall(r"set_config\('search_path', '([^']+)'", statement)
            if paths:
                self.search_path = paths[-1]
            elif parameters is not None and "set_config" in statement:
                self.search_path = parameters[0]
            return SimpleNamespace(scalar_one=lambda: prior)

    connection = Connection()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    migration.upgrade()
    assert connection.search_path == "pg_catalog, public"
