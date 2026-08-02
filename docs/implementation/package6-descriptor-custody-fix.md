# Package 6 Native Descriptor Custody Fix

## Status and boundary

This document covers Goal 1 only: eliminate the interval in which `open(2)` or `openat(2)` has returned a fresh descriptor but no destructor-backed owner exists.

The Package 6 native custodian and this extension remain source-closure and disposable-test authority. They do not authorize production activation, service startup, PostgreSQL access, live execution, or live trading.

## Rejected design

The rejected path was:

```text
open/openat
-> raw integer descriptor
-> Python callable ctypes restype
-> Python ctypes object
-> Python identity binding
```

The kernel descriptor existed before Python had installed an exactly-once owner. An exception at the native-call result boundary could therefore leak the descriptor. A Python `try/finally`, a callable `restype`, or a post-return identity check cannot close that interval.

## Implemented ownership boundary

`native/package6_custodian/src/python_fd_custody.c` implements the CPython extension `_package6_fd_custody` and its `FdOwner` type.

The native sequence is:

```text
allocate FdOwner with fd = -1
-> call open/openat with O_CLOEXEC forced
-> store the successful descriptor in FdOwner
-> capture fstat device and inode
-> return the owned object to Python
```

`FdOwner` has its native deallocator before the syscall runs. No successful descriptor is returned to Python as an unowned integer. Python can borrow the descriptor number only while the owner object remains alive.

`services/paper_runtime/evidence.py::_native_open` receives `FdOwner`, validates the captured identity shape, and places it inside `_DescriptorAuthority`. It no longer uses `_OwnedDescriptor`, `_NativeOpenResult`, `_convert_native_open_result`, `_LIBC_OPEN`, or `_LIBC_OPENAT`.

## Close contract

The native owner implements this state transition:

```text
owned(fd >= 0)
-> consume by setting fd = -1
-> issue one close(fd)
-> closed or ambiguous-close-consumed
```

Rules:

- Ownership is consumed before the syscall.
- A destructor, explicit second close, or exception path cannot retry the number.
- An ambiguous close error returns failure but leaves ownership consumed.
- If the kernel or a test reuses the number, later owner cleanup cannot close the foreign descriptor.
- `abandon_uncertain_generation()` consumes ownership without close when another code path has already made descriptor identity uncertain.
- Every native open forces `O_CLOEXEC`, even when the Python caller omits it.

## Extension loading and build isolation

The canonical root `make test` target creates a new external mode-`0700`
directory for each invocation with a shape such as:

```text
/tmp/package6-custodian-test.<random>/python/_package6_fd_custody<EXT_SUFFIX>
```

It leaves that directory in place for the separately controlled cleanup step.
Consequently, concurrent same-UID checkouts do not select artifacts through a
shared build pathname. After the build, the root recipe requires exactly one
regular extension, computes its SHA-256, and launches pytest with both
`PACKAGE6_FD_CUSTODY_EXTENSION_PATH` and
`PACKAGE6_FD_CUSTODY_EXTENSION_SHA256` bound to that artifact.
This is invocation separation plus exact-byte binding, not a security boundary
against actively malicious code already running as the same UID.

The direct developer target `make build-package6-custodian` remains backward
compatible and uses `/tmp/package6-custodian-build` unless `BUILD_DIR` is
overridden. That shared default is not candidate-isolated across concurrent
same-UID checkouts and is not canonical test or release evidence. A direct
caller that requires candidate binding must choose its own external private
`BUILD_DIR`, compute the built extension digest, and set both loader variables.
The root `make test-package6-custodian-native` target likewise creates a fresh
external mode-`0700` build directory for each sanitizer invocation.

The loader accepts only an absolute, user-owned, regular, singly linked artifact
with mode `0600`. Its direct parent must be user-owned mode `0700`; the default
build root and `python` directory are both mode `0700`. Writable ancestry is
rejected except the root-owned sticky `/tmp` boundary. A symlink is rejected.
The expected native contract marker is `NATIVE_OBJECT_V1`.

After preliminary path checks, the loader retains a no-follow descriptor chain from
`/` through every artifact parent. Every directory policy check uses `fstat` on
the retained descriptor. It then opens the artifact relative to the retained
direct parent with `O_NOFOLLOW | O_CLOEXEC` and derives regular-file, owner,
mode `0600`, single-link, metadata, size, and SHA-256 policy from that retained
file descriptor. It executes the extension through `/proc/self/fd/<fd>` and
verifies descriptor bytes plus descriptor-relative pathname identity again after
loading before publishing the module in `sys.modules`. Replacement before open
is therefore revalidated against descriptor policy, while replacement after open
cannot select a different shared object.

When `PACKAGE6_FD_CUSTODY_EXTENSION_SHA256` is present, it must be exactly 64
lowercase hexadecimal characters. The loader hashes the retained artifact
descriptor and compares it with that expected digest before constructing or
executing the extension module. A malformed digest, a mismatch, or an unavailable
expected artifact fails closed. Omitting the digest retains the legacy direct
developer loading behavior; canonical `make test` never omits it.

A preloaded `sys.modules["_package6_fd_custody"]` entry is accepted only when all of these checks pass:

- its loader is `ExtensionFileLoader`;
- its module and specification origins resolve to the validated extension artifact;
- its contract marker is exact;
- `FdOwner` is the expected non-heap native type;
- `open` and `openat` are built-in functions bound to that exact module object.

A colliding Python module, forged module metadata with a heap owner type, or replaced Python entry point fails closed before descriptor acquisition.

Direct developer build command:

```bash
umask 0002
make build-package6-custodian
```

The native Makefile creates a private temporary artifact in the mode-`0700` Python
build directory, explicitly sets the temporary file to mode `0600`, and runs
the extension linker under `umask 0177`. It then verifies that the temporary
artifact is owned by the current user, is mode `0600`, and has one link before
capturing its device and inode and atomically replacing the final extension with
`mv -Tf`. Publication then proves that the final path is a regular non-symlink
with the same device and inode, current UID, mode `0600`, and link count one,
and revalidates both private build directories. The production stub, extension,
service-test binary, and all sanitizer binaries are forced to rebuild on every
relevant native Make invocation. `test_protocol` also declares both protocol
headers, including `include/p6c_types.h`. Warm outputs therefore cannot survive
a relevant invocation merely because their timestamps are newer than changed
compiler flags or Make configuration.

The current Package 6 source-closure build defines `P6FD_TESTING`. Its private hooks only inject deterministic faults and count native close calls for adversarial tests. This build has no production activation authority.

## Adversarial tests

Required tests live in `tests/foundation/test_package6_controller_closure.py`:

- `test_native_fd_custody_rejects_preloaded_python_module`
- `test_native_open_converter_entry_exception_cannot_leak_fd`
- `test_native_open_first_opcode_exception_cannot_leak_fd`
- `test_native_open_keyboard_interrupt_cannot_leak_fd`
- `test_native_open_system_exit_cannot_leak_fd`
- `test_native_open_owner_destructor_closes_exactly_once`
- `test_native_open_close_error_never_retries_reused_number`

The tests inventory `/proc/self/fd`, inject failure after native acquisition, interrupt the first Python result opcode, run `KeyboardInterrupt` and `SystemExit`, force descriptor-number reuse, and count native close calls. The destructor test also verifies `FD_CLOEXEC` when the Python call omits `O_CLOEXEC`.

Recorded TDD evidence:

```text
RED:   6 failed in 0.63s before the extension existed
GREEN: 6 passed in 0.41s after native ownership was wired
RED:   1 failed when a preloaded same-name Python module was accepted
GREEN: 1 passed after loader, origin, native-type, and entry-point validation
RED:   6 failed before isolated root wiring, forced warm rebuilds, and final
       publication provenance checks
GREEN: 6 passed after the native and root Make contracts were hardened
RED:   2 failed when malformed and mismatched expected digests were ignored
GREEN: 2 passed after expected-digest validation was placed before module
       construction
```

Focused existing descriptor coverage subsequently returned:

```text
14 passed, 140 deselected
```

These focused results are implementation evidence only. Final Goal 1 acceptance still requires a fresh native build, the complete controller-closure file, relevant native tests, a forbidden-pattern scan, and `git diff --check` on the final source bytes.

## Acceptance commands

```bash
umask 0002
make build-package6-custodian
uv run pytest -q tests/foundation/test_package6_controller_closure.py -k 'native_open or owned_descriptor'
make test-package6-custodian-native
python3 - <<'PY'
from pathlib import Path
source = Path("services/paper_runtime/evidence.py").read_text()
for forbidden in (
    "_convert_native_open_result",
    "class _NativeOpenResult",
    "class _OwnedDescriptor",
    "_LIBC_OPEN =",
    "_LIBC_OPENAT =",
):
    assert forbidden not in source, forbidden
assert not any(
    "restype = _" in line and "OPEN" in line
    for line in source.splitlines()
)
PY
git diff --check
```

The static scan must return no assertion failure. Tests or build failures keep Goal 1 at FAIL. Failures confined to Package 6 publication, rollback, cgroup-generation authority, or later goals must be reported separately and cannot be presented as Goal 1 evidence.

These commands and their artifacts remain paper-only source-closure evidence.
They do not establish Track A GO, release GO, production activation, live
execution approval, or live trading approval. Production and live status remain
NO-GO pending independent review and the separately governed release authority
and cutover work.
