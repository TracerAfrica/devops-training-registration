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
from tracer.gateways import Flutterwave, GatewayError, Paystack, Stripe  # noqa: E402
from tracer.store import MemoryStore, SheetStore  # noqa: E402

log = logging.getLogger("tracer")
_store = None

GATEWAYS = {
    "flutterwave": (Flutterwave, lambda: config.FLW_SECRET_KEY, lambda: config.FLW_WEBHOOK_HASH),
    "paystack": (Paystack, lambda: config.PAYSTACK_SECRET_KEY, lambda: config.PAYSTACK_SECRET_KEY),
    "stripe": (Stripe, lambda: config.STRIPE_SECRET_KEY, lambda: config.STRIPE_WEBHOOK_SECRET),
}


def get_store():
    global _store
    if _store is None:
        if os.environ.get("USE_MEMORY_STORE") == "1":
            _store = MemoryStore()
        else:
            _store = SheetStore(config.GOOGLE_SERVICE_ACCOUNT_JSON, config.SHEET_ID, config.SHEET_TAB)
    return _store


def gateway_factory(name: str | None = None):
    """The configured provider for new payments; a row's own provider when re-checking an old one."""
    name = (name or config.PAYMENT_PROVIDER).lower()
    if name not in GATEWAYS:
        raise GatewayError("Payments are not configured yet. Please try again later.")
    cls, key, _ = GATEWAYS[name]
    return cls(key())


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
            out = service.register(body, get_store, gateway_factory)
        elif action == "verify":
            out = service.verify(body.get("reference"), get_store(), gateway_factory)
        else:
            out = {"ok": False, "error": "Unknown request."}
    except (service.UserError, GatewayError) as e:
        out = {"ok": False, "error": str(e)}
    except Exception:
        log.exception("api error")
        out = {"ok": False, "error": "Something went wrong on our side. Please try again in a minute."}
    return JSONResponse(out, headers=headers)


async def webhook(request: Request):
    provider = request.path_params["provider"]
    if provider not in GATEWAYS:
        return JSONResponse({"ok": False}, status_code=404)
    cls, _, secret = GATEWAYS[provider]
    raw = await request.body()
    ref = cls.webhook_reference(raw, request.headers, secret())
    if ref is None:
        return JSONResponse({"ok": False}, status_code=401)
    if ref and service.REF_RE.match(str(ref)):
        try:
            service.verify(ref, get_store(), gateway_factory)  # always re-checks with the provider's API
        except service.UserError:
            pass  # not a payment from this form
        except Exception:
            log.exception("webhook verify failed for %s", ref)
            return JSONResponse({"ok": False}, status_code=500)  # provider retries
    return JSONResponse({"ok": True})


async def cron_reconcile(request: Request):
    if not config.CRON_SECRET or request.headers.get("authorization") != f"Bearer {config.CRON_SECRET}":
        return JSONResponse({"ok": False}, status_code=401)
    return JSONResponse(service.reconcile(get_store(), gateway_factory))


async def health(request: Request):
    _, key, hook = GATEWAYS.get(config.PAYMENT_PROVIDER, (None, lambda: "", lambda: ""))
    return JSONResponse({"ok": True, "service": "tracer-africa-registration", "provider": config.PAYMENT_PROVIDER,
                         "payments": bool(key()), "webhook": bool(hook()),
                         "sheet": bool(config.SHEET_ID and config.GOOGLE_SERVICE_ACCOUNT_JSON)})


app = Starlette(routes=[
    Route("/api", api, methods=["POST", "OPTIONS"]),
    Route("/api/", api, methods=["POST", "OPTIONS"]),
    Route("/api/{provider}/webhook", webhook, methods=["POST"]),
    Route("/api/cron/reconcile", cron_reconcile, methods=["GET"]),
    Route("/api/health", health, methods=["GET"]),
])
