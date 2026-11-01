"""JARVIS desktop backend entry point.

Run from the ``backend`` directory:
    .\\.venv\\Scripts\\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8765

The REST API accepts loopback clients only and every mutating request must
carry ``X-JARVIS-Client: desktop``. The phone companion connects to
``/ws/phone`` with a paired device token instead.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ai.model_router import ModelRouter
from ai.planner import Planner
from api import (
    personal_routes,
    phone_routes,
    settings_routes,
    system_routes,
    task_routes,
    voice_routes,
)
from config import REPO_DIR, settings
from database.db import Database
from database.repository import Repository
from events import EventBus
from executor import TaskExecutor
from phone.device_bridge import PhoneBridge
from security.audit_log import AuditLog
from security.authentication import PairingCodes, RateLimiter, SecurityService
from security.permissions import PermissionPolicy
from scheduler import ReminderScheduler
from services import Services
from tools import build_registry
from voice.speech_to_text import SpeechToText
from voice.text_to_speech import TextToSpeech

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("jarvis")

CLIENT_HEADER = "x-jarvis-client"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:8765",
    "http://127.0.0.1:8765",
]
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(host: str | None) -> bool:
    return bool(host) and (host in LOOPBACK_HOSTS or host.startswith("127."))


def _origin_ok(request: Request | WebSocket) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return any(origin.rstrip("/") == allowed for allowed in ALLOWED_ORIGINS)


def _build_services() -> Services:
    settings.ensure_dirs()
    database = Database(settings.db_path)
    database.initialize()
    repo = Repository(database)
    repo.seed()
    if repo.recover_stale_tasks():
        logger.info("Marked interrupted tasks as failed after restart")
    repo.prune()

    bus = EventBus()
    security = SecurityService(repo)
    pairing = PairingCodes(ttl_seconds=120)
    audit = AuditLog(repo, settings.audit_log_path)
    policy = PermissionPolicy(repo, settings.read_roots, settings.write_roots)
    registry = build_registry(settings.task_timeout_seconds)
    router = ModelRouter(settings, repo)
    bridge = PhoneBridge(settings, repo, bus, security, pairing, audit)
    stt = SpeechToText(settings)
    tts = TextToSpeech()
    executor = TaskExecutor(settings, repo, registry, bus, policy, audit, bridge, router)
    planner = Planner(settings, repo, registry, router)
    reminders = ReminderScheduler(repo, bus)

    async def desktop_command(action: str, args: dict[str, Any], device_id: str) -> dict[str, Any]:
        text = str(args.get("text", "")).strip()
        if not text:
            return {"ok": False, "message": "No command text supplied.", "data": {}}
        plan = await planner.plan(text)
        result = await executor.submit(text, plan, source="phone")
        audit.record("phone", "phone.command", "low",
                     {"device_id": device_id, "text": text}, "ok")
        if result.get("requires_confirmation"):
            return {
                "ok": False,
                "message": "This action needs confirmation on the desktop.",
                "data": {"task": result.get("task")},
            }
        task = result.get("task")
        return {
            "ok": True,
            "message": result.get("reply", "Command received."),
            "data": {
                "task_id": (task or {}).get("id"),
                "status": (task or {}).get("status"),
                "reply": result.get("reply"),
            },
        }

    bridge.command_handler = desktop_command
    dist = REPO_DIR / "desktop" / "dist"
    return Services(
        settings=settings,
        repo=repo,
        bus=bus,
        registry=registry,
        policy=policy,
        audit=audit,
        security=security,
        pairing=pairing,
        rate_limiter=RateLimiter(limit=30, window_seconds=60),
        router=router,
        planner=planner,
        executor=executor,
        bridge=bridge,
        stt=stt,
        tts=tts,
        reminders=reminders,
        started_at=time.time(),
        serve_dir=dist if dist.is_dir() else None,
    )


async def _keep_model_warm(services: Services) -> None:
    """Re-page the model while a conversation is live, then let it leave memory.

    A cold qwen2.5:3b costs 80-150s on this hardware — no GPU offload and barely
    2 GB of RAM free — which reads as JARVIS ignoring the user. Refreshing it
    every few minutes, but only while the last reply is recent, keeps the next
    command fast without holding 2 GB overnight.

    The ping repeats the planner's own prompt rather than a bare "ok": an
    unrelated prompt keeps the weights resident but wipes the ~3,600-token tool
    catalog the model had already read, which is the expensive part.
    """
    settings = services.settings
    while True:
        await asyncio.sleep(180)
        last = services.router.last_request_at
        if not last:
            continue
        if time.time() - last < settings.model_keep_warm_seconds:
            await services.router.warm(services.planner.warm_messages())


@asynccontextmanager
async def lifespan(app: FastAPI):
    services = _build_services()
    services.bus.bind_loop()
    app.state.services = services
    services.audit.record("system", "server.start", "low",
                          {"version": services.settings.version,
                           "host": services.settings.host,
                           "port": services.settings.port}, "ok")
    services.repo.add_notification(
        "system",
        "JARVIS online",
        f"Backend {services.settings.version} is running on "
        f"{services.settings.host}:{services.settings.port}.",
    )
    logger.info("JARVIS backend ready on %s:%s", services.settings.host, services.settings.port)
    # Do not block startup on paging the model in.
    warmup = asyncio.create_task(services.router.warm(services.planner.warm_messages()))
    keeper = asyncio.create_task(_keep_model_warm(services))
    services.reminders.start()
    try:
        yield
    finally:
        warmup.cancel()
        keeper.cancel()
        await services.reminders.stop()
        await services.executor.shutdown()
        await services.bridge.close_all()
        logger.info("JARVIS backend stopped")


app = FastAPI(
    title="JARVIS",
    version=settings.version,
    description="Local-first personal AI assistant backend.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-JARVIS-Client"],
    expose_headers=["X-Jarvis-TTS-Info"],
)


@app.middleware("http")
async def local_guard(request: Request, call_next):
    client = request.client.host if request.client else ""
    if request.url.path.startswith("/api"):
        if not _is_loopback(client):
            return JSONResponse(
                {"detail": "The JARVIS REST API is local-only. Use the phone bridge from the app."},
                status_code=403,
            )
        if request.method in MUTATING_METHODS and request.headers.get(CLIENT_HEADER) != "desktop":
            return JSONResponse(
                {"detail": "Missing X-JARVIS-Client: desktop header."},
                status_code=403,
            )
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(system_routes.router)
app.include_router(task_routes.router)
app.include_router(settings_routes.router)
app.include_router(personal_routes.router)
app.include_router(phone_routes.router)
app.include_router(voice_routes.router)


async def _snapshot(services: Services) -> dict[str, Any]:
    providers = await services.router.status()
    active = await services.router.active()
    database_ok = await asyncio.to_thread(services.repo.db.healthy)
    health = {
        "status": "ok" if database_ok else "degraded",
        "version": services.settings.version,
        "uptime_seconds": round(time.time() - services.started_at),
        "database": {"ok": database_ok, "schema": services.repo.db.schema_version()},
        "providers": [
            {"name": p.name, "kind": p.kind, "available": p.available,
             "detail": p.detail, "model": p.model}
            for p in providers
        ],
        "active_provider": active.name if active else None,
        "tools": len(services.registry.all()),
        "voice": {
            "backend_stt": services.stt.availability(),
            "backend_tts": services.tts.availability(),
        },
        "phone": services.bridge.status(),
        "bound_host": services.settings.host,
        "permissions": services.policy.describe_boundaries(),
    }
    return {
        "type": "snapshot",
        "payload": {
            "health": health,
            "settings": services.repo.all_settings(),
            "tasks": services.repo.list_tasks(40),
            "messages": services.repo.list_messages(60),
            "notifications": services.repo.list_notifications(40),
            "phone": services.bridge.status(),
        },
    }


@app.websocket("/ws")
async def ui_socket(websocket: WebSocket) -> None:
    services: Services = websocket.app.state.services
    client = websocket.client.host if websocket.client else ""
    if not _is_loopback(client) or not _origin_ok(websocket):
        await websocket.close(code=1008, reason="Local desktop clients only")
        return
    await websocket.accept()
    queue = services.bus.subscribe()
    try:
        await websocket.send_json(await _snapshot(services))
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - a UI socket error must not affect the server
        logger.debug("UI socket closed", exc_info=True)
    finally:
        services.bus.unsubscribe(queue)
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


@app.websocket("/ws/phone")
async def phone_socket(websocket: WebSocket) -> None:
    services: Services = websocket.app.state.services
    client = websocket.client.host if websocket.client else ""
    if not services.rate_limiter.allow(f"phone-handshake:{client}"):
        await websocket.close(code=1008, reason="Too many attempts")
        return
    await services.bridge.handle_socket(websocket)


if settings.host not in LOOPBACK_HOSTS:
    logger.warning(
        "JARVIS_HOST=%s binds beyond loopback. The REST API rejects non-local clients; "
        "only the authenticated phone bridge is exposed.",
        settings.host,
    )

if (REPO_DIR / "desktop" / "dist").is_dir():
    app.mount("/", StaticFiles(directory=REPO_DIR / "desktop" / "dist", html=True), name="ui")
else:
    @app.get("/")
    async def index() -> dict[str, Any]:
        return {
            "name": "JARVIS",
            "version": settings.version,
            "ui": "The dashboard build was not found. Run 'npm run dev' in desktop/ "
                  "or 'npm run build' to serve it from this backend.",
            "health": "/api/health",
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
