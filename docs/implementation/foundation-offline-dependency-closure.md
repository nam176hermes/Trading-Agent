# Foundation Offline Dependency Closure

## Authority

- Canonical lock SHA-256: `c18739d0b32f5bf56882a250f82393b8c31d3a10b0423f40ccc8f3dcd0b243c6`
- Wheelhouse aggregate SHA-256: `bcee824c80dd1f967cdb450e72dfaf3ea889f4aa5275754c35216d073c7f6938`
- Python identity: `CPython 3.11.15`
- Downloader: `pip 25.1.1`
- Preparation UV: `uv 0.11.7`
- Artifact count: 21
- Committed inventory: `docs/implementation/foundation-wheelhouse-manifest.json`

The external wheelhouse is addressed by the lock hash. The path itself is intentionally omitted from committed evidence. Wheel binaries remain outside Git.

## Locked production closure

| Package | Version | Runtime wheel class | License metadata |
|---|---:|---|---|
| alembic | 1.18.5 | py3-none-any | MIT |
| annotated-doc | 0.0.4 | py3-none-any | MIT |
| annotated-types | 0.7.0 | py3-none-any | UNKNOWN |
| anyio | 4.14.1 | py3-none-any | MIT |
| click | 8.4.2 | py3-none-any | BSD-3-Clause |
| fastapi | 0.139.0 | py3-none-any | MIT |
| greenlet | 3.5.3 | cp311 manylinux x86_64 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | py3-none-any | MIT |
| idna | 3.18 | py3-none-any | BSD-3-Clause |
| mako | 1.3.12 | py3-none-any | MIT |
| markupsafe | 3.0.3 | cp311 manylinux x86_64 | BSD-3-Clause |
| psycopg | 3.3.4 | py3-none-any | LGPL-3.0-only |
| psycopg-binary | 3.3.4 | cp311 manylinux x86_64 | LGPL-3.0-only |
| psycopg-pool | 3.3.1 | py3-none-any | LGPL-3.0-only |
| pydantic | 2.13.4 | py3-none-any | MIT |
| pydantic-core | 2.46.4 | cp311 manylinux x86_64 | MIT |
| sqlalchemy | 2.0.51 | cp311 manylinux x86_64 | MIT |
| starlette | 1.3.1 | py3-none-any | BSD-3-Clause |
| typing-extensions | 4.16.0 | py3-none-any | PSF-2.0 |
| typing-inspection | 0.4.2 | py3-none-any | MIT |
| uvicorn | 0.51.0 | py3-none-any | BSD-3-Clause |

Exact filenames, source URLs, sizes and artifact SHA-256 values are in the committed inventory.

## Native boundary

Native wheels are pinned to CPython 3.11 on Linux x86_64 with manylinux compatibility. The native set is `greenlet`, `markupsafe`, `psycopg-binary`, `pydantic-core` and `sqlalchemy`. No source distribution is allowed during preparation or release installation.

`annotated-types` reports `UNKNOWN` license metadata in its wheel. This is recorded as a metadata gap, not replaced with a guessed value.
