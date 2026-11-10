"""Isolated real API and PostgreSQL for browser QA; no provider credentials or writes."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))


def main() -> None:
    from gluons import devdb

    db = devdb.start(ROOT / ".pgdata" / "web-e2e", database=f"gluons_web_e2e_{os.getpid()}")
    os.environ.update(db.environment())
    os.environ.update({
        "GLUONS_ENV": "test",
        "GLUONS_MAIL_TRANSPORT": "file",
        "GLUONS_MAIL_DIR": str(ROOT / "apps" / "web" / ".e2e-mail"),
        "GLUONS_PUBLIC_API_URL": "http://localhost:8001",
        "GLUONS_PUBLIC_WEB_URL": "http://localhost:3001",
        "GLUONS_ALLOWED_WEB_ORIGINS": "http://localhost:3001",
        "GLUONS_GOOGLE_CLIENT_ID": "",
        "GLUONS_GOOGLE_CLIENT_SECRET": "",
    })
    devdb.migrate(db)
    from gluons.config import get_settings
    from gluons.db.session import reset_engines
    get_settings.cache_clear()
    reset_engines()

    from gluons.runtime import worker

    stop = threading.Event()

    def advance() -> None:
        while not stop.is_set():
            worker.drain()
            time.sleep(0.25)

    threading.Thread(target=advance, daemon=True).start()
    import uvicorn
    try:
        uvicorn.run("gluons.api.app:app", host="127.0.0.1", port=8001, log_level="warning", access_log=False)
    finally:
        stop.set()


if __name__ == "__main__":
    main()
