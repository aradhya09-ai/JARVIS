from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_services
from api.schemas import IntegrationWrite, MemoryWrite, SettingsPatch
from database.repository import DEFAULT_SETTINGS
from services import Services

router = APIRouter(prefix="/api", tags=["configuration"])

PROTECTED_PREFIXES = ("security.",)
BOOLEAN_KEYS = {
    "voice.tts_enabled", "voice.wake_word_enabled", "voice.auto_listen",
    "privacy.memory_enabled", "privacy.task_history", "privacy.telemetry",
    "monitor.enabled", "notifications.enabled", "permissions.auto_confirm_moderate",
    "theme.reduced_motion", "startup.auto_start",
}
PATH_KEYS = {"output.directory", "projects.directory"}
RESTART_REQUIRED = {"output.directory", "projects.directory"}


def _validate(key: str, value: Any) -> None:
    if key not in DEFAULT_SETTINGS or key.startswith(PROTECTED_PREFIXES):
        raise HTTPException(status_code=400, detail=f"Setting '{key}' is not editable.")
    if key in BOOLEAN_KEYS and not isinstance(value, bool):
        raise HTTPException(status_code=400, detail=f"{key} must be a boolean.")
    if key == "ai.provider" and value not in {"auto", "ollama", "cloud"}:
        raise HTTPException(status_code=400, detail="ai.provider must be auto, ollama or cloud.")
    if key == "ai.temperature" and not (isinstance(value, (int, float)) and 0 <= value <= 1.5):
        raise HTTPException(status_code=400, detail="ai.temperature must be between 0 and 1.5.")
    if key == "monitor.interval_ms" and not (isinstance(value, int) and 500 <= value <= 10000):
        raise HTTPException(status_code=400, detail="monitor.interval_ms must be 500-10000.")
    if key == "theme.name" and value not in {"blue", "amber"}:
        raise HTTPException(status_code=400, detail="theme.name must be blue or amber.")
    if key == "theme.accent" and not (isinstance(value, str)
                                     and re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
        raise HTTPException(status_code=400, detail="theme.accent must be a #RRGGBB colour.")
    if key == "apps.favorites" and not (isinstance(value, list)
                                       and all(isinstance(v, str) for v in value)):
        raise HTTPException(status_code=400, detail="apps.favorites must be a list of names.")
    if key in PATH_KEYS and not (isinstance(value, str) and value.strip()):
        raise HTTPException(status_code=400, detail=f"{key} must be a non-empty path.")
    if key.startswith("voice.") and not isinstance(value, (str, bool)):
        raise HTTPException(status_code=400, detail=f"{key} has an invalid type.")
    if key == "phone.device_name" and not isinstance(value, str):
        raise HTTPException(status_code=400, detail="phone.device_name must be a string.")


@router.get("/settings")
async def get_settings(services: Services = Depends(get_services)) -> dict:
    return {
        "settings": services.repo.all_settings(),
        "restart_required_keys": sorted(RESTART_REQUIRED),
        "permissions": services.policy.describe_boundaries(),
        "version": services.settings.version,
    }


@router.patch("/settings")
async def patch_settings(payload: SettingsPatch,
                         services: Services = Depends(get_services)) -> dict:
    if not payload.values:
        raise HTTPException(status_code=400, detail="No settings supplied.")
    for key, value in payload.values.items():
        _validate(key, value)
    restart = []
    for key, value in payload.values.items():
        services.repo.set_setting(key, value)
        services.bus.publish("settings.updated", {"key": key, "value": value})
        if key in RESTART_REQUIRED:
            restart.append(key)
    services.audit.record("user", "settings.update", "low",
                          {"keys": sorted(payload.values)}, "ok")
    return {"settings": services.repo.all_settings(), "restart_required": restart}


@router.get("/memory")
async def get_memory(services: Services = Depends(get_services)) -> dict:
    return {
        "memories": services.repo.get_memory() or [],
        "enabled": services.repo.get_setting("privacy.memory_enabled", True),
    }


@router.post("/memory")
async def set_memory(payload: MemoryWrite, services: Services = Depends(get_services)) -> dict:
    if not services.repo.get_setting("privacy.memory_enabled", True):
        raise HTTPException(status_code=409, detail="Memory is disabled in privacy settings.")
    services.repo.set_memory(payload.key, payload.value, payload.kind)
    services.audit.record("user", "memory.set", "low", {"key": payload.key}, "ok")
    return {"ok": True}


@router.delete("/memory/{key}")
async def delete_memory(key: str, services: Services = Depends(get_services)) -> dict:
    services.repo.delete_memory(key)
    services.audit.record("user", "memory.delete", "low", {"key": key}, "ok")
    return {"ok": True}


@router.delete("/memory")
async def clear_memory(services: Services = Depends(get_services)) -> dict:
    services.repo.clear_memory()
    services.audit.record("user", "memory.clear", "moderate", {}, "ok")
    return {"ok": True}


@router.get("/integrations")
async def integrations(services: Services = Depends(get_services)) -> dict:
    return {
        "integrations": services.repo.list_integrations(),
        "note": (
            "Configuration is stored locally, but OAuth flows are NOT IMPLEMENTED. "
            "JARVIS will not read email, calendar, Moodle or GitHub data until those "
            "flows are added and authorised."
        ),
    }


@router.put("/integrations/{provider}")
async def put_integration(provider: str, payload: IntegrationWrite,
                          services: Services = Depends(get_services)) -> dict:
    allowed = {"gmail", "outlook", "google_calendar", "microsoft_calendar", "moodle", "github"}
    if provider not in allowed:
        raise HTTPException(status_code=404, detail="Unknown integration.")
    services.repo.set_integration(provider, payload.config, payload.enabled)
    services.audit.record("user", "integration.config", "moderate",
                          {"provider": provider, "enabled": payload.enabled}, "ok")
    return {
        "ok": True,
        "note": "Saved local configuration only. OAuth authorisation is NOT IMPLEMENTED.",
    }
