# Foundation Release Reproducibility

## Repeated wheelhouse preparation

First successful preparation:

```json
{"aggregate_sha256":"bcee824c80dd1f967cdb450e72dfaf3ea889f4aa5275754c35216d073c7f6938","artifacts":21,"reused":false}
```

Second preparation against the same lock:

```json
{"aggregate_sha256":"bcee824c80dd1f967cdb450e72dfaf3ea889f4aa5275754c35216d073c7f6938","artifacts":21,"reused":true}
```

The second command verifies the sealed inventory and does not download or overwrite artifacts.

## Repeated release build

`test_actual_locked_app_build_is_offline_copied_symlink_free_and_runnable` builds two releases from the same Git commit and wheelhouse. It asserts equality of:

- Complete file entries.
- Logical release digest.
- Interpreter identity and layout invariants.

The second release is renamed after construction and remains runnable. This proves the virtual environment does not depend on its staging directory name.

## Reproducibility authorities

| Authority | Value |
|---|---|
| Lock SHA-256 | `c18739d0b32f5bf56882a250f82393b8c31d3a10b0423f40ccc8f3dcd0b243c6` |
| Dependency inventory SHA-256 | `bcee824c80dd1f967cdb450e72dfaf3ea889f4aa5275754c35216d073c7f6938` |
| Python | `CPython 3.11.15` |
| Downloader | `pip 25.1.1` |
| Preparation UV | `uv 0.11.7` |

The release file digest is generated per build and compared by the host test. No dependency or lockfile was changed in Package 01.
