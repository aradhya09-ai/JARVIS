"""Phone actions proxied to the paired Android companion over its authenticated socket."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from security.permissions import Risk
from tools.base import ToolContext, ToolDefinition, ToolError, ToolResult


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenAppArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app: str = Field(min_length=1, max_length=80)


class AlarmArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time: str = Field(pattern=r"^([01]?\d|2[0-3]):[0-5]\d$",
                      description="24-hour time, e.g. 05:00")
    label: str = Field(default="JARVIS alarm", max_length=80)


class ContactArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact: str = Field(min_length=1, max_length=120)


class WhatsAppArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact: str = Field(min_length=1, max_length=120)
    message: str = Field(default="", max_length=2000)
    send: bool = Field(default=False, description="Only true when the user explicitly asked to send")


async def _call(ctx: ToolContext, action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    if ctx.bridge is None:
        raise ToolError("The phone bridge is not running.")
    return await ctx.bridge.request(action, args or {}, ctx.settings.phone_request_timeout)


def _offline_message() -> str:
    return (
        "No Android phone is connected. Open the JARVIS companion app on your phone, "
        "connect it to this desktop, then try again."
    )


def _result(result: dict[str, Any]) -> ToolResult:
    ok = bool(result.get("ok"))
    message = str(result.get("message") or ("Done." if ok else "The phone rejected the request."))
    data = result.get("data") or {}
    return ToolResult(ok=ok, summary=message, data=data, error=None if ok else message)


async def status_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    status = ctx.bridge.status() if ctx.bridge else {"connected": False}
    if status.get("connected"):
        battery = status.get("battery")
        suffix = f" Battery {battery}%." if battery is not None else ""
        return ToolResult.success(
            f"{status.get('name', 'Android phone')} is connected.{suffix}", status=status
        )
    return ToolResult.success(_offline_message(), status=status)


async def battery_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    try:
        return _result(await _call(ctx, "battery"))
    except ToolError:
        return ToolResult.failure(_offline_message())


async def open_app_handler(args: OpenAppArgs, ctx: ToolContext) -> ToolResult:
    try:
        return _result(await _call(ctx, "app.open", {"app": args.app}))
    except ToolError:
        return ToolResult.failure(_offline_message())


async def alarm_handler(args: AlarmArgs, ctx: ToolContext) -> ToolResult:
    hour, minute = (int(part) for part in args.time.split(":"))
    try:
        result = await _call(ctx, "alarm.set", {"hour": hour, "minute": minute, "label": args.label})
    except ToolError:
        return ToolResult.failure(_offline_message())
    ctx.audit.record(ctx.actor, "phone.alarm", Risk.MODERATE.value,
                     {"time": args.time, "label": args.label}, "ok" if result.get("ok") else "failed")
    return _result(result)


async def contact_handler(args: ContactArgs, ctx: ToolContext) -> ToolResult:
    try:
        return _result(await _call(ctx, "contacts.search", {"query": args.contact}))
    except ToolError:
        return ToolResult.failure(_offline_message())


async def call_handler(args: ContactArgs, ctx: ToolContext) -> ToolResult:
    try:
        result = await _call(ctx, "call.dial", {"contact": args.contact})
    except ToolError:
        return ToolResult.failure(_offline_message())
    ctx.audit.record(ctx.actor, "phone.dial", Risk.HIGH.value, {"contact": args.contact},
                     "ok" if result.get("ok") else "failed")
    return _result(result)


async def whatsapp_handler(args: WhatsAppArgs, ctx: ToolContext) -> ToolResult:
    try:
        result = await _call(
            ctx, "whatsapp.chat",
            {"contact": args.contact, "message": args.message, "send": args.send},
        )
    except ToolError:
        return ToolResult.failure(_offline_message())
    ctx.audit.record(ctx.actor, "phone.whatsapp", Risk.HIGH.value,
                     {"contact": args.contact, "send": args.send},
                     "ok" if result.get("ok") else "failed")
    return _result(result)


async def reels_start_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    try:
        result = await _call(ctx, "reels.start")
    except ToolError:
        return ToolResult.failure(_offline_message())
    ctx.audit.record(ctx.actor, "phone.reels.start", Risk.MODERATE.value, {},
                     "ok" if result.get("ok") else "failed")
    return _result(result)


async def reels_stop_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    try:
        return _result(await _call(ctx, "reels.stop"))
    except ToolError:
        return ToolResult.failure(_offline_message())


def build() -> list[ToolDefinition]:
    return [
        ToolDefinition(name="phone.status", description="Report the phone connection state.",
                       args_model=EmptyArgs, risk=Risk.LOW, handler=status_handler, category="phone"),
        ToolDefinition(name="phone.battery", description="Read the paired phone's battery level.",
                       args_model=EmptyArgs, risk=Risk.LOW, handler=battery_handler, category="phone"),
        ToolDefinition(name="phone.open_app", description="Launch an app on the paired phone.",
                       args_model=OpenAppArgs, risk=Risk.LOW, handler=open_app_handler, category="phone"),
        ToolDefinition(name="phone.alarm", description="Set an alarm on the phone (asks for confirmation).",
                       args_model=AlarmArgs, risk=Risk.MODERATE, handler=alarm_handler,
                       confirm=True, category="phone"),
        ToolDefinition(name="phone.find_contact", description="Search contacts on the phone.",
                       args_model=ContactArgs, risk=Risk.LOW, handler=contact_handler, category="phone"),
        ToolDefinition(name="phone.call", description="Open the phone dialer for a contact (does not dial silently).",
                       args_model=ContactArgs, risk=Risk.HIGH, handler=call_handler,
                       confirm=True, category="phone"),
        ToolDefinition(name="phone.whatsapp",
                       description="Open a WhatsApp chat with a contact and a drafted message.",
                       args_model=WhatsAppArgs, risk=Risk.HIGH, handler=whatsapp_handler,
                       confirm=True, category="phone"),
        ToolDefinition(name="phone.reels_start",
                       description="Start a user-enabled accessibility auto-scroll for reels.",
                       args_model=EmptyArgs, risk=Risk.MODERATE, handler=reels_start_handler,
                       confirm=True, category="phone"),
        ToolDefinition(name="phone.reels_stop", description="Stop the accessibility auto-scroll.",
                       args_model=EmptyArgs, risk=Risk.LOW, handler=reels_stop_handler, category="phone"),
    ]
