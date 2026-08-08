"""Finite, client-free paper compatibility launcher for sealed Nautilus."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Callable, NoReturn, Sequence


_SIMULATION_LAUNCHER_PATH = Path("/engine/launcher/nautilus_backtest.py")
if not _SIMULATION_LAUNCHER_PATH.is_file():
    _SIMULATION_LAUNCHER_PATH = Path(__file__).with_name("nautilus_backtest.py")
_SIMULATION_SPEC = importlib.util.spec_from_file_location(
    "_sealed_simulation_launcher_helpers", _SIMULATION_LAUNCHER_PATH
)
if _SIMULATION_SPEC is None or _SIMULATION_SPEC.loader is None:
    raise ImportError("reviewed simulation launcher helpers are unavailable")
_SIMULATION = importlib.util.module_from_spec(_SIMULATION_SPEC)
_SIMULATION_SPEC.loader.exec_module(_SIMULATION)

_extract_sealed_wheels = _SIMULATION._extract_sealed_wheels
_sealed_dependency_path_scope = _SIMULATION._sealed_dependency_path_scope
_load_target_portfolio_strategy = _SIMULATION._load_target_portfolio_strategy
_TARGET_PORTFOLIO_STRATEGY_PATH = _SIMULATION._TARGET_PORTFOLIO_STRATEGY_PATH


def _sealed_import_qualification_helpers() -> tuple[object, object, object, Path]:
    """Expose the exact simulation-reviewed import-only helper boundary."""

    return (
        _extract_sealed_wheels,
        _sealed_dependency_path_scope,
        _load_target_portfolio_strategy,
        _TARGET_PORTFOLIO_STRATEGY_PATH,
    )

_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.ASCII,
)
_COMMAND_FIELDS = {
    "command_type",
    "engine_configuration",
    "instrument_catalog",
    "strategy_configuration",
    "strategy_source_sha256",
    "scenario_campaign_sha256",
}
_ARTIFACT_FIELDS = {"artifact_id", "sha256", "media_type"}
_ARTIFACT_NAMES = (
    "engine_configuration",
    "instrument_catalog",
    "strategy_configuration",
)
_MAX_INPUT_BYTES = 8 * 1024 * 1024


def _kernel_process_arguments() -> tuple[bytes, ...]:
    descriptor = -1
    try:
        descriptor = os.open(
            "/proc/self/cmdline",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        raw = b"".join(iter(lambda: os.read(descriptor, 4096), b""))
        if not raw or len(raw) > 1_048_576 or not raw.endswith(b"\0"):
            return ()
        return tuple(raw[:-1].split(b"\0"))
    except OSError:
        return ()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


_KERNEL_PROCESS_ARGUMENTS = _kernel_process_arguments()
_CLEAN_ISOLATED_ENGINE_ENTRY = (
    __name__ == "__main__"
    and __spec__ is None
    and sys.implementation.name == "cpython"
    and sys.flags.isolated == 1
    and sys.flags.no_site == 1
    and sys.flags.ignore_environment == 1
    and sys.flags.no_user_site == 1
    and sys.flags.safe_path is True
    and tuple(sys.orig_argv[1:3]) == ("-I", "-S")
    and tuple(sys.orig_argv[3:]) == tuple(sys.argv)
    and bool(sys.argv)
    and sys.argv[0] == __file__
    and len(_KERNEL_PROCESS_ARGUMENTS) >= 4
    and _KERNEL_PROCESS_ARGUMENTS[1:3] == (b"-I", b"-S")
    and _KERNEL_PROCESS_ARGUMENTS[3:] == tuple(
        os.fsencode(argument) for argument in sys.argv
    )
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _strict_object(raw: bytes, *, label: str) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise ValueError(f"{label} contains a duplicate field")
            result[name] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict) or _canonical(value) != raw:
        raise ValueError(f"{label} must be a canonical JSON object")
    return value


def _artifact(value: object) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or set(value) != _ARTIFACT_FIELDS
        or not isinstance(value.get("artifact_id"), str)
        or _UUID.fullmatch(str(value["artifact_id"])) is None
        or not isinstance(value.get("sha256"), str)
        or _SHA256.fullmatch(str(value["sha256"])) is None
        or value.get("media_type") != "application/json"
    ):
        raise ValueError("paper compatibility artifact reference is invalid")
    return value


def validate_paper_compatibility_request(raw: bytes) -> dict[str, object]:
    """Validate the direct research-only command without job-envelope authority."""

    value = _strict_object(raw, label="paper compatibility command")
    if set(value) != _COMMAND_FIELDS or value.get("command_type") != "ValidatePaperCompatibility":
        raise ValueError("only ValidatePaperCompatibility is accepted")
    identities = []
    for name in _ARTIFACT_NAMES:
        reference = _artifact(value.get(name))
        identities.append(
            (reference["artifact_id"], reference["sha256"], reference["media_type"])
        )
    if len(set(identities)) != len(identities):
        raise ValueError("paper compatibility contains a duplicate artifact reference")
    for name in ("strategy_source_sha256", "scenario_campaign_sha256"):
        digest = value.get(name)
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise ValueError(f"{name} is invalid")
    return value


def _validate_semantics(
    artifacts: tuple[bytes, bytes, bytes],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    engine_configuration = _strict_object(
        artifacts[0], label="engine configuration"
    )
    instrument_catalog = _strict_object(artifacts[1], label="instrument catalog")
    strategy_configuration = _strict_object(
        artifacts[2], label="strategy configuration"
    )
    if engine_configuration != _SIMULATION._SIMULATION_CONFIGURATION:
        raise ValueError("paper compatibility engine configuration is invalid")
    if set(instrument_catalog) != _SIMULATION._CATALOG_FIELDS:
        raise ValueError("paper compatibility instrument catalog is invalid")
    if (
        instrument_catalog.get("instrument") != _SIMULATION._SIMULATION_INSTRUMENT
        or instrument_catalog.get("timeframe") != "1m"
    ):
        raise ValueError("paper compatibility catalog instrument is invalid")
    if (
        set(strategy_configuration) != _SIMULATION._SIMULATION_STRATEGY_FIELDS
        or strategy_configuration.get("schema_version")
        != "nautilus-execution-target-v1"
    ):
        raise ValueError("paper compatibility strategy configuration is invalid")
    positions = strategy_configuration.get("positions")
    if (
        not isinstance(positions, list)
        or len(positions) != 1
        or not isinstance(positions[0], dict)
        or set(positions[0]) != _SIMULATION._SIMULATION_POSITION_FIELDS
        or positions[0].get("instrument") != _SIMULATION._SIMULATION_INSTRUMENT
        or not isinstance(positions[0].get("target_quantity"), str)
    ):
        raise ValueError("paper compatibility target position is invalid")
    return engine_configuration, instrument_catalog, strategy_configuration


def initialize_and_dispose_paper_strategy(
    *,
    engine_configuration: dict[str, object],
    instrument_catalog: dict[str, object],
    strategy_configuration: dict[str, object],
    strategy_type: type,
    configuration_type: type,
    instrument_id_factory: Callable[[str], object],
    bar_type_factory: Callable[[str], object],
) -> None:
    """Construct and dispose the fixed strategy without clients or a node."""

    if engine_configuration != _SIMULATION._SIMULATION_CONFIGURATION:
        raise ValueError("paper compatibility engine configuration is invalid")
    instrument = instrument_catalog.get("instrument")
    positions = strategy_configuration.get("positions")
    if not isinstance(instrument, dict) or not isinstance(positions, list) or len(positions) != 1:
        raise ValueError("paper compatibility strategy semantics are invalid")
    position = positions[0]
    if not isinstance(position, dict) or position.get("instrument") != instrument:
        raise ValueError("paper compatibility strategy instrument is invalid")
    symbol = instrument.get("symbol")
    venue = instrument.get("venue")
    timeframe = instrument_catalog.get("timeframe")
    target_quantity = position.get("target_quantity")
    if not all(isinstance(value, str) and value for value in (symbol, venue, timeframe, target_quantity)):
        raise ValueError("paper compatibility strategy values are invalid")
    if not str(timeframe).endswith("m") or not str(timeframe)[:-1].isdigit():
        raise ValueError("paper compatibility timeframe is invalid")
    instrument_text = f"{symbol}.{venue}"
    configuration = configuration_type(
        instrument_id=instrument_id_factory(instrument_text),
        bar_type=bar_type_factory(
            f"{instrument_text}-{str(timeframe)[:-1]}-MINUTE-LAST-EXTERNAL"
        ),
        target_quantity=target_quantity,
        scenario_id="paper-compatibility",
        execution_plan=(),
        event_semantics=(),
        fee_rate="0",
        slippage_bps="0",
        liquidity_limit="0",
        stale_quote_threshold_seconds=0,
        stop_price=None,
        take_profit_price=None,
        stop_take_profit_precedence="stop-first",
    )
    strategy = strategy_type(configuration)
    try:
        try:
            observed_configuration = getattr(strategy, "config", configuration)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise ValueError("paper strategy initialization cannot be proven") from exc
        if observed_configuration is not configuration:
            raise ValueError("paper strategy initialization cannot be proven")
    finally:
        dispose = getattr(strategy, "dispose", None)
        if not callable(dispose):
            raise ValueError("paper strategy disposal is unavailable")
        dispose()


def _initialize_real_strategy(
    engine_configuration: dict[str, object],
    instrument_catalog: dict[str, object],
    strategy_configuration: dict[str, object],
) -> None:
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.identifiers import InstrumentId

    strategy_type, configuration_type = _load_target_portfolio_strategy()
    initialize_and_dispose_paper_strategy(
        engine_configuration=engine_configuration,
        instrument_catalog=instrument_catalog,
        strategy_configuration=strategy_configuration,
        strategy_type=strategy_type,
        configuration_type=configuration_type,
        instrument_id_factory=InstrumentId.from_str,
        bar_type_factory=BarType.from_str,
    )


def _manifest_bound_strategy_sha256() -> str:
    raw = _SIMULATION._read_regular(_SIMULATION._CLOSURE_MANIFEST_PATH)
    document = _SIMULATION._strict_json_document(raw)
    if not isinstance(document, dict) or not isinstance(document.get("files"), list):
        raise ValueError("closure manifest files are invalid")
    matches = [
        record
        for record in document["files"]
        if isinstance(record, dict)
        and record.get("target") == str(_TARGET_PORTFOLIO_STRATEGY_PATH)
    ]
    if len(matches) != 1:
        raise ValueError("closure manifest must name exactly one target strategy")
    digest = matches[0].get("sha256")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        raise ValueError("target portfolio strategy digest is invalid")
    return digest


def build_paper_compatibility_event(
    request: dict[str, object],
    artifacts: tuple[bytes, bytes, bytes],
    *,
    initialize_and_dispose: Callable[
        [dict[str, object], dict[str, object], dict[str, object]], None
    ] = _initialize_real_strategy,
    manifest_strategy_sha256: Callable[[], str] = _manifest_bound_strategy_sha256,
) -> dict[str, object]:
    if len(artifacts) != len(_ARTIFACT_NAMES):
        raise ValueError("paper compatibility requires exactly three artifacts")
    decoded = _validate_semantics(artifacts)
    for name, raw in zip(_ARTIFACT_NAMES, artifacts, strict=True):
        reference = _artifact(request.get(name))
        if not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), str(reference["sha256"])
        ):
            raise ValueError("paper compatibility artifact digest does not match")
    if not hmac.compare_digest(
        str(request["strategy_source_sha256"]), manifest_strategy_sha256()
    ):
        raise ValueError("paper compatibility strategy source is not manifest-bound")
    initialize_and_dispose(*decoded)
    return {
        "compatible": True,
        "engine_configuration_sha256": _artifact(
            request["engine_configuration"]
        )["sha256"],
        "event_type": "PaperCompatibilityValidated",
        "instrument_catalog_sha256": _artifact(request["instrument_catalog"])[
            "sha256"
        ],
        "scenario_campaign_sha256": request["scenario_campaign_sha256"],
        "strategy_configuration_sha256": _artifact(
            request["strategy_configuration"]
        )["sha256"],
        "strategy_source_sha256": request["strategy_source_sha256"],
    }


def _read_regular(path: Path, *, maximum_size: int = _MAX_INPUT_BYTES) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        observed = os.fstat(descriptor)
        if not stat.S_ISREG(observed.st_mode) or observed.st_size > maximum_size:
            raise ValueError("paper compatibility input is not a regular file")
        chunks: list[bytes] = []
        while block := os.read(descriptor, 65_536):
            chunks.append(block)
        return b"".join(chunks)
    except OSError as exc:
        raise ValueError("paper compatibility input cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _mounted_artifacts(request: dict[str, object]) -> tuple[bytes, bytes, bytes]:
    values: list[bytes] = []
    root = Path("/inputs/artifacts")
    for name in _ARTIFACT_NAMES:
        reference = _artifact(request[name])
        values.append(
            _read_regular(root / f"{name}-{reference['sha256']}.json")
        )
    return tuple(values)  # type: ignore[return-value]


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: Sequence[str] | None = None) -> None:
    if not _CLEAN_ISOLATED_ENGINE_ENTRY:
        _fail("paper compatibility requires direct CPython -I -S execution")
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:2] != ["--profile", "paper-compatibility"] or len(arguments) != 4:
        _fail("expected the attested paper compatibility profile and request inputs")
    try:
        request_raw = _read_regular(Path(arguments[2]))
        sidecar = _read_regular(Path(arguments[3])).decode("ascii").strip()
        if _SHA256.fullmatch(sidecar) is None or not hmac.compare_digest(
            hashlib.sha256(request_raw).hexdigest(), sidecar
        ):
            raise ValueError("request digest sidecar does not bind the command")
        request = validate_paper_compatibility_request(request_raw)
        artifacts = _mounted_artifacts(request)
        with tempfile.TemporaryDirectory(
            prefix="nautilus-paper-compatibility-", dir="/tmp"
        ) as temporary:
            roots = _extract_sealed_wheels(
                Path("/engine/wheels"), Path(temporary) / "wheels"
            )
            with _sealed_dependency_path_scope(roots):
                event = build_paper_compatibility_event(request, artifacts)
        print(_canonical(event).decode("ascii"))
    except (ImportError, OSError, UnicodeDecodeError, ValueError) as exc:
        _fail(str(exc))


if __name__ == "__main__":
    main()
