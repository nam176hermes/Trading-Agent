"""Manually selected disposable SQL source check; not protected qualification."""
import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from services.job_worker.p3_fixture_sql import _cleanup_cluster, run_sql_fixture
from packages.alpha_lifecycle.contracts.base import SourceIdentity
from packages.pre_p3_provenance import canonical_source_identity


def run_sql_source_check():
    return run_sql_fixture(SourceIdentity.model_validate(canonical_source_identity(ROOT)))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable-sql", action="store_true")
    args = parser.parse_args(argv)
    if not args.run_disposable_sql:
        parser.error("explicit --run-disposable-sql is required to start the owned test cluster")
    run_sql_source_check()

if __name__ == "__main__":
    main()
