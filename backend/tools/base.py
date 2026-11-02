"""Tool abstraction: typed arguments, risk levels, registry and execution."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from config import Settings
from database.repository import Repository
from events import EventBus
from security.audit_log import AuditLog
from security.permissions import PermissionError_, PermissionPolicy, Risk

logger = logging.getLogger("jarvis.tools")


class ToolError(Exception):
    """A predictable tool failure with a user-facing message."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class ToolValidationError(ToolError):
    pass


@dataclass
class ToolResult:
    ok: bool
    summary: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def success(cls, summary: str, **data: Any) -> "ToolResult":
        return cls(ok=True, summary=summary, data=data)

    @classmethod
    def failure(cls, summary: str, **data: Any) -> "ToolResult":
        return cls(ok=False, summary=summary, error=summary, data=data)


@dataclass
class ToolContext:
    settings: Settings
    repo: Repository
    policy: PermissionPolicy
    audit: AuditLog
    bus: EventBus
    bridge: Any = None
    router: Any = None
    executor: Any = None
    task_id: str = ""
    actor: str = "user"

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.bus.publish(event_type, {"task_id": self.task_id, **payload})

    def note(self, message: str, level: str = "info") -> None:
        if self.task_id:
            self.repo.add_task_event(self.task_id, level, message)


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[ToolResult]]
# Decides approval from the arguments themselves, because one tool can hold two
# different risks: "forget my favourite colour" and "forget everything".
ConfirmWhen = Callable[[BaseModel, Any], bool]


@dataclass
class ToolDefinition:
    name: str
    description: str
    args_model: type[BaseModel]
    risk: Risk
    handler: ToolHandler
    confirm: bool | None = None
    confirm_when: ConfirmWhen | None = None
    category: str = "general"

    def schema(self) -> dict[str, Any]:
        return self.args_model.model_json_schema()

    def catalog_entry(self) -> dict[str, Any]:
        schema = self.schema()
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk.value,
            "category": self.category,
            "args": schema.get("properties", {}),
            "required": schema.get("required", []),
        }


class ToolRegistry:
    def __init__(self, timeout_seconds: float = 120.0):
        self.timeout_seconds = timeout_seconds
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(
                f"Unknown tool '{name}'.",
                hint="JARVIS only runs tools that are registered and allowlisted.",
            )
        return tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def catalog(self) -> list[dict[str, Any]]:
        return [tool.catalog_entry() for tool in self._tools.values()]

    def validate(self, name: str, args: dict[str, Any]) -> BaseModel:
        tool = self.get(name)
        try:
            return tool.args_model.model_validate(args or {})
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in exc.errors()
            )
            raise ToolValidationError(
                f"Invalid arguments for {name}: {problems}",
                hint="JARVIS rejected the tool call before executing it.",
            ) from exc

    def requires_confirmation(
        self,
        name: str,
        policy: PermissionPolicy,
        args: BaseModel | None = None,
        settings: Any = None,
    ) -> bool:
        tool = self.get(name)
        if tool.confirm_when is not None and args is not None and settings is not None:
            return bool(tool.confirm_when(args, settings))
        return policy.requires_confirmation(tool.risk, tool.confirm)

    async def execute(
        self, name: str, args: BaseModel, ctx: ToolContext, timeout: float | None = None
    ) -> ToolResult:
        tool = self.get(name)
        limit = timeout or self.timeout_seconds
        try:
            return await asyncio.wait_for(tool.handler(args, ctx), timeout=limit)
        except asyncio.TimeoutError:
            logger.warning("Tool %s timed out after %.0fs", name, limit)
            return ToolResult.failure(
                f"{name} timed out after {limit:.0f} seconds and was stopped."
            )
        except asyncio.CancelledError:
            raise
        except PermissionError_ as exc:
            return ToolResult.failure(str(exc))
        except ToolError as exc:
            message = str(exc)
            if exc.hint:
                message = f"{message} {exc.hint}"
            return ToolResult.failure(message)
        except Exception:  # noqa: BLE001 - report, never crash the executor
            logger.exception("Unexpected failure in tool %s", name)
            return ToolResult.failure(
                f"{name} failed unexpectedly. Check backend logs for details."
            )
