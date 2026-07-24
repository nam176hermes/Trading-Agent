# Job Plane Runtime Backup and Restore v2

**Evidence date:** 2026-07-16
**Status:** `NOT EXECUTED — RECOVERY/BASELINE GATE BLOCKED`

## Gate result

No fresh runtime dump was created and no isolated runtime restore database was
created. Stage B depends on a safely recovered, authenticated runtime cluster
at the exact accepted pre-`0005` baseline. Stage A stopped before its first
write because the recovery runbook's exact dual-reviewed approval record was
absent.

Consequently there is no Part 2 evidence for:

- a current custom-format dump;
- dump mode `0600`, size, or SHA-256;
- dump catalog readability;
- source revision/count/table inventory;
- isolated restore identity;
- restored revision, count, relation, role, or ACL parity;
- temporary runtime-restore database removal.

No dump path, credential, password, environment value, URI, or DSN was read or
printed. No dump was placed in Git or a release tree.

## Historical evidence is not substituted

The reviewed recovery runbook records an older verified fallback dump at
revision `0003_contract_lineage_repair` with approximately 51 hours of possible
RPO loss. It is not a fresh pre-`0005` backup and was not opened, restored, or
revalidated in Part 2. It cannot satisfy this gate.

## Future required sequence

Under a conforming approval, backup may begin only after the single authorized
recovery start proves PostgreSQL 16, exact cluster/database/endpoint identity,
the approved pre-`0005` head, canonical `43,055`, quarantine `222`, exact Job
Plane counts, relation integrity, and accepted roles/ACLs.

The approved procedure must then create one private custom-format dump under
umask `077`, prove final mode `0600`, hash and read its catalog, restore into a
unique isolated PostgreSQL 16 target with pre-provisioned portable roles,
compare every required table/catalog invariant, retain evidence, and remove
the temporary database only after success. Any mismatch remains a stop
condition; it does not authorize repair or runtime migration.
