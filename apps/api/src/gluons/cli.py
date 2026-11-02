"""Operator commands. `gluons --help` lists them."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from gluons.config import REPO_ROOT


def _migrate(_: argparse.Namespace) -> int:
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=REPO_ROOT / "apps" / "api", env=os.environ, check=False
    ).returncode


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("gluons.api.app:app", host=args.host, port=args.port)
    return 0


def _worker(_: argparse.Namespace) -> int:
    from gluons.runtime.worker import main

    main()
    return 0


def _skills(_: argparse.Namespace) -> int:
    from gluons.skills.catalog import sync_versions

    count = sync_versions()
    print(f"validated and pinned {count} skills")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="gluons")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply database migrations").set_defaults(fn=_migrate)
    serve = commands.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(fn=_serve)
    commands.add_parser("worker", help="run the background worker").set_defaults(fn=_worker)
    commands.add_parser("skills-sync", help="validate contracts and pin them").set_defaults(fn=_skills)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
