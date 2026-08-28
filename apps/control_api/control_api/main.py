from __future__ import annotations

import uvicorn

from services.sentry import configure_sentry

from .app import create_app


def run() -> None:
    configure_sentry()
    uvicorn.run(create_app(), host="127.0.0.1", port=8400, access_log=False)


if __name__ == "__main__":
    run()
