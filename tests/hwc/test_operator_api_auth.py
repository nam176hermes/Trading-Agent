from __future__ import annotations

import hmac
import os
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from apps.operator_api import auth as auth_module
from apps.operator_api.auth import OperatorAuthenticator, load_private_token
from apps.operator_api.config import (
    OperatorApiConfigurationError,
    OperatorApiSettings,
)
from apps.operator_api.errors import OperatorApiError
from packages.operator_control import credentials as credential_module


WEB_TOKEN = b"w" * 32
CLI_TOKEN = b"c" * 32


def write_token(path: Path, value: bytes, mode: int = 0o600) -> None:
    path.write_bytes(value)
    path.chmod(mode)


def env_for(tmp_path: Path) -> dict[str, str]:
    web = tmp_path / "web.token"
    cli = tmp_path / "cli.token"
    write_token(web, WEB_TOKEN + b"\n")
    write_token(cli, CLI_TOKEN)
    return {
        "OPERATOR_API_WEB_TOKEN_FILE": str(web),
        "OPERATOR_API_WEB_PRINCIPAL_ID": "operator.web",
        "OPERATOR_API_CLI_TOKEN_FILE": str(cli),
        "OPERATOR_API_CLI_PRINCIPAL_ID": "operator.cli",
    }


def scope(*headers: tuple[bytes, bytes]) -> dict[str, object]:
    return {"type": "http", "headers": list(headers)}


@pytest.mark.parametrize(
    "missing",
    (
        "OPERATOR_API_WEB_TOKEN_FILE",
        "OPERATOR_API_WEB_PRINCIPAL_ID",
        "OPERATOR_API_CLI_TOKEN_FILE",
        "OPERATOR_API_CLI_PRINCIPAL_ID",
    ),
)
def test_settings_require_every_credential_field(tmp_path: Path, missing: str) -> None:
    environment = env_for(tmp_path)
    del environment[missing]
    with pytest.raises(OperatorApiConfigurationError, match="invalid"):
        OperatorApiSettings.from_env(environment)


@pytest.mark.parametrize(
    "key,value",
    (
        ("OPERATOR_API_WEB_TOKEN_FILE", "relative.token"),
        ("OPERATOR_API_WEB_TOKEN_FILE", "/tmp/../tmp/token"),
        ("OPERATOR_API_WEB_TOKEN_FILE", "/tmp//token"),
        ("OPERATOR_API_WEB_PRINCIPAL_ID", ""),
        ("OPERATOR_API_WEB_PRINCIPAL_ID", "bad principal"),
        ("OPERATOR_API_CLI_PRINCIPAL_ID", "x" * 129),
    ),
)
def test_settings_reject_noncanonical_paths_and_principals(
    tmp_path: Path, key: str, value: str
) -> None:
    environment = env_for(tmp_path)
    environment[key] = value
    with pytest.raises(OperatorApiConfigurationError, match="invalid"):
        OperatorApiSettings.from_env(environment)


def test_bind_contract_is_fixed(tmp_path: Path) -> None:
    settings = OperatorApiSettings.from_env(env_for(tmp_path))
    assert (settings.bind_host, settings.bind_port) == ("127.0.0.1", 8402)
    with pytest.raises(OperatorApiConfigurationError):
        OperatorApiSettings(
            settings.web_token_file,
            settings.web_principal_id,
            settings.cli_token_file,
            settings.cli_principal_id,
            bind_host="0.0.0.0",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "value",
    (
        b"x" * 31,
        b"x" * 4097,
        b" leading-token-that-is-long-enough",
        b"trailing-token-that-is-long-enough ",
        b"internal token value that is long enough",
        b"x" * 32 + b"\n\n",
        b"x" * 31 + b"\x00",
        b"x" * 31 + b"\xff",
    ),
)
def test_token_loader_rejects_invalid_content(tmp_path: Path, value: bytes) -> None:
    target = tmp_path / "token"
    write_token(target, value)
    with pytest.raises(OperatorApiConfigurationError, match="unavailable") as caught:
        load_private_token(target)
    assert str(target) not in str(caught.value)


@pytest.mark.parametrize("kind", ("missing", "symlink", "directory", "fifo", "socket"))
def test_token_loader_rejects_absent_or_nonregular_objects(
    tmp_path: Path, kind: str
) -> None:
    cleanup: Path | None = None
    if kind == "socket":
        cleanup = Path(tempfile.mkdtemp(prefix="hwc-auth-", dir="/tmp"))
        tmp_path = cleanup
    target = tmp_path / "token"
    sock: socket.socket | None = None
    if kind == "symlink":
        target.symlink_to("/dev/null")
    elif kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "socket":
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(target))
    try:
        with pytest.raises(OperatorApiConfigurationError, match="unavailable"):
            load_private_token(target)
    finally:
        if sock is not None:
            sock.close()
        if cleanup is not None:
            shutil.rmtree(cleanup)


def test_token_loader_rejects_unsafe_mode_owner_and_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "token"
    write_token(target, WEB_TOKEN, 0o640)
    with pytest.raises(OperatorApiConfigurationError):
        load_private_token(target)

    target.chmod(0o600)
    monkeypatch.setattr(credential_module.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(OperatorApiConfigurationError):
        load_private_token(target)
    monkeypatch.undo()

    tmp_path.chmod(0o777)
    with pytest.raises(OperatorApiConfigurationError):
        load_private_token(target)


def test_token_loader_rejects_symlinked_ancestor(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    write_token(real / "token", WEB_TOKEN)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(OperatorApiConfigurationError):
        load_private_token(alias / "token")


def test_authenticator_requires_distinct_tokens(tmp_path: Path) -> None:
    environment = env_for(tmp_path)
    Path(environment["OPERATOR_API_CLI_TOKEN_FILE"]).write_bytes(WEB_TOKEN)
    with pytest.raises(OperatorApiConfigurationError, match="unavailable"):
        OperatorAuthenticator(OperatorApiSettings.from_env(environment))


def test_authenticator_compares_both_tokens_and_returns_exact_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authenticator = OperatorAuthenticator(
        OperatorApiSettings.from_env(env_for(tmp_path))
    )
    calls: list[tuple[bytes, bytes]] = []
    original_compare = hmac.compare_digest

    def compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return original_compare(left, right)

    monkeypatch.setattr(auth_module.hmac, "compare_digest", compare)
    actor = authenticator.authenticate(
        scope((b"authorization", b"Bearer " + CLI_TOKEN))
    )
    assert (actor.interface, actor.principal_id) == ("CLI", "operator.cli")
    assert calls == [(CLI_TOKEN, WEB_TOKEN), (CLI_TOKEN, CLI_TOKEN)]


@pytest.mark.parametrize(
    "headers",
    (
        (),
        ((b"authorization", b"Basic value"),),
        ((b"authorization", b"Bearer"),),
        ((b"authorization", b"Bearer wrong"),),
        (
            (b"authorization", b"Bearer " + WEB_TOKEN),
            (b"authorization", b"Bearer " + WEB_TOKEN),
        ),
    ),
)
def test_authenticator_rejects_missing_malformed_duplicate_or_wrong_bearer(
    tmp_path: Path, headers: tuple[tuple[bytes, bytes], ...]
) -> None:
    authenticator = OperatorAuthenticator(
        OperatorApiSettings.from_env(env_for(tmp_path))
    )
    with pytest.raises(OperatorApiError) as caught:
        authenticator.authenticate(scope(*headers))
    assert (caught.value.status_code, caught.value.code) == (
        401,
        "AUTHENTICATION_REQUIRED",
    )
    assert WEB_TOKEN.decode() not in str(caught.value)


def test_interface_headers_cannot_spoof_token_identity(tmp_path: Path) -> None:
    authenticator = OperatorAuthenticator(
        OperatorApiSettings.from_env(env_for(tmp_path))
    )
    actor = authenticator.authenticate(
        scope(
            (b"authorization", b"Bearer " + WEB_TOKEN),
            (b"x-operator-interface", b"CLI"),
            (b"x-operator-role", b"admin"),
        )
    )
    assert (actor.interface, actor.principal_id) == ("WEB", "operator.web")
