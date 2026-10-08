"""Vercel entry point: one ASGI app serving every /api/* route."""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

from tracer import config, service  # noqa: E402
from tracer.paystack import Paystack, PaystackError, valid_signature  # noqa: E402
from tracer.store import MemoryStore, SheetStore  # noqa: E402

log = logging.getLogger("tracer")
_store = None


def get_store():
    global _store
    if _store is None:
        if os.environ.get("USE_MEMORY_STORE") == "1":
            _store = MemoryStore()
        else:
            _store = SheetStore(config.GOOGLE_SERVICE_ACCOUNT_JSON, config.SHEET_ID, config.SHEET_TAB)
    return _store


def paystack_factory():
    return Paystack(config.PAYSTACK_SECRET_KEY)


def _cors(request: Request) -> dict:
    origin = request.headers.get("origin", "")
    if origin in config.ALLOWED_ORIGINS:
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400"}
    return {}


async def api(request: Request):
    headers = _cors(request)
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=headers)
    try:
        body = json.loads(await request.body() or b"{}")
        action = body.get("action")
        if action == "settings":
            out = service.settings()
        elif action == "register":
            out = service.register(body, get_store(), paystack_factory)
        elif action == "verify":
            out = service.verify(body.get("reference"), get_store(), paystack_factory)
        else:
            out = {"ok": False, "error": "Unknown request."}
    except (service.UserError, PaystackError) as e:
        out = {"ok": False, "error": str(e)}
    except Exception:
        log.exception("api error")
        out = {"ok": False, "error": "Something went wrong on our side. Please try again in a minute."}
    return JSONResponse(out, headers=headers)


async def paystack_webhook(request: Request):
    raw = await request.body()
    if not valid_signature(raw, request.headers.get("x-paystack-signature", ""), config.PAYSTACK_SECRET_KEY):
        return JSONResponse({"ok": False}, status_code=401)
    event = json.loads(raw or b"{}")
    if event.get("event") == "charge.success":
        ref = (event.get("data") or {}).get("reference", "")
        if service.REF_RE.match(str(ref)):
            try:
                service.verify(ref, get_store(), paystack_factory)  # re-checks with Paystack; never trusts the body alone
            except service.UserError:
                pass  # payment not from this form
            except Exception:
                log.exception("webhook verify failed for %s", ref)
                return JSONResponse({"ok": False}, status_code=500)  # Paystack retries
    return JSONResponse({"ok": True})


async def cron_reconcile(request: Request):
    if not config.CRON_SECRET or request.headers.get("authorization") != f"Bearer {config.CRON_SECRET}":
        return JSONResponse({"ok": False}, status_code=401)
    return JSONResponse(service.reconcile(get_store(), paystack_factory))


async def health(request: Request):
    return JSONResponse({"ok": True, "service": "tracer-africa-registration",
                         "paystack": bool(config.PAYSTACK_SECRET_KEY),
                         "sheet": bool(config.SHEET_ID and config.GOOGLE_SERVICE_ACCOUNT_JSON)})


app = Starlette(routes=[
    Route("/api", api, methods=["POST", "OPTIONS"]),
    Route("/api/", api, methods=["POST", "OPTIONS"]),
    Route("/api/paystack/webhook", paystack_webhook, methods=["POST"]),
    Route("/api/cron/reconcile", cron_reconcile, methods=["GET"]),
    Route("/api/health", health, methods=["GET"]),
])
