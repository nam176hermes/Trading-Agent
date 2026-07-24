# Contract Generation

Pydantic models in `apps/control_api/control_api/contracts.py` and
`packages/job_contracts` are the source of truth.

```bash
make generate-contracts
make check-contracts
```

The canonical Node generator toolchain is declared by
`apps/dashboard/package.json`, locked by `apps/dashboard/package-lock.json`,
and installed in `apps/dashboard/node_modules`. The normal Make targets and
CLI default use that in-repository toolchain, so contract checks are portable
across clones and worktrees and do not require a sibling dashboard checkout.

Generation writes:

- `generated/openapi/openapi.json` — OpenAPI 3.1.
- `generated/json-schema/*.json` — selected JSON Schema 2020-12 documents.
- `generated/dashboard/api-types.ts` — static TypeScript types.
- `generated/dashboard/api-schemas.ts` — Zod schemas/client metadata.

`scripts/generate_contracts.py --check` renders into a temporary directory and
byte-compares the repository-owned outputs. It exits non-zero for any drift and
does not edit or require a clean linked dashboard repository. A reviewed
consumer can be synchronized explicitly with
`--dashboard-root /absolute/path/to/dashboard`; linked-project approval is
required before doing so. `--tool-root /absolute/path/to/tool-installation`
remains an explicit diagnostic override that selects a read-only Node tool
installation and does not make that directory an output target. The CLI does
not read an ambient `CONTRACT_TOOL_ROOT`; callers must opt into a diagnostic
tool override explicitly. Generated files are never hand-edited and are
excluded from ESLint because the upstream generator emits types outside local
style rules; they remain covered by TypeScript compilation and runtime parsing
tests.

The Zod generator currently emits broader TypeScript types around nullable OpenAPI unions than `openapi-typescript`. The dashboard therefore uses `openapi-typescript` types for static typing and the generated Zod object for network validation. The client returns data only after `safeParse` succeeds.
