# Phase 3 Migration Design

The importer is dry-run by default. It discovers only approved report, decision,
SQLite signal, capability, and cost-evidence sources; opens SQLite in read-only
mode; uses content hashes plus one-based record indices and normalization
version `phase3-v1`; and emits sanitized planned errors without payload bodies.

The CLI exposes `--dry-run`, `--apply`, `--resume`, `--domain`, `--limit`, and
`--source-file`. At the pre-apply checkpoint, `--apply` is deliberately blocked
for real data. Transactional fixture apply and resume are tested separately
before that guard can ever be reviewed for removal.
