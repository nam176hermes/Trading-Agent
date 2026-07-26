"""Validated empty-start environment for a research-only child."""

from __future__ import annotations

import os
import re
import stat
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, NoReturn, cast

from packages.runtime_release.config import RuntimeAuthorityV2, RuntimePathsV2

from .errors import WorkerBlockedError

FIXED_PATH = "/usr/bin:/bin"
# Research input is deliberately separate from the active legacy tree.  The
# canonical mode/kill-switch evidence remains in ``safety.py`` and must never
# be copied into this root: a copy could go stale while a child is running.
APPROVED_DATA_ROOT = Path("/home/thenam176/.local/share/trading-agent/research-input")
APPROVED_REPORTS_DIR = Path("/home/thenam176/.local/share/trading-agent/research-output/reports")
APPROVED_SIGNAL_OUTPUT_DIR = Path("/home/thenam176/.local/share/trading-agent/research-output/signals")
APPROVED_SCRATCH_HOME = Path("/home/thenam176/.local/run/trading-agent/research-home")
SEMANTIC_ROOT_OWNER_UID = 0

_PATH_KEYS = frozenset({
    "TRADING_DATA_ROOT",
    "TRADING_REPORTS_DIR",
    "TRADING_SIGNAL_OUTPUT_DIR",
    "TRADING_JOB_ID",
    "TRADING_JOB_ATTEMPT_ID",
    "TRADING_ATTEMPT_ID",
    "TRADING_RESEARCH_BACKEND_COMMIT",
    "TRADING_RESEARCH_SCRATCHPAD_ROOT",
    "TRADING_SEMANTIC_AUTHORITY_PATH",
})
_RESEARCH_KEYS = (
    "TRADING_RESEARCH_ANTHROPIC_API_KEY",
    "TRADING_RESEARCH_DEEPSEEK_API_KEY",
    "TRADING_RESEARCH_EXA_API_KEY",
    "TRADING_RESEARCH_OPENAI_API_KEY",
)
_PACKAGE6_FIXTURE_KEYS = (
    "TRADING_PACKAGE6_FIXTURE_AUTHORITY_PATH",
    "TRADING_PACKAGE6_APPROVAL_SHA256",
)
_SEMANTIC_AUTHORITY_PATH_ENV = "TRADING_SEMANTIC_AUTHORITY_PATH"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class EnvironmentValidationError(WorkerBlockedError):
    pass


def _blocked(reason: str, message: str) -> NoReturn:
    raise EnvironmentValidationError(reason, message)


def _contains_symlink(path: Path) -> bool:
    if not path.is_absolute() or ".." in path.parts:
        _blocked("ENVIRONMENT_ROOT_NOT_CANONICAL", "environment root is not canonical")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except OSError:
            return False
    return False


@dataclass(frozen=True, slots=True)
class _RootPolicy:
    owner_uid: int
    mode: int
    ancestor_owner_uids: frozenset[int]


def _root_policies(
    paths: RuntimePathsV2 | None = None,
) -> tuple[tuple[Path, _RootPolicy], ...]:
    runtime_owner = os.geteuid()
    ancestor_owners = frozenset({0, runtime_owner, Path("/").stat().st_uid})
    data_root = APPROVED_DATA_ROOT if paths is None else paths.semantic_input_root
    reports_root = APPROVED_REPORTS_DIR if paths is None else paths.reports_root
    signals_root = (
        APPROVED_SIGNAL_OUTPUT_DIR if paths is None else paths.signals_root
    )
    scratch_root = APPROVED_SCRATCH_HOME if paths is None else paths.scratch_root
    return (
        (
            data_root,
            _RootPolicy(
                SEMANTIC_ROOT_OWNER_UID if paths is None else runtime_owner,
                0o711,
                ancestor_owners,
            ),
        ),
        (reports_root, _RootPolicy(runtime_owner, 0o700, ancestor_owners)),
        (
            signals_root,
            _RootPolicy(runtime_owner, 0o700, ancestor_owners),
        ),
        (scratch_root, _RootPolicy(runtime_owner, 0o700, ancestor_owners)),
    )


def _validate_ancestor(
    info: os.stat_result, policy: _RootPolicy, *, disposable_tmp: bool = False
) -> None:
    mode = stat.S_IMODE(info.st_mode)
    if (
        disposable_tmp
        and info.st_uid in {0, Path("/").stat().st_uid}
        and mode == 0o1777
    ):
        return
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in policy.ancestor_owner_uids
        or mode & 0o7022
    ):
        _blocked(
            "ENVIRONMENT_ROOT_ANCESTOR_UNSAFE",
            "environment root has an unsafe ancestor",
        )


def _inspect_root(
    path: Path, policy: _RootPolicy, *, allow_disposable_tmp: bool = False
) -> os.stat_result:
    """Open each component relative to a retained directory descriptor."""

    if not path.is_absolute() or ".." in path.parts:
        _blocked("ENVIRONMENT_ROOT_NOT_CANONICAL", "environment root is not canonical")
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        current = os.open(path.anchor, flags)
        descriptors.append(current)
        _validate_ancestor(os.fstat(current), policy)
        components = path.parts[1:]
        current_path = Path(path.anchor)
        for index, component in enumerate(components):
            current = os.open(component, flags, dir_fd=current)
            descriptors.append(current)
            current_path /= component
            if index != len(components) - 1:
                _validate_ancestor(
                    os.fstat(current),
                    policy,
                    disposable_tmp=(
                        allow_disposable_tmp and current_path == Path("/tmp")
                    ),
                )
        return os.fstat(current)
    except FileNotFoundError:
        _blocked("ENVIRONMENT_ROOT_MISSING", "approved environment root does not exist")
    except OSError as exc:
        if _contains_symlink(path):
            _blocked("ENVIRONMENT_ROOT_SYMLINK", "environment root contains a symlink")
        raise EnvironmentValidationError("ENVIRONMENT_ROOT_UNREADABLE", "approved environment root cannot be inspected") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _validate_root(
    path: Path, policy: _RootPolicy, *, allow_disposable_tmp: bool = False
) -> None:
    info = _inspect_root(path, policy, allow_disposable_tmp=allow_disposable_tmp)
    if not stat.S_ISDIR(info.st_mode):
        _blocked("ENVIRONMENT_ROOT_MISSING", "approved environment root is not a directory")
    if info.st_uid != policy.owner_uid:
        _blocked("ENVIRONMENT_ROOT_OWNER_UNSAFE", "approved environment root has an unsafe owner")
    if stat.S_IMODE(info.st_mode) != policy.mode:
        _blocked("ENVIRONMENT_ROOT_MODE_UNSAFE", "approved environment root has an unsafe mode")


@dataclass(
    frozen=True, slots=True, init=False, eq=False, repr=False, weakref_slot=True,
)
class ResearchEnvironmentSettings:
    _roots: tuple[Path, Path, Path, Path]
    _policies: tuple[tuple[Path, _RootPolicy], ...]
    _credentials: tuple[tuple[str, str], ...]
    _package6_fixture: tuple[tuple[str, str], ...]
    _semantic_authority_path: Path | None

    def __repr__(self) -> str:
        return (
            "ResearchEnvironmentSettings(validated=True, "
            f"credentials={len(getattr(self, '_credentials', ()))})"
        )

    @classmethod
    def from_source(cls, source: Mapping[str, str] | None = None) -> "ResearchEnvironmentSettings":
        values = os.environ if source is None else source
        if _PATH_KEYS.intersection(values):
            _blocked("ENVIRONMENT_PATH_OVERRIDE_FORBIDDEN", "runtime path and root overrides are forbidden")
        policies = _root_policies()
        roots = tuple(root for root, _ in policies)
        for root, policy in policies:
            _validate_root(root, policy)
        credentials = tuple(
            (key, value)
            for key in _RESEARCH_KEYS
            if (value := values.get(key)) is not None and value != ""
        )
        if any(key in values for key in _PACKAGE6_FIXTURE_KEYS):
            _blocked(
                "ENVIRONMENT_PACKAGE6_AUTHORITY_FORBIDDEN",
                "Package 6 fixture authority requires validated v2 authority",
            )
        settings = cls()
        object.__setattr__(settings, "_roots", roots)
        object.__setattr__(settings, "_policies", policies)
        object.__setattr__(settings, "_credentials", credentials)
        object.__setattr__(settings, "_package6_fixture", ())
        object.__setattr__(settings, "_semantic_authority_path", None)
        _ISSUED_SETTINGS.add(settings)
        return settings

    @classmethod
    def from_authority(
        cls,
        authority: RuntimeAuthorityV2,
        source: Mapping[str, str] | None = None,
    ) -> "ResearchEnvironmentSettings":
        """Bind child paths to a validated v2 authority, never environment."""

        if not isinstance(authority, RuntimeAuthorityV2) or not isinstance(
            getattr(authority, "runtime_paths", None), RuntimePathsV2
        ):
            raise TypeError("v2 runtime path authority is required")
        values = os.environ if source is None else source
        if _PATH_KEYS.intersection(values):
            _blocked(
                "ENVIRONMENT_PATH_OVERRIDE_FORBIDDEN",
                "runtime path and root overrides are forbidden",
            )
        runtime_paths = cast(RuntimePathsV2, authority.runtime_paths)
        policies = _root_policies(runtime_paths)
        roots = tuple(root for root, _ in policies)
        for root, policy in policies:
            _validate_root(
                root,
                policy,
                allow_disposable_tmp=(
                    getattr(authority, "scope", "") == "PACKAGE6_STAGING_ONLY"
                    and authority._material is not None
                    and root.is_relative_to(authority._material.disposable_root)
                ),
            )
        credentials = tuple(
            (key, value)
            for key in _RESEARCH_KEYS
            if (value := values.get(key)) is not None and value != ""
        )
        supplied = tuple(values.get(key) for key in _PACKAGE6_FIXTURE_KEYS)
        if any(value is not None for value in supplied):
            if not all(isinstance(value, str) and value for value in supplied):
                _blocked(
                    "ENVIRONMENT_PACKAGE6_AUTHORITY_INCOMPLETE",
                    "Package 6 fixture authority is incomplete",
                )
            authority_value = supplied[0]
            approval_value = supplied[1]
            if not isinstance(authority_value, str) or not isinstance(
                approval_value, str
            ):
                _blocked(
                    "ENVIRONMENT_PACKAGE6_AUTHORITY_INCOMPLETE",
                    "Package 6 fixture authority is incomplete",
                )
            authority_path = Path(authority_value)
            if (
                not authority_path.is_absolute()
                or ".." in authority_path.parts
                or str(authority_path) != os.path.normpath(authority_path)
                or authority_path.parts[:2] != ("/", "tmp")
                or _SHA256.fullmatch(approval_value) is None
            ):
                _blocked(
                    "ENVIRONMENT_PACKAGE6_AUTHORITY_INVALID",
                    "Package 6 fixture authority is invalid",
                )
            package6_fixture = tuple(
                zip(
                    _PACKAGE6_FIXTURE_KEYS,
                    (authority_value, approval_value),
                    strict=True,
                )
            )
        else:
            package6_fixture = ()
        settings = cls()
        object.__setattr__(settings, "_roots", roots)
        object.__setattr__(settings, "_policies", policies)
        object.__setattr__(settings, "_credentials", credentials)
        object.__setattr__(settings, "_package6_fixture", package6_fixture)
        object.__setattr__(
            settings,
            "_semantic_authority_path",
            runtime_paths.semantic_authority,
        )
        _ISSUED_SETTINGS.add(settings)
        return settings


_ISSUED_SETTINGS: weakref.WeakSet[ResearchEnvironmentSettings] = weakref.WeakSet()


def build_child_environment(settings: ResearchEnvironmentSettings) -> dict[str, str]:
    """Build a fresh environment from validated fixed paths and dedicated keys."""

    if not isinstance(settings, ResearchEnvironmentSettings) or settings not in _ISSUED_SETTINGS:
        raise TypeError("build_child_environment requires validated environment settings")
    policies = getattr(settings, "_policies", ())
    roots = tuple(root for root, _ in policies)
    if len(policies) != 4 or settings._roots != roots:
        _blocked("ENVIRONMENT_ROOT_NOT_APPROVED", "environment settings no longer name the exact fixed roots")
    for root, policy in policies:
        _validate_root(
            root,
            policy,
            allow_disposable_tmp=(
                settings._semantic_authority_path is not None
                and root.parts[:2] == ("/", "tmp")
            ),
        )
    credential_keys = tuple(key for key, _ in settings._credentials)
    if len(credential_keys) != len(set(credential_keys)) or any(key not in _RESEARCH_KEYS for key in credential_keys):
        _blocked("ENVIRONMENT_CREDENTIAL_NOT_APPROVED", "environment settings contain a non-research credential")
    package6_fixture = getattr(settings, "_package6_fixture", ())
    fixture_keys = tuple(key for key, _ in package6_fixture)
    if fixture_keys not in {(), _PACKAGE6_FIXTURE_KEYS}:
        _blocked(
            "ENVIRONMENT_PACKAGE6_AUTHORITY_INVALID",
            "Package 6 fixture authority is not exact",
        )
    data_root, reports_dir, signal_output_dir, scratch_home = roots
    child = {
        "PATH": FIXED_PATH,
        "HOME": str(scratch_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "TRADING_DATA_ROOT": str(data_root),
        "TRADING_REPORTS_DIR": str(reports_dir),
        "TRADING_SIGNAL_OUTPUT_DIR": str(signal_output_dir),
        **(
            {_SEMANTIC_AUTHORITY_PATH_ENV: str(settings._semantic_authority_path)}
            if settings._semantic_authority_path is not None
            else {}
        ),
        **dict(settings._credentials),
        **dict(package6_fixture),
        "TRADING_MODE": "paper",
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
        "LIVE_TRADING_ENABLED": "false",
    }
    return child


__all__ = [
    "APPROVED_DATA_ROOT", "APPROVED_REPORTS_DIR", "APPROVED_SCRATCH_HOME",
    "APPROVED_SIGNAL_OUTPUT_DIR", "EnvironmentValidationError", "FIXED_PATH",
    "SEMANTIC_ROOT_OWNER_UID",
    "ResearchEnvironmentSettings", "build_child_environment",
]
