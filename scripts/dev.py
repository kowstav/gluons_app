"""Run local API, durable worker, labelled ERP simulator and Next.js without Docker."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-web", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    from gluons import devdb
    db = devdb.start(ROOT / ".pgdata" / "dev")
    env = {**os.environ, **db.environment(), "GLUONS_ENV": "local", "PYTHONUTF8": "1"}
    devdb.migrate(db)
    subprocess.run([sys.executable, "-m", "gluons.cli", "skills-sync"], cwd=ROOT, env=env, check=True)
    logs = ROOT / ".gluons-data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    commands = [
        ("api", [sys.executable, "-m", "gluons.cli", "serve"]),
        ("worker", [sys.executable, "-m", "gluons.cli", "worker"]),
        ("simulator", [sys.executable, "-m", "uvicorn", "sandbox_erp:app", "--app-dir", str(ROOT / "apps" / "sandbox_erp"), "--port", "8100"]),
    ]
    if not args.no_web:
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if npm is None or not (ROOT / "apps" / "web" / "node_modules").exists():
            raise SystemExit("Install Node and run npm ci --prefix apps/web first, or use --no-web.")
        commands.append(("web", [npm, "--prefix", str(ROOT / "apps" / "web"), "run", "dev"]))
    children = []
    streams = []
    try:
        for name, command in commands:
            stream = (logs / f"{name}.log").open("a", encoding="utf-8")
            streams.append(stream)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            children.append(subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, creationflags=flags))
        print("Gluons Web: http://localhost:3000 | API: http://localhost:8000/docs")
        print("Local email verification messages: .gluons-data/mail | Logs: .gluons-data/logs")
        print("ERP is a labelled simulator. No external provider credentials or cloud resources are used.")
        print("Press Ctrl+C to stop the local services.")
        while all(process.poll() is None for process in children):
            time.sleep(0.5)
        print("A service stopped. Inspect its local log.")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for process in children:
            if process.poll() is None:
                process.terminate()
        for process in children:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        for stream in streams:
            stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
