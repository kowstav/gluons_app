"""SIMULATOR. A small independent order system exposing a subset of the Frappe REST API.

This is not ERPNext. It exists so a clean checkout can exercise a real external write
and an independent readback across a process boundary with its own datastore. Every
response carries `X-Gluons-Simulator: true`, and connections to it are recorded with
environment `simulator`, so nothing it does can be presented as a production result.

`/__sim/*` injects the failures the runtime must survive: a 503, a dropped response
after the write landed, and a silently altered write.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

DB_PATH = Path(os.environ.get("SANDBOX_ERP_DB", ".gluons-data/sandbox_erp.sqlite3"))
API_KEY = os.environ.get("SANDBOX_ERP_API_KEY", "sandbox-key")
API_SECRET = os.environ.get("SANDBOX_ERP_API_SECRET", "sandbox-secret")

app = FastAPI(title="Gluons Sandbox ERP (simulator)")
_lock = threading.Lock()
_faults: dict[str, Any] = {}


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "create table if not exists sales_orders (name text primary key, po_no text, doc text not null, created integer)"
    )
    return connection


@app.middleware("http")
async def label(request: Request, call_next: Any) -> Response:
    response = await call_next(request)
    response.headers["X-Gluons-Simulator"] = "true"
    return response


def require_token(authorization: str | None = Header(default=None)) -> None:
    if authorization != f"token {API_KEY}:{API_SECRET}":
        raise HTTPException(status_code=401, detail="invalid token")


def _take_fault(name: str) -> Any:
    with _lock:
        return _faults.pop(name, None)


@app.get("/api/method/frappe.auth.get_logged_user", dependencies=[Depends(require_token)])
def logged_user() -> dict[str, Any]:
    return {"message": "sandbox@gluons.example", "simulator": True}


@app.post("/api/resource/Sales%20Order", dependencies=[Depends(require_token)])
@app.post("/api/resource/Sales Order", dependencies=[Depends(require_token)])
def create_order(document: dict[str, Any]) -> Response:
    status = _take_fault("fail_next")
    if status:
        return JSONResponse({"exc": "injected failure"}, status_code=status)
    alter = _take_fault("alter_next")
    drop = _take_fault("drop_response_next")
    name = f"SAL-ORD-{uuid.uuid4().hex[:8].upper()}"
    items = document.get("items", [])
    total = sum(float(i["qty"]) * float(i.get("rate", 0)) for i in items)
    stored = {**document, "name": name, "docstatus": 0, "grand_total": total}
    if alter:
        stored["items"] = [{**items[0], "qty": float(items[0]["qty"]) + 1}, *items[1:]]
    with _lock, _db() as connection:
        connection.execute(
            "insert into sales_orders (name, po_no, doc, created) values (?, ?, ?, strftime('%s','now'))",
            (name, document.get("po_no"), json.dumps(stored)),
        )
    if drop:
        # The write landed, but the caller never learns that.
        return JSONResponse({"exc": "gateway timeout"}, status_code=504)
    return JSONResponse({"data": stored})


@app.get("/api/resource/Sales%20Order", dependencies=[Depends(require_token)])
@app.get("/api/resource/Sales Order", dependencies=[Depends(require_token)])
def list_orders(filters: str | None = None) -> dict[str, Any]:
    status = _take_fault("fail_read_next")
    if status:
        raise HTTPException(status_code=status, detail="injected failure")
    po_no = None
    for field, _op, value in json.loads(filters or "[]"):
        if field == "po_no":
            po_no = value
    with _db() as connection:
        rows = connection.execute("select name, po_no from sales_orders where (? is null or po_no = ?)", (po_no, po_no))
        return {"data": [{"name": r["name"], "po_no": r["po_no"]} for r in rows]}


@app.get("/api/resource/Sales%20Order/{name}", dependencies=[Depends(require_token)])
@app.get("/api/resource/Sales Order/{name}", dependencies=[Depends(require_token)])
def get_order(name: str) -> dict[str, Any]:
    with _db() as connection:
        row = connection.execute("select doc from sales_orders where name = ?", (name,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return {"data": json.loads(row["doc"])}


@app.post("/__sim/fault")
def set_fault(fault: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        _faults.update(fault)
        return {"faults": dict(_faults)}


@app.get("/__sim/orders")
def all_orders() -> dict[str, Any]:
    with _db() as connection:
        rows = connection.execute("select doc from sales_orders").fetchall()
    return {"count": len(rows), "orders": [json.loads(r["doc"]) for r in rows]}


@app.post("/__sim/reset")
def reset() -> dict[str, str]:
    with _lock, _db() as connection:
        connection.execute("delete from sales_orders")
        _faults.clear()
    return {"status": "reset"}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "simulator": True}
