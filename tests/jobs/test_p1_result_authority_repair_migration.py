from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


ROOT = Path(__file__).parents[2]
MIGRATION = ROOT / "alembic/versions/0016_p1_result_authority_repair.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("p1_result_authority_0016", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_p1_result_authority_repair_is_exact_forward_only_driver_sql() -> None:
    migration = _load_migration()
    observed: dict[str, object] = {}

    class Bind:
        def exec_driver_sql(
            self, statement: str, *, execution_options: dict[str, object]
        ) -> None:
            observed.update(statement=statement, execution_options=execution_options)

    migration.op = SimpleNamespace(get_bind=lambda: Bind())
    migration.upgrade()
    statement = observed["statement"]
    assert isinstance(statement, str)
    assert migration.revision == "0016_p1_result_authority_repair"
    assert migration.down_revision == "0015_p1_accounting_closure_rotation"
    assert statement.count("88889999aaaabbbb") == 1
    assert statement.count("89ab89ab89ab89ab") == 1
    assert "6e8cae1e8f9f120fbf79fc0a9eb444ce0c1163b708e35ec71ec813561c20f445" in statement
    assert "f7e70b8bc22d44600da7357657c8a7016035e5d2e6d71caaf5ea87351e33c230" in statement
    assert "aea1129235f91d7645741c04912590a31cc3e667df43867c8b3a7cecfec9b743" in statement
    assert "ec8e58681820e129ca4febf16ea3ab20751856d9d9cb710604fb2c80d5a9569f" in statement
    assert "8972d3cf715cfd761e86d88446161c6c4a36e8b4fb61f76d02ed41bd227ee089" in statement
    assert "4a04f41c0ac9dcb45ae09aa02b245cd07f4ea5f287c6956011d1c63d7c8c5eb4" in statement
    assert "04ec80653561e0c40cd57d1920642dd6e1e878d0e11f4729cd4b97273e06dd5b" in statement
    assert "d2bd044da6afcb5647160e32fffbfa49619fabcfdc8a85dba78beaf3e30c330e" in statement
    assert "10e24e84094478e5b4994dab8ffdb22dec021ca235cfe51a05e94ae49e62fd34" in statement
    assert "1bac6ed97eec8dcd1dbd3ff1de27ec111fc8fbeff291d135bd423391441ded0e" in statement
    assert "5fcb5c4542ef72c38639922535d2d1065fb6198da28bcdce9634f66aa59b69e0" in statement
    assert "67cf705a214d242e2a197327c53117c4508c16f289b31dbb4fe7be6653219372" in statement
    assert "e4ffec60fa1e4f02b56b7484b6b84423c1d0349c3d5781062c4ad600a7090416" in statement
    assert "f9d54384bb1dae2a0cda166118cc0ceb016e8590c58579820384284ea8e42e9b" in statement
    assert "4f9c03425a69edf9844a1ae9188660ac7ea4285e5a1ecd87e8e6ecc31be6ec78" in statement
    assert "002fd4c9ccc597c2016b7ae1ec32be78138d0e05448ca24a1d6051a0a84b6141" in statement
    assert "v_legacy_document := public.canonical_domain_json(" in statement
    assert "ON CONFLICT ON CONSTRAINT engine_job_results_pkey DO NOTHING" in statement
    assert "job_plane.paper_worker_job_id_allowed(" in statement
    assert "job_artifacts.job_id" in statement
    assert "GRANT EXECUTE ON FUNCTION job_plane.paper_worker_job_allowed(" not in statement
    assert "DROP POLICY job_plane_worker_artifacts_insert" in statement
    assert statement.count("pg_catalog.set_config(") == 2
    assert observed["execution_options"] == {"no_parameters": True}

    try:
        migration.downgrade()
    except RuntimeError as exc:
        assert "forward-only" in str(exc)
    else:
        raise AssertionError("P1 result-authority repair downgrade was accepted")
