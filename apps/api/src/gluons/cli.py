"""Operator commands. `gluons --help` lists them."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import json
import getpass

from gluons.config import REPO_ROOT


def _migrate(_: argparse.Namespace) -> int:
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=REPO_ROOT / "apps" / "api", env=os.environ, check=False
    ).returncode


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("gluons.api.app:app", host=args.host, port=args.port, access_log=False)
    return 0


def _worker(args: argparse.Namespace) -> int:
    from gluons.runtime.worker import drain, main, scheduler_tick

    if args.once:
        scheduler_tick()
        drain(max_rounds=20)
    else:
        main()
    return 0


def _skills(_: argparse.Namespace) -> int:
    from gluons.skills.catalog import sync_versions

    count = sync_versions()
    print(f"validated and pinned {count} skills")
    return 0


def _operator_principal(args: argparse.Namespace):
    """Authenticate in a local terminal; passwords/tokens are never command arguments."""
    from gluons import identity
    issued = identity.login(args.email, getpass.getpass("Gluons password: "), ip="local_operator", user_agent="gluons-cli")
    if issued.mfa_required:
        identity.verify_mfa(issued.token, getpass.getpass("Authenticator or recovery code: "))
    view = identity.resolve_session(issued.token)
    principal = identity.principal_for_user(view.user_id, args.tenant_id, view.session_id)
    return principal, issued.token


def _bind_phone(args: argparse.Namespace) -> int:
    from gluons import identity
    from gluons.telephony import service
    principal, token = _operator_principal(args)
    try:
        principal.require("phone:manage")
        result = service.configure_operator_route(tenant_id=principal.tenant_id, route_id=args.route_id, number_id=args.number_id, agent_id=args.agent_id)
        from gluons.db.models import PhoneRoute
        from gluons.db.session import tenant_tx
        with tenant_tx(principal.tenant_id, principal.user_id) as session:
            row = session.get(PhoneRoute, args.route_id)
            secret = service.route_secret(row)
        target = REPO_ROOT / ".gluons-data" / "voice" / f"{result['id']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"route_id": result["id"], "header_name": "X-Gluons-Voice-Secret", "header_value": secret}), encoding="utf-8")
        print(f"Provider binding verified. Store the header from {target} in the ElevenLabs tool secret settings. Do not copy it into chat.")
        return 0
    finally:
        identity.logout(token)


def _bind_whatsapp(args: argparse.Namespace) -> int:
    from gluons import identity
    from gluons.whatsapp.service import bind_from_environment
    principal, token = _operator_principal(args)
    try:
        result = bind_from_environment(principal, args.session_id, account_id=args.account_id, phone_number_id=args.phone_id)
        print(f"Provider account verification result: {result.get('status', 'verified')}. Confirm the displayed account in Gluons Web.")
        return 0
    finally:
        identity.logout(token)


def main() -> int:
    parser = argparse.ArgumentParser(prog="gluons")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply database migrations").set_defaults(fn=_migrate)
    serve = commands.add_parser("serve", help="run the API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(fn=_serve)
    worker = commands.add_parser("worker", help="run the background worker")
    worker.add_argument("--once", action="store_true", help="schedule and drain due work, then exit (Cloud Run job)")
    worker.set_defaults(fn=_worker)
    commands.add_parser("skills-sync", help="validate contracts and pin them").set_defaults(fn=_skills)
    phone = commands.add_parser("phone-bind", help="authenticate locally and bind an existing authorized provider number; never purchases/calls")
    for name in ("email", "tenant-id", "route-id", "number-id", "agent-id"):
        phone.add_argument(f"--{name}", required=True)
    phone.set_defaults(fn=_bind_phone)
    whatsapp = commands.add_parser("whatsapp-bind", help="authenticate locally and verify an approved business token from the environment")
    for name in ("email", "tenant-id", "session-id", "account-id", "phone-id"):
        whatsapp.add_argument(f"--{name}", required=True)
    whatsapp.set_defaults(fn=_bind_whatsapp)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
