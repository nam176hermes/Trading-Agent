# Revision Notes

**Revision:** corrected orchestration package v2  
**Original baseline:** `e8166622a181307c5aa5869f5900d9845f294e83`  
**Repository mutation:** none

## Corrections applied

1. Package 6 now requires Packages 1, 2 and 4, including SEC-002, plus a separate runtime Greenlight.
2. Source preflight is separated from disposable/staging runtime preflight.
3. Skip inventory uses JSON so root tooling does not depend on legacy PyYAML.
4. Coverage tooling requires measured bootstrap and explicit dependency approval when new packages are needed.
5. Worktrees are restricted to the current clean root or an external path outside the repository root.
6. PostgreSQL start, stop, restore and PGDATA deletion require command-specific Greenlights in addition to approval records.
7. Package 6 uses tracked bounded child processes only, with no systemd or persistent services.
8. Package 4 requires caller/import impact analysis and forbids filename-only exclusion as proof.
9. Wheel binaries and mutable caches remain outside Git; only manifests and hashes may be committed.
10. Visible em dash characters were normalized to hyphens for output-quality compliance.
11. Completion scoring now distinguishes interim P0 re-assessment from final re-assessment.
12. Source edits no longer inherit runtime-probe language from a global safety rule.
13. Package 2 approval waiting no longer blocks source-only Packages 3, 4 and 5.
14. Final reassessment requires an explicit terminal state for every planned package and preserves blocked runtime gates in the score.

## Decision boundary

This corrected ZIP is a review artifact only. It has not been imported into the repository, committed or executed. No package has started.
