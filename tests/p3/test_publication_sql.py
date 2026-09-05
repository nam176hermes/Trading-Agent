from services.job_store.p3_publication_repository import P3PublicationRepository


def test_publication_uses_one_database_capability_call() -> None:
    assert "worker_commit_alpha_campaign" in P3PublicationRepository.COMMIT_SQL
    assert P3PublicationRepository.COMMIT_SQL.count("SELECT") == 1
    assert "append_domain_event" not in P3PublicationRepository.COMMIT_SQL
    assert "read_alpha_commit" in P3PublicationRepository.READ_SQL

