from __future__ import annotations

import asyncio
import platform
import time

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_services
from services import Services
from tools.application_launcher import list_windows, resolve_spec, running_processes
from tools.system_monitor import collect_system_stats, hardware_info, top_processes

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health(services: Services = Depends(get_services)) -> dict:
    providers = await services.router.status()
    active = await services.router.active()
    database_ok = await asyncio.to_thread(services.repo.db.healthy)
    return {
        "status": "ok" if database_ok else "degraded",
        "version": services.settings.version,
        "uptime_seconds": round(time.time() - services.started_at),
        "database": {"ok": database_ok, "schema": services.repo.db.schema_version()},
        "providers": [
            {
                "name": p.name, "kind": p.kind, "available": p.available,
                "detail": p.detail, "model": p.model,
            }
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


@router.get("/system/stats")
async def system_stats(processes: bool = False, services: Services = Depends(get_services)) -> dict:
    return await asyncio.to_thread(collect_system_stats, processes)


@router.get("/system/hardware")
async def system_hardware(services: Services = Depends(get_services)) -> dict:
    info = await asyncio.to_thread(hardware_info)
    return {**info, "system": platform.system(), "release": platform.release()}


@router.get("/system/processes")
async def system_processes(limit: int = 25, services: Services = Depends(get_services)) -> dict:
    limit = max(1, min(limit, 100))
    return {
        "processes": await asyncio.to_thread(top_processes, limit),
        "windows": await asyncio.to_thread(list_windows, 100),
    }


@router.get("/apps")
async def apps(services: Services = Depends(get_services)) -> dict:
    favorites = services.repo.get_setting("apps.favorites", []) or []
    items = []
    for spec in services.repo.list_apps():
        resolved = resolve_spec(spec)
        items.append(
            {
                "name": spec["name"],
                "aliases": spec.get("aliases", []),
                "kind": spec.get("kind", "exe"),
                "target": str(resolved) if resolved else spec.get("uri", ""),
                "available": bool(resolved) or spec.get("kind") == "uri",
                "builtin": spec.get("builtin", False),
                "favorite": spec["name"] in favorites,
            }
        )
    return {"apps": items, "favorites": favorites}


@router.get("/apps/running")
async def apps_running(services: Services = Depends(get_services)) -> dict:
    windows, processes = await asyncio.gather(
        asyncio.to_thread(list_windows, 100),
        asyncio.to_thread(running_processes, 100),
    )
    return {"windows": windows, "processes": processes}


@router.get("/providers")
async def providers(services: Services = Depends(get_services)) -> dict:
    statuses = await services.router.status()
    active = await services.router.active()
    return {
        "providers": [
            {"name": p.name, "kind": p.kind, "available": p.available,
             "detail": p.detail, "model": p.model}
            for p in statuses
        ],
        "active": active.name if active else None,
        "image": services.settings.image_provider,
    }


@router.get("/tools")
async def tools(services: Services = Depends(get_services)) -> dict:
    return {"tools": services.registry.catalog()}


@router.get("/permissions")
async def permissions(services: Services = Depends(get_services)) -> dict:
    return services.policy.describe_boundaries()


@router.get("/audit")
async def audit(limit: int = 100, services: Services = Depends(get_services)) -> dict:
    return {"entries": services.repo.list_audit(max(1, min(limit, 500)))}


@router.get("/notifications")
async def notifications(limit: int = 40, services: Services = Depends(get_services)) -> dict:
    return {"notifications": services.repo.list_notifications(max(1, min(limit, 200)))}


@router.get("/apps/check")
async def app_check(name: str, services: Services = Depends(get_services)) -> dict:
    from tools.application_launcher import find_shortcut

    spec = find_shortcut(services.repo, name)
    if spec is None:
        raise HTTPException(status_code=404, detail="Application is not configured.")
    resolved = resolve_spec(spec)
    return {"name": spec["name"], "available": bool(resolved) or spec.get("kind") == "uri",
            "target": str(resolved) if resolved else spec.get("uri", "")}
