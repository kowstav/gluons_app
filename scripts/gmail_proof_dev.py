"""Start an isolated local Gmail proof environment; never submit an event or send mail."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_WORKSPACE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def workspace_paths(name: str, *, resume: bool) -> tuple[Path, Path]:
    if not _WORKSPACE.fullmatch(name):
        raise ValueError("Workspace name must use lowercase letters, numbers, or hyphens")
    data = ROOT / ".pgdata" / f"gmail-proof-{name}"
    private = ROOT / ".gluons-data" / f"gmail-proof-{name}"
    if (data.is_symlink() or private.is_symlink()
            or not data.resolve().is_relative_to(ROOT.resolve()) or not private.resolve().is_relative_to(ROOT.resolve())):
        raise ValueError("Proof workspace must stay inside this checkout")
    if (resume and not (data.is_dir() and private.is_dir())) or (not resume and (data.exists() or private.exists())):
        raise ValueError("Use a new workspace name, or --resume for an existing proof workspace")
    return data, private


def check_local_keys(settings: object) -> None:
    from gluons.config import DEV_SIGNING_KEY_B64, DEV_VAULT_KEK_B64

    if (settings.hosted or settings.vault_kek_b64 == DEV_VAULT_KEK_B64  # type: ignore[attr-defined]
            or settings.signing_key_b64 == DEV_SIGNING_KEY_B64  # type: ignore[attr-defined]
            or settings.vault_kek_b64 == settings.signing_key_b64):  # type: ignore[attr-defined]
        raise ValueError("Use separate non-default local proof keys, never hosted or production keys")
    try:
        settings.key_bytes(settings.vault_kek_b64)  # type: ignore[attr-defined]
        settings.key_bytes(settings.signing_key_b64)  # type: ignore[attr-defined]
    except (ValueError, TypeError) as exc:
        raise ValueError("Local proof keys must each be base64-encoded 32-byte keys") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--web", action="store_true", help="Start the owner web app on localhost:13700")
    parser.add_argument("--worker", action="store_true", help="Start the isolated worker; previously approved jobs may resume")
    args = parser.parse_args()
    os.chdir(ROOT)
    from gluons.config import Settings

    try:
        data, private = workspace_paths(args.workspace, resume=args.resume)
        settings = Settings()
        check_local_keys(settings)
    except ValueError:
        raise SystemExit("Invalid workspace or local proof key configuration.") from None
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm") if args.web else None
    if args.web and (npm is None or not (ROOT / "apps" / "web" / "node_modules").exists()):
        raise SystemExit("Install the locked web dependencies before using --web.")

    from gluons import devdb

    private.mkdir(parents=True, exist_ok=args.resume)
    db = devdb.start(data, database="gluons_gmail_proof")
    devdb.migrate(db)
    assets = ROOT / "apps" / "api" / "tests" / "provider_proof_assets"
    # Do not pass unrelated provider keys or cloud settings into the proof processes.
    system_keys = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "USERPROFILE",
                   "HOME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
                   "NUMBER_OF_PROCESSORS", "OS", "PROCESSOR_ARCHITECTURE", "LANG"}
    base_env = {key: value for key, value in os.environ.items() if key.upper() in system_keys}
    env = {
        **base_env, **db.environment(), "GLUONS_ENV": "local", "GLUONS_GMAIL_PROOF_ENABLED": "true",
        "GLUONS_VAULT_KEK_B64": settings.vault_kek_b64, "GLUONS_SIGNING_KEY_B64": settings.signing_key_b64,
        "GLUONS_GOOGLE_CLIENT_ID": settings.google_client_id or "", "GLUONS_GOOGLE_CLIENT_SECRET": settings.google_client_secret or "",
        "GLUONS_SKILLS_DIR": str(assets / "skills"), "GLUONS_BLUEPRINT_DIR": str(assets / "blueprint"),
        "GLUONS_PUBLIC_API_URL": "http://localhost:18700", "GLUONS_PUBLIC_WEB_URL": "http://localhost:13700",
        "GLUONS_ALLOWED_WEB_ORIGINS": "http://localhost:13700", "GLUONS_MAIL_DIR": str(private / "mail"),
        "GLUONS_MAIL_TRANSPORT": "file", "GLUONS_QUEUE_BACKEND": "postgres", "PYTHONUTF8": "1",
    }
    # The private working directory prevents these children from loading the checkout's .env.
    subprocess.run([sys.executable, "-m", "gluons.cli", "skills-sync"], cwd=private, env=env, check=True)
    commands = [
        ("api", [sys.executable, "-m", "gluons.cli", "serve", "--host", "127.0.0.1", "--port", "18700"]),
    ]
    if args.worker:
        commands.append(("worker", [sys.executable, "-m", "gluons.cli", "worker"]))
    if npm:
        commands.append(("web", [npm, "--prefix", str(ROOT / "apps" / "web"), "run", "dev", "--", "--port", "13700"]))
    # The web build receives only its public API origin, never the API's private keys.
    web_env = dict(base_env)
    web_env.update({"NEXT_PUBLIC_API_URL": "http://localhost:18700", "NEXT_TELEMETRY_DISABLED": "1"})
    children: list[subprocess.Popen] = []
    streams = []
    try:
        for name, command in commands:
            stream = (private / f"{name}.log").open("a", encoding="utf-8")
            streams.append(stream)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            children.append(subprocess.Popen(command, cwd=private, env=web_env if name == "web" else env,
                                             stdout=stream, stderr=subprocess.STDOUT,
                                             creationflags=flags))
        print("Isolated proof API: http://localhost:18700; owner web: http://localhost:13700" if npm
              else "Isolated proof API: http://localhost:18700 (owner web not started)")
        print("No event or email was submitted. The worker is " + ("enabled" if args.worker else "disabled") + ".")
        print("Stop with Ctrl+C; the proof workspace is retained.")
        while all(process.poll() is None for process in children):
            time.sleep(0.5)
        print("A proof service stopped; inspect its private local log.")
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
