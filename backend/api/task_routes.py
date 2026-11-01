from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ai.planner import Plan
from api.deps import get_services
from api.schemas import CommandRequest, ConfirmRequest
from services import Services

router = APIRouter(prefix="/api", tags=["tasks"])


@router.post("/command")
async def command(payload: CommandRequest, services: Services = Depends(get_services)) -> dict:
    if not services.rate_limiter.allow("command"):
        raise HTTPException(status_code=429, detail="Too many commands in a short period. Wait a minute.")
    if payload.tool:
        text = payload.text or f"{payload.tool} (UI action)"
        plan = Plan(kind="tool", tool=payload.tool, args=payload.args, planner="ui",
                    intent=payload.tool)
    elif payload.text and payload.text.strip():
        text = payload.text.strip()
        history = [
            {"role": row["role"], "content": row["content"]}
            for row in services.repo.list_messages(8)
        ]
        plan = await services.planner.plan(text, history)
    else:
        raise HTTPException(status_code=400, detail="Provide 'text' or 'tool'.")
    return await services.executor.submit(text, plan, source=payload.source)


@router.get("/tasks")
async def list_tasks(status: str | None = None, limit: int = 40,
                     services: Services = Depends(get_services)) -> dict:
    if status and status not in {
        "pending", "running", "awaiting_confirmation", "completed", "failed", "cancelled"
    }:
        raise HTTPException(status_code=400, detail="Unknown task status.")
    return {"tasks": services.repo.list_tasks(max(1, min(limit, 200)), status)}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, services: Services = Depends(get_services)) -> dict:
    task = services.repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"task": task, "events": services.repo.task_events(task_id, 100)}


@router.post("/tasks/{task_id}/confirm")
async def confirm(task_id: str, payload: ConfirmRequest,
                  services: Services = Depends(get_services)) -> dict:
    result = services.executor.confirm(task_id, payload.token)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: str, services: Services = Depends(get_services)) -> dict:
    task = services.repo.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    cancelled = services.executor.cancel(task_id)
    return {"cancelled": cancelled, "task": services.repo.get_task(task_id)}


@router.get("/messages")
async def messages(limit: int = 60, services: Services = Depends(get_services)) -> dict:
    return {"messages": services.repo.list_messages(max(1, min(limit, 200)))}


@router.delete("/messages")
async def clear_messages(services: Services = Depends(get_services)) -> dict:
    services.repo.clear_messages()
    services.audit.record("user", "conversation.clear", "low", {}, "ok")
    services.bus.publish("messages.cleared", {})
    return {"cleared": True}


@router.post("/notifications/read")
async def mark_read(services: Services = Depends(get_services)) -> dict:
    services.repo.mark_notifications_read()
    return {"ok": True}
