from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import hmac
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Any, Sequence

from .offline_wheelhouse import verify_offline_wheelhouse


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40,64}\Z")
_DEFAULT_EXCLUSIONS = (
    ".git",
    ".env",
    ".env.*",
    ".venv",
    "__pycache__",
    "*.pyc",
    "*.key",
    "*.keys.enc",
    "*.pem",
)
_ENTRY_KEYS = ("path", "type", "mode", "size", "sha256")
_MANIFEST_KEYS = (
    "manifest_version",
    "release_type",
    "git_commit",
    "python_identity",
    "entries",
    "aggregate_sha256",
)
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_PYTHON_IDENTITY = re.compile(r"CPython 3\.11\.\d+\Z")
_PHASE4_APP_LINKS = ("crypto-research", "legacy-trading-agent", "trading-dashboard")


@dataclass(frozen=True)
class ReleasePolicy:
    expected_uid: int = 0
    expected_gid: int = 0
    exclusions: tuple[str, ...] = ()
    python_executable: str = "python3.11"
    create_venv: bool = True
    install_dependencies: bool = True
    release_type: str = "generic"
    expected_git_commit: str | None = None
    expected_python_identity: str | None = None
    excluded_git_symlink_paths: tuple[str, ...] = ()
    uv_offline: bool = True
    offline_wheelhouse: str | None = None
    remove_console_scripts: bool = True
    included_git_paths: tuple[str, ...] = ()

    @property
    def all_exclusions(self) -> tuple[str, ...]:
        return _DEFAULT_EXCLUSIONS + self.exclusions


@dataclass(frozen=True)
class BuildResult:
    commit: str
    manifest_path: Path
    digest: str
    entries: list[dict[str, Any]]
    python_identity: str


def phase4_app_release_policy(**overrides: Any) -> ReleasePolicy:
    """Return the code-owned application policy with only its known links excluded."""
    additional = tuple(overrides.pop("exclusions", ()))
    return ReleasePolicy(
        release_type="phase4-app",
        exclusions=additional,
        excluded_git_symlink_paths=_PHASE4_APP_LINKS,
        **overrides,
    )


def phase4_backend_release_policy(
    *, audited_paths: Sequence[str], **overrides: Any,
) -> ReleasePolicy:
    """Return a deny-by-default backend policy for an audited import closure."""

    normalized = tuple(sorted(set(audited_paths), key=os.fsencode))
    if not normalized or len(normalized) != len(tuple(audited_paths)):
        raise ValueError("backend audited paths must be non-empty and duplicate-free")
    for path in normalized:
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or str(pure) != path:
            raise ValueError("invalid backend audited path")
    return ReleasePolicy(
        release_type="phase4-backend",
        included_git_paths=normalized,
        **overrides,
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_metadata(metadata: os.stat_result, policy: ReleasePolicy) -> None:
    if metadata.st_uid != policy.expected_uid or metadata.st_gid != policy.expected_gid:
        raise ValueError("unsafe ownership")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o7022:
        raise ValueError("unsafe mode")


def _sha256_at(directory_fd: int, name: str, expected: os.stat_result) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        actual = os.fstat(file_fd)
        if not stat.S_ISREG(actual.st_mode):
            raise ValueError("unsafe file type")
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError("file changed during verification")
        digest = hashlib.sha256()
        while chunk := os.read(file_fd, 1024 * 1024):
            digest.update(chunk)
        final = os.fstat(file_fd)
        if (final.st_size, final.st_mtime_ns) != (actual.st_size, actual.st_mtime_ns):
            raise ValueError("file changed during verification")
        return digest.hexdigest()
    finally:
        os.close(file_fd)


def _walk(directory_fd: int, prefix: str, policy: ReleasePolicy) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with os.scandir(directory_fd) as children:
        names = sorted((child.name for child in children), key=os.fsencode)
    for name in names:
        if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name:
            raise ValueError("unsafe path")
        relative = f"{prefix}/{name}" if prefix else name
        relative.encode("utf-8", "strict")
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        _safe_metadata(metadata, policy)
        mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
        if stat.S_ISREG(metadata.st_mode):
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": mode,
                    "size": metadata.st_size,
                    "sha256": _sha256_at(directory_fd, name, metadata),
                }
            )
        elif stat.S_ISDIR(metadata.st_mode):
            entries.append(
                {
                    "path": relative,
                    "type": "directory",
                    "mode": mode,
                    "size": 0,
                    "sha256": _EMPTY_SHA256,
                }
            )
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            child_fd = os.open(name, flags, dir_fd=directory_fd)
            try:
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise ValueError("directory changed during verification")
                entries.extend(_walk(child_fd, relative, policy))
            finally:
                os.close(child_fd)
        else:
            raise ValueError("unsafe file type")
    return entries


def create_manifest(release_root: Path | str, policy: ReleasePolicy) -> list[dict[str, Any]]:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(os.fspath(release_root), flags)
    try:
        _safe_metadata(os.fstat(root_fd), policy)
        return sorted(_walk(root_fd, "", policy), key=lambda entry: entry["path"].encode("utf-8"))
    finally:
        os.close(root_fd)


def _manifest_envelope(
    entries: Sequence[dict[str, Any]],
    *,
    release_type: str,
    git_commit: str,
    python_identity: str,
) -> dict[str, Any]:
    if not release_type or not isinstance(release_type, str):
        raise ValueError("invalid release type")
    if not isinstance(git_commit, str) or _COMMIT.fullmatch(git_commit) is None:
        raise ValueError("invalid git commit")
    if not isinstance(python_identity, str) or _PYTHON_IDENTITY.fullmatch(python_identity) is None:
        raise ValueError("invalid Python identity")
    normalized_entries = list(entries)
    return {
        "manifest_version": 1,
        "release_type": release_type,
        "git_commit": git_commit,
        "python_identity": python_identity,
        "entries": normalized_entries,
        "aggregate_sha256": hashlib.sha256(_canonical(normalized_entries)).hexdigest(),
    }


def write_manifest(
    entries: Sequence[dict[str, Any]],
    manifest_path: Path | str,
    *,
    release_type: str,
    git_commit: str,
    python_identity: str,
) -> str:
    envelope = _manifest_envelope(
        entries,
        release_type=release_type,
        git_commit=git_commit,
        python_identity=python_identity,
    )
    canonical = _canonical(envelope)
    path = Path(manifest_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_fd = os.open(path, flags, 0o644)
    try:
        os.fchmod(file_fd, 0o644)
        with os.fdopen(file_fd, "wb", closefd=False) as output:
            output.write(canonical)
            output.write(b"\n")
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(file_fd)
    return hashlib.sha256(canonical).hexdigest()


def _read_manifest(manifest_path: Path | str, policy: ReleasePolicy) -> tuple[dict[str, Any], bytes]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_fd = os.open(os.fspath(manifest_path), flags)
    try:
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("unsafe manifest")
        _safe_metadata(metadata, policy)
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(file_fd, 1024 * 1024):
            total += len(chunk)
            if total > 64 * 1024 * 1024:
                raise ValueError("manifest too large")
            chunks.append(chunk)
        raw = b"".join(chunks)
    finally:
        os.close(file_fd)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or tuple(parsed.keys()) != _MANIFEST_KEYS:
        raise ValueError("invalid manifest")
    if parsed["manifest_version"] != 1:
        raise ValueError("invalid manifest version")
    if not isinstance(parsed["release_type"], str) or not parsed["release_type"]:
        raise ValueError("invalid release type")
    if not isinstance(parsed["git_commit"], str) or _COMMIT.fullmatch(parsed["git_commit"]) is None:
        raise ValueError("invalid git commit")
    if not isinstance(parsed["python_identity"], str) or _PYTHON_IDENTITY.fullmatch(parsed["python_identity"]) is None:
        raise ValueError("invalid Python identity")
    if not isinstance(parsed["entries"], list):
        raise ValueError("invalid entries")
    entries: list[dict[str, Any]] = []
    previous: bytes | None = None
    for item in parsed["entries"]:
        if not isinstance(item, dict) or tuple(item.keys()) != _ENTRY_KEYS:
            raise ValueError("invalid manifest entry")
        path = item["path"]
        encoded = path.encode("utf-8", "strict") if isinstance(path, str) else b""
        pure_path = PurePosixPath(path) if isinstance(path, str) else PurePosixPath(".")
        if not encoded or pure_path.is_absolute() or ".." in pure_path.parts or str(pure_path) != path:
            raise ValueError("invalid manifest path")
        if previous is not None and encoded <= previous:
            raise ValueError("invalid manifest order")
        previous = encoded
        if item["type"] not in {"file", "directory"}:
            raise ValueError("invalid manifest type")
        if not isinstance(item["mode"], str) or re.fullmatch(r"[0-7]{4}", item["mode"]) is None:
            raise ValueError("invalid manifest mode")
        if not isinstance(item["size"], int) or isinstance(item["size"], bool) or item["size"] < 0:
            raise ValueError("invalid manifest size")
        if not isinstance(item["sha256"], str) or _DIGEST.fullmatch(item["sha256"]) is None:
            raise ValueError("invalid manifest digest")
        entries.append(item)
    aggregate = hashlib.sha256(_canonical(entries)).hexdigest()
    if not isinstance(parsed["aggregate_sha256"], str) or not hmac.compare_digest(
        parsed["aggregate_sha256"], aggregate
    ):
        raise ValueError("invalid entry aggregate")
    canonical = _canonical(parsed)
    if raw != canonical + b"\n":
        raise ValueError("noncanonical manifest")
    return parsed, canonical


def verify_release(
    release_root: Path | str,
    manifest_path: Path | str,
    expected_digest: str,
    policy: ReleasePolicy,
) -> bool:
    try:
        if not isinstance(expected_digest, str) or _DIGEST.fullmatch(expected_digest) is None:
            raise ValueError("invalid expected digest")
        if not isinstance(policy.expected_git_commit, str) or _COMMIT.fullmatch(policy.expected_git_commit) is None:
            raise ValueError("missing commit authority")
        if (
            not isinstance(policy.expected_python_identity, str)
            or _PYTHON_IDENTITY.fullmatch(policy.expected_python_identity) is None
        ):
            raise ValueError("missing interpreter authority")
        envelope, canonical = _read_manifest(manifest_path, policy)
        actual_digest = hashlib.sha256(canonical).hexdigest()
        if not hmac.compare_digest(actual_digest, expected_digest):
            raise ValueError("manifest digest mismatch")
        if not hmac.compare_digest(envelope["release_type"], policy.release_type):
            raise ValueError("release type mismatch")
        if not hmac.compare_digest(envelope["git_commit"], policy.expected_git_commit):
            raise ValueError("git commit mismatch")
        if not hmac.compare_digest(envelope["python_identity"], policy.expected_python_identity):
            raise ValueError("interpreter mismatch")
        actual_entries = create_manifest(release_root, policy)
        if not hmac.compare_digest(_canonical(actual_entries), _canonical(envelope["entries"])):
            raise ValueError("release content mismatch")
        return True
    except Exception:
        raise ValueError("release verification failed") from None


def _excluded(path: str, policy: ReleasePolicy) -> bool:
    pure = PurePosixPath(path)
    for pattern in policy.all_exclusions:
        if pure.match(pattern) or any(PurePosixPath(part).match(pattern) for part in pure.parts):
            return True
        if path == pattern or path.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def _resolve_commit(source_git_dir: Path, commit: str) -> str:
    if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
        raise ValueError("commit must be an exact object id")
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
        cwd=source_git_dir,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not hmac.compare_digest(resolved, commit):
        raise ValueError("commit is not canonical")
    return resolved


def _export_git_object(source_git_dir: Path, commit: str, destination: Path, policy: ReleasePolicy) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit],
        cwd=source_git_dir,
        check=True,
        capture_output=True,
    ).stdout
    exported_files: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source:
            name = member.name.removesuffix("/")
            pure = PurePosixPath(name)
            if not name or pure.is_absolute() or ".." in pure.parts or str(pure) != name:
                raise ValueError("unsafe archive path")
            if member.issym() or member.islnk():
                if member.issym() and name in policy.excluded_git_symlink_paths:
                    continue
                raise ValueError("unsafe archive link")
            if policy.included_git_paths and not (
                name in policy.included_git_paths
                or any(path.startswith(name.rstrip("/") + "/") for path in policy.included_git_paths)
            ):
                continue
            if _excluded(name, policy):
                continue
            output = destination.joinpath(*pure.parts)
            if member.isdir():
                output.mkdir(mode=0o755, parents=True, exist_ok=True)
                output.chmod(0o755)
            elif member.isfile():
                exported_files.add(name)
                output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                for parent in (output.parent, *output.parent.parents):
                    if parent == destination.parent:
                        break
                    if parent.is_relative_to(destination):
                        parent.chmod(0o755)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                normalized_mode = 0o755 if member.mode & 0o111 else 0o644
                output_fd = os.open(output, flags, normalized_mode)
                try:
                    os.fchmod(output_fd, normalized_mode)
                    extracted = source.extractfile(member)
                    if extracted is None:
                        raise ValueError("missing archive content")
                    with os.fdopen(output_fd, "wb", closefd=False) as target:
                        shutil.copyfileobj(extracted, target)
                finally:
                    os.close(output_fd)
            else:
                raise ValueError("unsafe archive member")
    if policy.included_git_paths and exported_files != set(policy.included_git_paths):
        raise ValueError("audited release path set is incomplete")


def _python_identity(executable: str | Path) -> str:
    identity = subprocess.run(
        [
            os.fspath(executable),
            "-c",
            "import platform; print(f'{platform.python_implementation()} {platform.python_version()}')",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if _PYTHON_IDENTITY.fullmatch(identity) is None:
        raise ValueError("wrong Python interpreter")
    return identity


def _normalize_modes(root: Path) -> None:
    root.chmod(0o755)
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                raise ValueError("unsafe release link")
            path.chmod(0o755)
        for name in files:
            path = current_path / name
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("unsafe release type")
            path.chmod(0o755 if metadata.st_mode & 0o111 else 0o644)


def _make_venv_relocatable(venv: Path, staging: Path, policy: ReleasePolicy) -> None:
    bin_directory = venv / "bin"
    if not policy.remove_console_scripts:
        raise ValueError("console scripts require a fixed installation path")
    for script in bin_directory.iterdir():
        if not script.name.startswith("python"):
            if script.is_file() or script.is_symlink():
                script.unlink()
    for name in ("python", "python3", "python3.11"):
        interpreter = bin_directory / name
        interpreter.unlink(missing_ok=True)
        shutil.copy2(policy.python_executable, interpreter, follow_symlinks=True)
        interpreter.chmod(0o755)
    site_packages = venv / "lib/python3.11/site-packages"
    removed_metadata_names = {"direct_url.json", "uv_cache.json"}
    for metadata_name in removed_metadata_names:
        for path_bound_metadata in site_packages.glob(f"*.dist-info/{metadata_name}"):
            path_bound_metadata.unlink()
    for record in site_packages.glob("*.dist-info/RECORD"):
        rows = list(csv.reader(record.read_text().splitlines()))
        retained = []
        for row in rows:
            parts = PurePosixPath(row[0]).parts if row else ()
            is_removed_script = len(parts) >= 2 and parts[-2] == "bin" and all(
                part == ".." for part in parts[:-2]
            )
            is_removed_local_provenance = bool(parts) and parts[-1] in removed_metadata_names
            if not is_removed_script and not is_removed_local_provenance:
                retained.append(row)
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerows(retained)
        record.write_text(buffer.getvalue())
    config = venv / "pyvenv.cfg"
    lines = [line for line in config.read_text().splitlines() if not line.startswith("command = ")]
    config.write_text("\n".join(lines) + "\n")
    staging_bytes = os.fsencode(staging)
    for current, directories, files in os.walk(venv, followlinks=False):
        for name in directories:
            if (Path(current) / name).is_symlink():
                raise ValueError("unsafe venv link")
        for name in files:
            if staging_bytes in (Path(current) / name).read_bytes():
                raise ValueError("staging path remains in venv")


def _remove_bytecode(venv: Path) -> None:
    for cache in sorted(venv.rglob("__pycache__"), key=lambda path: len(path.parts), reverse=True):
        if cache.is_symlink() or not cache.is_dir():
            raise ValueError("unsafe bytecode cache")
        shutil.rmtree(cache)
    for bytecode in venv.rglob("*.pyc"):
        if bytecode.is_symlink() or not bytecode.is_file():
            raise ValueError("unsafe bytecode file")
        bytecode.unlink()


def _write_relocatable_source_paths(destination: Path, venv: Path) -> None:
    control_api = destination / "apps/control_api"
    if not control_api.is_dir() or control_api.is_symlink():
        raise ValueError("release control API source is absent")
    site_packages = venv / "lib/python3.11/site-packages"
    if not site_packages.is_dir() or site_packages.is_symlink():
        raise ValueError("release site-packages is absent")
    source_paths = site_packages / "runtime-release-source.pth"
    source_paths.write_text("../../../..\n../../../../apps/control_api\n", encoding="utf-8")
    source_paths.chmod(0o644)


def _create_locked_venv(destination: Path, policy: ReleasePolicy) -> str:
    _python_identity(policy.python_executable)
    wheelhouse: Path | None = None
    wheelhouse_digest: str | None = None
    if policy.install_dependencies and policy.uv_offline and policy.offline_wheelhouse is not None:
        wheelhouse = Path(policy.offline_wheelhouse)
        wheelhouse_digest = verify_offline_wheelhouse(wheelhouse, destination / "uv.lock")
    venv = destination / ".venv"
    subprocess.run(
        [policy.python_executable, "-m", "venv", "--without-pip", "--copies", os.fspath(venv)],
        check=True,
        capture_output=True,
    )
    lib64 = venv / "lib64"
    if lib64.is_symlink():
        lib64.unlink()
    if policy.install_dependencies:
        if not (destination / "uv.lock").is_file() or not (destination / "pyproject.toml").is_file():
            raise ValueError("locked dependency inputs are absent")
        environment = {
            **os.environ,
            "UV_COMPILE_BYTECODE": "0",
            "VIRTUAL_ENV": os.fspath(venv),
        }
        if wheelhouse is None:
            command = [
                "uv",
                "sync",
                "--frozen",
                "--no-dev",
                "--no-editable",
                "--active",
                "--link-mode",
                "copy",
                "--no-python-downloads",
            ]
            if policy.uv_offline:
                command.append("--offline")
            subprocess.run(
                command,
                cwd=destination,
                env=environment,
                check=True,
                capture_output=True,
            )
        else:
            requirements = destination / ".runtime-release-requirements.txt"
            try:
                subprocess.run(
                    [
                        "uv",
                        "export",
                        "--frozen",
                        "--no-dev",
                        "--no-editable",
                        "--no-emit-project",
                        "--format",
                        "requirements.txt",
                        "--output-file",
                        os.fspath(requirements),
                    ],
                    cwd=destination,
                    env=environment,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "uv",
                        "pip",
                        "sync",
                        os.fspath(requirements),
                        "--python",
                        os.fspath(venv / "bin/python"),
                        "--require-hashes",
                        "--strict",
                        "--only-binary=:all:",
                        "--offline",
                        "--no-index",
                        "--find-links",
                        os.fspath(wheelhouse),
                        "--no-cache",
                        "--link-mode",
                        "copy",
                        "--no-python-downloads",
                    ],
                    cwd=destination,
                    env=environment,
                    check=True,
                    capture_output=True,
                )
            finally:
                requirements.unlink(missing_ok=True)
        if wheelhouse is not None and verify_offline_wheelhouse(wheelhouse, destination / "uv.lock") != wheelhouse_digest:
            raise ValueError("offline wheelhouse changed during build")
        if wheelhouse is not None:
            _write_relocatable_source_paths(destination, venv)
    lib64 = venv / "lib64"
    if lib64.is_symlink():
        lib64.unlink()
    _make_venv_relocatable(venv, destination, policy)
    if wheelhouse is not None:
        wheelhouse_bytes = os.fsencode(wheelhouse)
        for current, _directories, files in os.walk(venv, followlinks=False):
            for name in files:
                if wheelhouse_bytes in (Path(current) / name).read_bytes():
                    raise ValueError("wheelhouse path remains in venv")
    identity = _python_identity(venv / "bin/python")
    _remove_bytecode(venv)
    return identity


def build_release(
    source_git_dir: Path | str,
    commit: str,
    destination: Path | str,
    policy: ReleasePolicy,
) -> BuildResult:
    source = Path(source_git_dir)
    target = Path(destination)
    manifest_path = target.with_name(f"{target.name}.manifest.json")
    staging: Path | None = None
    try:
        if target.exists() or target.is_symlink() or manifest_path.exists() or manifest_path.is_symlink():
            raise ValueError("destination exists")
        resolved = _resolve_commit(source, commit)
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        staging.chmod(0o755)
        _export_git_object(source, resolved, staging, policy)
        python_identity = _python_identity(policy.python_executable)
        if policy.create_venv:
            python_identity = _create_locked_venv(staging, policy)
        _normalize_modes(staging)
        entries = create_manifest(staging, policy)
        digest = write_manifest(
            entries,
            manifest_path,
            release_type=policy.release_type,
            git_commit=resolved,
            python_identity=python_identity,
        )
        staging.rename(target)
        staging = None
        return BuildResult(resolved, manifest_path, digest, entries, python_identity)
    except Exception:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        try:
            manifest_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError("release build failed") from None
