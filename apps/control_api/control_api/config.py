from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True, slots=True)
class Settings:
    data_root: Path
    store_backend: str = "legacy"
    stale_after_seconds: int = 1800
    service_name: str = "trading-control-api"
    git_commit: str = "unknown-unbuilt"
    build_time: str = "local-build"
    deployment_id: str = "phase2-local"
    allowed_origins: tuple[str, ...] = ("http://127.0.0.1:3000", "http://localhost:3000")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        values = os.environ if env is None else env
        raw_root = values.get("TRADING_DATA_ROOT")
        if not raw_root:
            raise ValueError("TRADING_DATA_ROOT is required")
        origins = tuple(
            origin.strip()
            for origin in values.get(
                "CONTROL_API_ALLOWED_ORIGINS",
                "http://127.0.0.1:3000,http://localhost:3000",
            ).split(",")
            if origin.strip()
        )
        backend = values.get("TRADING_STORE_BACKEND", "legacy").strip().lower()
        if backend not in {"legacy", "postgres"}:
            raise ValueError("TRADING_STORE_BACKEND must be legacy or postgres")
        return cls(
            data_root=Path(raw_root).expanduser().resolve(),
            store_backend=backend,
            stale_after_seconds=int(values.get("TRADING_DATA_STALE_AFTER_SECONDS", "1800")),
            git_commit=values.get("GIT_COMMIT", "unknown-unbuilt"),
            build_time=values.get("BUILD_TIME", "local-build"),
            deployment_id=values.get("DEPLOYMENT_ID", "phase2-local"),
            allowed_origins=origins,
        )
