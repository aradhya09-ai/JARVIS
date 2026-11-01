"""Allowlisted Windows application launcher and window control."""
from __future__ import annotations

import asyncio
import ctypes
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

import psutil
import winreg
from pydantic import BaseModel, ConfigDict, Field

import paths
from database.repository import Repository
from security.permissions import Risk
from tools.base import ToolContext, ToolDefinition, ToolError, ToolResult


def _expand(raw: str) -> Path:
    return Path(os.path.expandvars(raw)).expanduser()


def _registry_app_path(exe: str) -> Path | None:
    if os.name != "nt":
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(
                hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
            ) as key:
                value, _ = winreg.QueryValueEx(key, "")
                candidate = _expand(value.strip('"'))
                if candidate.exists():
                    return candidate
        except OSError:
            continue
    return None


def resolve_spec(spec: dict[str, Any]) -> Path | None:
    if spec.get("kind") == "uri":
        return None
    for raw in spec.get("paths", []):
        candidate = _expand(raw)
        if candidate.exists():
            return candidate
    exe = spec.get("executable")
    if exe:
        found = shutil.which(exe)
        if found:
            return Path(found)
        registry = _registry_app_path(exe)
        if registry:
            return registry
        for base in (
            os.environ.get("SystemRoot", r"C:\Windows"),
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ):
            candidate = Path(base) / "System32" / exe
            if candidate.exists():
                return candidate
    return None


def normalize(name: str) -> str:
    return " ".join(name.lower().replace("_", " ").replace("-", " ").split())


def find_shortcut(repo: Repository, name: str) -> dict[str, Any] | None:
    wanted = normalize(name)
    if not wanted:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for spec in repo.list_apps():
        candidates = [normalize(spec.get("name", ""))] + [
            normalize(alias) for alias in spec.get("aliases", [])
        ]
        exe = normalize(spec.get("executable", ""))
        if exe.endswith(".exe"):
            exe = exe[:-4]
        candidates.append(exe)
        if wanted in candidates:
            return spec
        for candidate in candidates:
            if candidate and (wanted in candidate or candidate in wanted):
                score = len(candidate)
                if best is None or score > best[0]:
                    best = (score, spec)
    return best[1] if best else None


def label_matches(wanted: str, label: str) -> bool:
    """Word-prefix matching, so 'code' finds 'Visual Studio Code' but not 'Unicode'."""
    if not wanted:
        return False
    if wanted == label:
        return True
    tokens = label.split()
    return all(
        any(token == wanted_token or token.startswith(wanted_token) for token in tokens)
        for wanted_token in wanted.split()
    )


_UNSAFE_SHORTCUT = re.compile(r"\buninstall\b|\bremove\b|\brepair\b", re.I)


def scan_shortcuts(name: str) -> list[Path]:
    """Desktop and Start Menu .lnk/.url entries whose label matches a spoken name.

    This is what lets "open OBS" work for anything Windows itself lists, without
    JARVIS ever searching the disk or running a command line. Uninstallers are
    skipped: a Start Menu folder holds "Uninstall Foo" entries, and "open foo"
    must never reach one of those.
    """
    wanted = normalize(name)
    hits: list[Path] = []
    for root in paths.shortcut_roots():
        try:
            # One walk per root: rglob-ing twice doubles the cost of scanning
            # the tens of thousands of Start Menu entries this runs against.
            entries = [
                Path(dirpath, filename)
                for dirpath, _, filenames in os.walk(root)
                for filename in filenames
                if filename.endswith((".lnk", ".url"))
            ]
        except OSError:
            continue
        for entry in entries:
            if _UNSAFE_SHORTCUT.search(entry.stem):
                continue
            if label_matches(wanted, normalize(entry.stem)):
                hits.append(entry)
    return sorted(hits, key=lambda p: (len(normalize(p.stem)), str(p)))


# Windows Settings pages reachable by URI. Only well-established pages are
# listed; an unlisted page falls through to the Start Menu scan instead of
# guessing at a URI that might open the wrong thing.
SETTINGS_PAGES: dict[str, tuple[str, str]] = {
    "display": ("ms-settings:display", "Display"),
    "brightness": ("ms-settings:display", "Display"),
    "screen resolution": ("ms-settings:display", "Display"),
    "night light": ("ms-settings:nightlight", "Night light"),
    "nightlight": ("ms-settings:nightlight", "Night light"),
    "sound": ("ms-settings:sound", "Sound"),
    "volume": ("ms-settings:sound", "Sound"),
    "network": ("ms-settings:network", "Network"),
    "wifi": ("ms-settings:network-wifi", "Wi-Fi"),
    "bluetooth": ("ms-settings:bluetooth", "Bluetooth"),
    "notifications": ("ms-settings:notifications", "Notifications"),
    "apps": ("ms-settings:appsfeatures", "Apps"),
    "storage": ("ms-settings:storagesense", "Storage"),
    "update": ("ms-settings:windowsupdate", "Windows Update"),
    "windows update": ("ms-settings:windowsupdate", "Windows Update"),
    "personalization": ("ms-settings:personalization", "Personalization"),
    "background": ("ms-settings:background", "Background"),
    "themes": ("ms-settings:themes", "Themes"),
    "mouse": ("ms-settings:mousetouchpad", "Mouse"),
    "touchpad": ("ms-settings:mousetouchpad", "Touchpad"),
    "keyboard": ("ms-settings:keyboard", "Keyboard"),
    "date and time": ("ms-settings:dateandtime", "Date and time"),
    "language": ("ms-settings:regionlanguage", "Language"),
    "printers": ("ms-settings:printers", "Printers"),
    "accounts": ("ms-settings:emailandaccounts", "Accounts"),
    "default apps": ("ms-settings:defaultapps", "Default apps"),
    "about": ("ms-settings:about", "About"),
    "power": ("ms-settings:powersleep", "Power"),
    "battery": ("ms-settings:batterysaver", "Battery"),
    "ease of access": ("ms-settings:easeofaccess", "Ease of access"),
    "privacy": ("ms-settings:privacy", "Privacy"),
}


def find_settings_page(name: str) -> tuple[str, str] | None:
    """Map "open bluetooth settings" onto the Settings page, if the name is one."""
    wanted = normalize(name).replace(" settings", "").replace(" setting", "").strip()
    if not wanted:
        return None
    exact = SETTINGS_PAGES.get(wanted)
    if exact:
        return exact
    for key, page in SETTINGS_PAGES.items():
        if key in wanted.split() or (len(key) > 4 and key in wanted):
            return page
    return None


def _process_name_matches(name: str, spec: dict[str, Any]) -> bool:
    exe = (spec.get("executable") or "").lower().removesuffix(".exe")
    lowered = name.lower().removesuffix(".exe")
    return bool(exe and (exe == lowered))


def list_windows(limit: int = 80) -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    windows: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = ""
        try:
            process_name = psutil.Process(pid.value).name()
        except (psutil.Error, OSError):
            pass
        windows.append(
            {
                "hwnd": int(hwnd),
                "title": buffer.value,
                "pid": int(pid.value),
                "process": process_name,
            }
        )
        return True

    user32.EnumWindows(callback, 0)
    return windows[:limit]


def running_processes(limit: int = 60) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "username"]):
        try:
            info = proc.info
            items.append(
                {"pid": info["pid"], "name": info["name"], "user": info.get("username")}
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    items.sort(key=lambda item: (item["name"] or "").lower())
    return items[:limit]


def _focus_window(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
    return int(user32.GetForegroundWindow()) == int(hwnd)


def _launch(spec: dict[str, Any]) -> dict[str, Any]:
    if spec.get("kind") == "uri":
        os.startfile(spec["uri"])  # type: ignore[attr-defined]
        return {"method": "uri", "target": spec["uri"], "alive": True}
    executable = resolve_spec(spec)
    if executable is None:
        raise ToolError(
            f"I could not find {spec.get('name')} on this computer.",
            hint="Install it, then add or edit its shortcut in Settings → Applications.",
        )
    process = subprocess.Popen(
        [str(executable)],
        cwd=str(Path.home()),
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Long enough to catch an executable that fails at once, short enough that
    # "open X" answers inside a second instead of after 1.2.
    time.sleep(0.35)
    exit_code = process.poll()
    return {
        "method": "exe",
        "target": str(executable),
        "pid": process.pid,
        "alive": exit_code is None,
        "exit_code": exit_code,
    }


class AppNameArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120, description="Friendly app name, e.g. 'VS Code'")


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _open_unlisted(name: str, ctx: ToolContext) -> ToolResult | None:
    """Reach anything Windows itself lists, not only the seeded application table.

    Two bounded sources: a Windows Settings page, and a Desktop or Start Menu
    shortcut. Never an arbitrary path, and never a shell command line.
    """
    if os.name == "nt":
        page = find_settings_page(name)
        if page:
            uri, label = page
            try:
                await asyncio.to_thread(os.startfile, uri)  # type: ignore[attr-defined]
            except OSError:
                return ToolResult.failure(f"Windows would not open the {label} page.")
            ctx.audit.record(ctx.actor, "app.open", Risk.LOW.value,
                             {"settings_page": label, "uri": uri}, "ok")
            return ToolResult.success(f"Opening {label} settings.", settings_uri=uri)
    hits = await asyncio.to_thread(scan_shortcuts, name)
    if not hits:
        return None
    if len(hits) > 1:
        names = ", ".join(path.stem for path in hits[:5])
        return ToolResult.success(
            f"Several things match {name}: {names}. Tell me which one.",
            candidates=[str(path) for path in hits[:5]],
            needs_choice=True,
        )
    target = hits[0]
    opened, _ = await asyncio.to_thread(paths.open_path, target)
    if not opened:
        return ToolResult.failure(f"Windows would not open {target.stem}.")
    ctx.audit.record(ctx.actor, "app.open", Risk.MODERATE.value,
                     {"shortcut": str(target)}, "ok")
    return ToolResult.success(f"Opening {target.stem}.", shortcut=str(target))


async def open_handler(args: AppNameArgs, ctx: ToolContext) -> ToolResult:
    name = args.name.strip()
    spec = find_shortcut(ctx.repo, name)
    if spec is None:
        fallback = await _open_unlisted(name, ctx)
        if fallback is not None:
            return fallback
        return ToolResult.failure(f"I could not find {name} on this computer.")
    if spec.get("kind") != "uri" and resolve_spec(spec) is None:
        return ToolResult.failure(
            f"{spec['name']} is configured but not installed (or its path changed).",
            target=spec.get("executable"),
        )
    launch = await asyncio.to_thread(_launch, spec)
    ctx.audit.record(
        ctx.actor, "app.open", Risk.LOW.value,
        {"app": spec["name"], "target": launch.get("target")}, "ok",
    )
    # The absolute path stays in the payload for the UI. Spoken, it is noise:
    # "Opened VS Code, C:\Users\aradh\AppData\Local\Programs\Microsoft VS
    # Code\Code.exe" is the whole sentence, and the name was the only useful word.
    if launch.get("alive") or launch.get("exit_code") in (None, 0):
        return ToolResult.success(f"Opening {spec['name']}.", app=spec["name"], launch=launch)
    return ToolResult.failure(
        f"{spec['name']} started and exited at once (code {launch.get('exit_code')}).",
        app=spec["name"], launch=launch,
    )


async def switch_handler(args: AppNameArgs, ctx: ToolContext) -> ToolResult:
    spec = find_shortcut(ctx.repo, args.name)
    target_name = spec["name"] if spec else args.name
    if os.name != "nt":
        return ToolResult.failure("Window switching is only supported on Windows.")
    windows = await asyncio.to_thread(list_windows, 200)
    wanted = normalize(target_name)
    exe_alias = normalize((spec or {}).get("executable", "").removesuffix(".exe"))

    def score(window: dict[str, Any]) -> int:
        title = normalize(window["title"])
        process = normalize((window["process"] or "").removesuffix(".exe"))
        points = 0
        if wanted and wanted in title:
            points += 3
        if exe_alias and process == exe_alias:
            points += 2
        if spec and _process_name_matches(window["process"], spec):
            points += 2
        return points

    ranked = sorted(windows, key=score, reverse=True)
    if not ranked or score(ranked[0]) == 0:
        return ToolResult.failure(
            f"No open window for {target_name} was found. Say 'open {target_name}' to launch it."
        )
    window = ranked[0]
    focused = await asyncio.to_thread(_focus_window, window["hwnd"])
    if not focused:
        return ToolResult.failure(
            f"Found the {target_name} window, but Windows blocked the focus change. "
            "Click its taskbar icon to bring it forward."
        )
    ctx.audit.record(ctx.actor, "app.switch", Risk.LOW.value,
                     {"app": target_name, "window": window["title"]}, "ok")
    return ToolResult.success(f"Switched to {target_name} — \"{window['title']}\".",
                              window=window)


async def close_handler(args: AppNameArgs, ctx: ToolContext) -> ToolResult:
    spec = find_shortcut(ctx.repo, args.name)
    wanted = normalize((spec or {}).get("executable", "").removesuffix(".exe")) or normalize(args.name)
    if not wanted:
        return ToolResult.failure("Tell me which application to close.")
    victims = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info["name"] or "").removesuffix(".exe")
            if name.lower() == wanted or (not spec and normalize(args.name) in name.lower()):
                victims.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not victims:
        return ToolResult.failure(f"{args.name} does not appear to be running.")
    closed, refused = [], []
    for proc in victims[:10]:
        try:
            name = proc.name()
            proc.terminate()
            try:
                proc.wait(timeout=3)
                closed.append(name)
            except psutil.TimeoutExpired:
                refused.append(name)
        except psutil.Error:
            refused.append(proc.info.get("name") or str(proc.pid))
    if not closed:
        return ToolResult.failure(
            f"Windows refused to close {args.name}. A process may be protected or awaiting a save dialog."
        )
    summary = f"Closed {len(closed)} {args.name} process(es)."
    if refused:
        summary += f" {len(refused)} did not respond and was left running."
    ctx.audit.record(ctx.actor, "app.close", Risk.MODERATE.value,
                     {"app": args.name, "closed": closed, "refused": refused}, "ok")
    return ToolResult.success(summary, closed=closed, refused=refused)


async def list_running_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    windows, processes = await asyncio.gather(
        asyncio.to_thread(list_windows, 80), asyncio.to_thread(running_processes, 80)
    )
    visible = [w for w in windows if w["title"]]
    summary = (
        f"{len(visible)} visible windows are open."
        if visible
        else "No visible application windows were detected."
    )
    return ToolResult.success(summary, windows=visible, processes=processes)


async def list_configured_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    apps = []
    for spec in ctx.repo.list_apps():
        resolved = resolve_spec(spec)
        apps.append(
            {
                "name": spec["name"],
                "aliases": spec.get("aliases", []),
                "kind": spec.get("kind", "exe"),
                "target": str(resolved) if resolved else spec.get("uri", ""),
                "available": bool(resolved) or spec.get("kind") == "uri",
            }
        )
    available = [a["name"] for a in apps if a["available"]]
    return ToolResult.success(
        f"{len(available)} of {len(apps)} configured apps are installed: {', '.join(available)}.",
        apps=apps,
    )


def build() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="app.open",
            description="Launch an application by name: a configured app, a Windows Settings "
                        "page, or any Desktop/Start Menu shortcut. E.g. VS Code, Chrome, "
                        "Bluetooth settings.",
            args_model=AppNameArgs,
            risk=Risk.LOW,
            handler=open_handler,
            category="applications",
        ),
        ToolDefinition(
            name="app.switch",
            description="Focus an already-running application window.",
            args_model=AppNameArgs,
            risk=Risk.LOW,
            handler=switch_handler,
            category="applications",
        ),
        ToolDefinition(
            name="app.close",
            description="Close an application's processes (asks for confirmation).",
            args_model=AppNameArgs,
            risk=Risk.MODERATE,
            handler=close_handler,
            confirm=True,
            category="applications",
        ),
        ToolDefinition(
            name="app.list_running",
            description="List visible windows and running processes.",
            args_model=EmptyArgs,
            risk=Risk.LOW,
            handler=list_running_handler,
            category="applications",
        ),
        ToolDefinition(
            name="app.list_configured",
            description="List configured application shortcuts and whether they are installed.",
            args_model=EmptyArgs,
            risk=Risk.LOW,
            handler=list_configured_handler,
            category="applications",
        ),
    ]
