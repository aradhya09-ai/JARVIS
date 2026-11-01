from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_services
from api.schemas import PhoneNotifyRequest
from services import Services

router = APIRouter(prefix="/api/phone", tags=["phone"])


@router.get("/status")
async def status(services: Services = Depends(get_services)) -> dict:
    return services.bridge.status()


@router.post("/pair/start")
async def pair_start(services: Services = Depends(get_services)) -> dict:
    payload = services.bridge.pair_start()
    services.audit.record("user", "phone.pair.start", "moderate",
                          {"desktop_id": payload["desktop_id"]}, "ok")
    return payload


@router.get("/devices")
async def devices(services: Services = Depends(get_services)) -> dict:
    return {"devices": services.repo.list_devices()}


@router.post("/devices/{device_id}/revoke")
async def revoke(device_id: str, services: Services = Depends(get_services)) -> dict:
    services.repo.revoke_device(device_id)
    disconnected = await services.bridge.disconnect(device_id)
    services.audit.record("user", "phone.revoke", "high", {"device_id": device_id}, "ok")
    return {"revoked": True, "disconnected": disconnected}


@router.post("/notify")
async def notify(payload: PhoneNotifyRequest, services: Services = Depends(get_services)) -> dict:
    delivered = await services.bridge.push_event(
        "notification", {"title": payload.title, "body": payload.body}
    )
    return {"delivered": delivered, "connected": services.bridge.status()["connected"]}
