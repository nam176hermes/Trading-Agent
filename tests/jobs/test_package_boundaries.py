def test_phase4_package_roots_are_importable():
    import apps.job_api
    import packages.job_contracts
    import services.job_scheduler
    import services.job_store
    import services.job_worker
