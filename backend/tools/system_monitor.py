"""Real Windows telemetry — nothing here is hardcoded."""
from __future__ import annotations

import asyncio
import ctypes
import os
import platform
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field

import paths
from security.permissions import Risk
from tools.base import ToolContext, ToolDefinition, ToolResult

psutil.cpu_percent(interval=None)  # prime the first reading

_INTERNET: dict[str, Any] = {"checked": 0.0, "value": None}
_PROC_SAMPLES: dict[int, tuple[float, float]] = {}
_LAST_STATS: dict[str, Any] = {}


def _internet_connected(timeout: float = 0.7, cache_seconds: float = 10.0) -> bool:
    now = time.time()
    if _INTERNET["value"] is not None and now - _INTERNET["checked"] < cache_seconds:
        return bool(_INTERNET["value"])
    value = False
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=timeout):
            value = True
    except OSError:
        value = False
    _INTERNET.update(checked=now, value=value)
    return value


def _battery() -> dict[str, Any]:
    try:
        battery = psutil.sensors_battery()
    except (AttributeError, NotImplementedError):
        battery = None
    if battery is None:
        return {"available": False, "reason": "No battery sensor reported by Windows."}
    plugged = bool(battery.power_plugged)
    minutes: int | None = None
    if battery.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        minutes = int(battery.secsleft // 60)
    return {
        "available": True,
        "percent": round(float(battery.percent), 1),
        "plugged_in": plugged,
        "charging": plugged,
        "time_left_minutes": minutes,
    }


def _humanize(seconds: float) -> str:
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _network() -> dict[str, Any]:
    interfaces = []
    for name, stats in psutil.net_if_stats().items():
        interfaces.append({"name": name, "up": bool(stats.isup), "speed_mbps": stats.speed})
    interfaces.sort(key=lambda item: (not item["up"], item["name"]))
    active = [item["name"] for item in interfaces if item["up"]]
    connected = _internet_connected()
    return {
        "internet": connected,
        "connected": connected,
        "interfaces": interfaces,
        "active_interfaces": active,
    }


def top_processes(limit: int = 8) -> list[dict[str, Any]]:
    now = time.time()
    seen: set[int] = set()
    results: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            pid = proc.pid
            seen.add(pid)
            cpu_total = sum(proc.cpu_times()[:2])
            previous = _PROC_SAMPLES.get(pid)
            cpu_percent: float | None = None
            if previous and now - previous[0] > 0.2:
                elapsed = now - previous[0]
                cpu_percent = round(max(0.0, (cpu_total - previous[1]) / elapsed * 100.0), 1)
            _PROC_SAMPLES[pid] = (now, cpu_total)
            memory = proc.info.get("memory_info")
            results.append(
                {
                    "pid": pid,
                    "name": proc.info.get("name") or f"pid {pid}",
                    "memory_mb": round(memory.rss / (1024 * 1024), 1) if memory else None,
                    "cpu_percent": cpu_percent,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    for stale in set(_PROC_SAMPLES) - seen:
        del _PROC_SAMPLES[stale]
    results.sort(key=lambda item: item["memory_mb"] or 0.0, reverse=True)
    return results[:limit]


def collect_system_stats(include_processes: bool = True) -> dict[str, Any]:
    global _LAST_STATS
    try:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(Path_home()))
        cpu_freq = psutil.cpu_freq()
        boot = psutil.boot_time()
        # First reading after startup is meaningless without a sample window.
        cpu_percent = psutil.cpu_percent(interval=0.12 if not _LAST_STATS else None)
        stats: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cpu": {
                "percent": round(cpu_percent, 1),
                "cores": psutil.cpu_count(logical=True),
                "physical_cores": psutil.cpu_count(logical=False),
                "frequency_mhz": round(cpu_freq.current, 0) if cpu_freq else None,
            },
            "memory": {
                "total_gb": round(memory.total / 1024**3, 1),
                "used_gb": round(memory.used / 1024**3, 1),
                "available_gb": round(memory.available / 1024**3, 1),
                "percent": round(memory.percent, 1),
            },
            "battery": _battery(),
            "disk": {
                "path": str(Path_home()),
                "total_gb": round(disk.total / 1024**3, 1),
                "used_gb": round(disk.used / 1024**3, 1),
                "free_gb": round(disk.free / 1024**3, 1),
                "percent": round(disk.percent, 1),
            },
            "network": _network(),
            "uptime": {
                "seconds": int(time.time() - boot),
                "humanized": _humanize(time.time() - boot),
            },
            "os": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "hostname": platform.node(),
                "python": platform.python_version(),
            },
            "process_count": len(psutil.pids()),
        }
        if include_processes:
            stats["processes"] = top_processes()
        _LAST_STATS = stats
        return stats
    except Exception as exc:  # noqa: BLE001 - telemetry must never break the API
        fallback = dict(_LAST_STATS)
        fallback["error"] = f"Telemetry collection failed: {exc}"
        return fallback


def Path_home():  # small indirection keeps tests able to override
    from pathlib import Path

    return Path.home()


class StatsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[
        Literal["cpu", "memory", "battery", "disk", "network", "uptime", "processes"]
    ] = Field(
        default_factory=lambda: ["cpu", "memory", "battery", "disk", "network", "uptime"]
    )


async def _stats_handler(args: StatsArgs, ctx: ToolContext) -> ToolResult:
    stats = await asyncio.to_thread(collect_system_stats, "processes" in args.sections)
    lines: list[str] = []
    for section in args.sections:
        if section == "cpu":
            lines.append(f"CPU usage is {stats['cpu']['percent']}% across {stats['cpu']['cores']} threads.")
        elif section == "memory":
            mem = stats["memory"]
            lines.append(f"RAM: {mem['used_gb']} GB of {mem['total_gb']} GB used ({mem['percent']}%).")
        elif section == "battery":
            battery = stats["battery"]
            if battery.get("available"):
                state = "charging" if battery["plugged_in"] else "on battery"
                extra = ""
                if battery.get("time_left_minutes") is not None and not battery["plugged_in"]:
                    extra = f", about {battery['time_left_minutes']} minutes remaining"
                lines.append(f"Battery is at {battery['percent']}% ({state}{extra}).")
            else:
                lines.append("Battery information is unavailable on this device.")
        elif section == "disk":
            disk = stats["disk"]
            lines.append(f"Disk: {disk['free_gb']} GB free of {disk['total_gb']} GB ({disk['percent']}% used).")
        elif section == "network":
            lines.append(
                "Internet connection is active."
                if stats["network"]["internet"]
                else "No internet connection detected."
            )
        elif section == "uptime":
            lines.append(f"System uptime is {stats['uptime']['humanized']}.")
        elif section == "processes":
            top = stats.get("processes", [])[:5]
            if top:
                lines.append(
                    "Top processes by memory: "
                    + ", ".join(f"{p['name']} ({p['memory_mb']} MB)" for p in top)
                    + "."
                )
    filtered = {key: stats[key] for key in args.sections if key in stats}
    return ToolResult.success(" ".join(lines), stats=filtered)


class ScreenshotArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _screenshot_handler(args: ScreenshotArgs, ctx: ToolContext) -> ToolResult:
    try:
        from PIL import ImageGrab
    except ImportError:
        return ToolResult.failure(
            "Screenshots need Pillow. Install it with: pip install Pillow"
        )

    def capture() -> dict:
        from datetime import datetime

        configured = str(ctx.repo.get_setting("paths.screenshots", "") or "").strip()
        target_dir = (Path(os.path.expandvars(configured)).expanduser() if configured
                      else paths.screenshots_dir())
        target_dir.mkdir(parents=True, exist_ok=True)
        image = ImageGrab.grab(all_screens=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = target_dir / f"screenshot_{stamp}.png"
        image.save(path, "PNG")
        return {"path": str(path), "width": image.width, "height": image.height,
                "size_kb": round(Path(path).stat().st_size / 1024, 1)}

    try:
        info = await asyncio.to_thread(capture)
    except OSError as exc:
        return ToolResult.failure(f"Screenshot capture failed: {exc}")
    saved = Path(info["path"])
    if not saved.exists():
        return ToolResult.failure("The capture reported success but no file was written.")
    ctx.repo.add_artifact("screenshot", str(saved), saved.name, ctx.task_id)
    # Opening is deliberately limited to the file this tool just wrote: os.startfile
    # runs an executable it is handed, so a path from a model or a webpage must never
    # reach it. Nothing is said about where the file lives — the viewer is the reply.
    opened, _ = await asyncio.to_thread(paths.open_path, saved)
    return ToolResult.success(
        "Executed." if opened else "Captured, but the viewer did not open.",
        location=paths.friendly_target(saved),
        opened=opened,
        **info,
    )


_HARDWARE_CACHE: dict = {"at": 0.0, "value": None}


def hardware_info() -> dict:
    now = time.time()
    if _HARDWARE_CACHE["value"] is not None and now - _HARDWARE_CACHE["at"] < 300:
        return dict(_HARDWARE_CACHE["value"])
    cpu_name: str | None = None
    gpus: list[str] = []
    device_model: str | None = None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            cpu_name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip() or None
    except OSError:
        cpu_name = None
    try:
        import subprocess

        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "$g = Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name };"
                "$m = Get-CimInstance Win32_ComputerSystem | ForEach-Object { $_.Model };"
                "if ($g) { $g } else { '' }; $m",
            ],
            capture_output=True, text=True, timeout=8,
        )
        if out.returncode == 0:
            lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
            if lines:
                device_model = lines[-1]
                gpus = lines[:-1]
    except Exception:
        gpus, device_model = [], None
    value = {"cpu_name": cpu_name, "gpus": gpus, "device_model": device_model}
    _HARDWARE_CACHE.update(at=now, value=value)
    return dict(value)


class LockArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _lock_handler(args: LockArgs, ctx: ToolContext) -> ToolResult:
    if os.name != "nt":
        return ToolResult.failure("Locking the workstation is only supported on Windows.")
    ok = await asyncio.to_thread(ctypes.windll.user32.LockWorkStation)
    if ok:
        return ToolResult.success("Workstation locked.")
    return ToolResult.failure("Windows refused the lock request (error %s)." % ctypes.GetLastError())


def _open_display_settings() -> bool:
    try:
        os.startfile("ms-settings:display")  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


class BrightnessArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["get", "set", "up", "down", "max", "min"] = Field(
        default="get", description="get reads it; set needs percent; up/down use step."
    )
    percent: int | None = Field(default=None, ge=0, le=100)
    step: int = Field(default=20, ge=1, le=100)


# One PowerShell process does read + change + re-read. Spawning powershell.exe
# costs more than the WMI call itself, so the round trip is never split in two.
_BRIGHTNESS_TEMPLATE = """
$ErrorActionPreference = 'Stop'
try {{
  $b = Get-CimInstance -Namespace root\\wmi -ClassName WmiMonitorBrightness
  $cur = [int]($b | Select-Object -First 1 -ExpandProperty CurrentBrightness)
  if ('{action}' -eq 'get') {{ 'OK|' + $cur + '|' + $cur; exit }}
  $m = Get-CimInstance -Namespace root\\wmi -ClassName WmiMonitorBrightnessMethods |
    Select-Object -First 1
  switch ('{action}') {{
    'set'  {{ $target = {percent} }}
    'up'   {{ $target = [Math]::Min(100, $cur + {step}) }}
    'down' {{ $target = [Math]::Max(1, $cur - {step}) }}
    'max'  {{ $target = 100 }}
    'min'  {{ $target = 1 }}
  }}
  # A CIM instance does not carry the WMI method, so call it through
  # Invoke-CimMethod; the return value is empty even on success, which is why
  # the level is re-read below rather than trusted from the call.
  Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness `
    -Arguments @{{ Timeout = 1; Brightness = $target }} | Out-Null
  $after = $target
  for ($i = 0; $i -lt 6; $i++) {{
    Start-Sleep -Milliseconds 150
    $now = [int](Get-CimInstance -Namespace root\\wmi -ClassName WmiMonitorBrightness |
      Select-Object -First 1 -ExpandProperty CurrentBrightness)
    if ($now -eq $target) {{ $after = $now; break }}
    $after = $now
  }}
  'OK|' + $cur + '|' + $after
}} catch {{
  'ERR|' + $_.Exception.Message
}}
"""


def _run_brightness(script: str) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=20,
    )
    return (out.stdout or "").strip()


async def _brightness_handler(args: BrightnessArgs, ctx: ToolContext) -> ToolResult:
    if os.name != "nt":
        return ToolResult.failure("Brightness control is only supported on Windows.")
    if args.action == "set" and args.percent is None:
        return ToolResult.failure("Tell me a percentage to set the brightness to.")

    script = _BRIGHTNESS_TEMPLATE.format(
        action=args.action,
        percent=int(args.percent or 0),
        step=int(args.step),
    )
    try:
        raw = await asyncio.to_thread(_run_brightness, script)
    except subprocess.TimeoutExpired:
        return ToolResult.failure("Windows did not answer the brightness request in time.")
    except OSError as exc:
        return ToolResult.failure(f"Brightness call failed: {exc}")

    status, _, rest = raw.partition("|")
    if status != "OK":
        detail = rest[:160] or "no reply from Windows"
        ctx.audit.record(ctx.actor, "system.brightness", Risk.LOW.value,
                         {"action": args.action}, "error")
        if "class" in detail.lower() or "not found" in detail.lower():
            # A desktop panel driven by the GPU, not by WMI. Say so and hand him
            # the one control that does work rather than hedging in prose.
            opened = await asyncio.to_thread(_open_display_settings)
            return ToolResult.failure(
                "This screen has no hardware brightness control I can reach. "
                + ("Display settings is open for you to use the slider."
                   if opened else "Try the slider in Settings > System > Display.")
            )
        return ToolResult.failure(f"Windows refused the brightness change ({detail}).")

    current_text, _, after_text = rest.partition("|")
    try:
        current, after = int(current_text), int(after_text)
    except ValueError:
        return ToolResult.failure("Windows reported a brightness value I could not read.")

    ctx.audit.record(ctx.actor, "system.brightness", Risk.LOW.value,
                     {"action": args.action, "from": current, "to": after}, "ok")
    if args.action == "get":
        return ToolResult.success(f"Brightness is at {current} percent.", current=current)
    if after == current:
        return ToolResult.success(
            f"It is already at {current} percent.", current=current, changed=False
        )
    return ToolResult.success(f"Brightness {current} to {after} percent.",
                              current=after, previous=current, changed=True)


class VolumeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["get", "set", "up", "down", "mute", "unmute"] = Field(
        default="get", description="get reads it; set needs percent; up/down use step."
    )
    percent: int | None = Field(default=None, ge=0, le=100)
    step: int = Field(default=10, ge=1, le=100)


async def _volume_handler(args: VolumeArgs, ctx: ToolContext) -> ToolResult:
    if os.name != "nt":
        return ToolResult.failure("Volume control is only supported on Windows.")
    if args.action == "set" and args.percent is None:
        return ToolResult.failure("Tell me a percentage to set the volume to.")

    # Imported here so a missing audio library is reported as a missing
    # capability instead of breaking every other system tool at startup.
    try:
        from tools import volume_control
    except ImportError:
        return ToolResult.failure(
            "Volume control needs pycaw. Install it with: pip install pycaw comtypes"
        )

    def operate() -> tuple[int, bool, int | None]:
        current, was_muted = volume_control.read()
        if args.action == "get":
            return current, was_muted, None
        if args.action == "mute":
            if was_muted:
                return current, was_muted, None
            after, muted = volume_control.write(muted=True)
            return after, muted, current
        if args.action == "unmute":
            if not was_muted:
                return current, was_muted, None
            after, muted = volume_control.write(muted=False)
            return after, muted, current
        target = (int(args.percent) if args.action == "set"
                  else min(100, max(0, current + (args.step if args.action == "up" else -args.step))))
        if target == current:
            return current, was_muted, None
        after, muted = volume_control.write(percent=target)
        return after, muted, current

    try:
        level, muted, previous = await asyncio.to_thread(operate)
    except volume_control.VolumeUnavailable as exc:
        ctx.audit.record(ctx.actor, "system.volume", Risk.LOW.value,
                         {"action": args.action}, "error")
        return ToolResult.failure(f"I could not reach the volume control: {exc}.")

    ctx.audit.record(ctx.actor, "system.volume", Risk.LOW.value,
                     {"action": args.action, "from": previous, "to": level,
                      "muted": muted}, "ok")
    if args.action == "get":
        spoken = (f"Volume is at {level} percent, but the speakers are muted."
                  if muted else f"Volume is at {level} percent.")
        return ToolResult.success(spoken, level=level, muted=muted)
    if previous is None:
        if args.action == "mute":
            return ToolResult.success("It is already muted.", level=level, muted=True)
        if args.action == "unmute":
            return ToolResult.success("It is not muted.", level=level, muted=False)
        return ToolResult.success(
            f"It is already at {level} percent.", level=level, changed=False
        )
    if args.action in {"mute", "unmute"}:
        return ToolResult.success("Muted." if muted else "Unmuted.",
                                  level=level, muted=muted)
    return ToolResult.success(f"Volume {previous} to {level} percent.",
                              level=level, previous=previous, muted=muted, changed=True)


def build() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="system.stats",
            description="Read real CPU, memory, battery, disk, network and uptime telemetry.",
            args_model=StatsArgs,
            risk=Risk.LOW,
            handler=_stats_handler,
            category="system",
        ),
        ToolDefinition(
            name="system.screenshot",
            description="Capture the screen and save a PNG into the output folder.",
            args_model=ScreenshotArgs,
            risk=Risk.MODERATE,
            handler=_screenshot_handler,
            category="system",
        ),
        ToolDefinition(
            name="system.lock",
            description="Lock the Windows workstation.",
            args_model=LockArgs,
            risk=Risk.MODERATE,
            handler=_lock_handler,
            confirm=False,
            category="system",
        ),
        ToolDefinition(
            name="system.brightness",
            description="Read or change the laptop screen brightness. up/down move it by "
                        "step percent; set needs a percent from 0 to 100.",
            args_model=BrightnessArgs,
            risk=Risk.LOW,
            handler=_brightness_handler,
            confirm=False,
            category="system",
        ),
        ToolDefinition(
            name="system.volume",
            description="Read or change the Windows master volume. up/down move it by "
                        "step percent; set needs a percent from 0 to 100; mute and "
                        "unmute silence the speakers.",
            args_model=VolumeArgs,
            risk=Risk.LOW,
            handler=_volume_handler,
            confirm=False,
            category="system",
        ),
    ]
