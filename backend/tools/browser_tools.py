"""Browser, website-alias and desktop-shortcut helpers.

JARVIS can open search and product pages reliably. It cannot read the
results back without a search API or scraping, so it never pretends to have
summarised pages it did not load.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from pydantic import BaseModel, ConfigDict, Field

import paths
from security.permissions import Risk
from tools.application_launcher import _registry_app_path, normalize, scan_shortcuts
from tools.base import ToolContext, ToolDefinition, ToolError, ToolResult


class BrowserArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    browser: str = Field(default="", max_length=40,
                         description="chrome, edge, firefox or brave. Empty uses the default browser.")


class AmazonArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    max_price: int | None = Field(default=None, ge=1, le=10_000_000,
                                  description="Optional budget in rupees, added to the search query")
    open_page: bool = True


class MapsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    mode: str = Field(default="driving", pattern="^(driving|walking|bicycling|transit)$")


class OpenUrlArgs(BrowserArgs):
    url: str = Field(min_length=1, max_length=2048)


def _open(url: str) -> bool:
    return webbrowser.open(url, new=2)


def _validate_url(raw: str) -> str:
    candidate = raw.strip()
    if not candidate.startswith(("http://", "https://")):
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ToolError("That does not look like a valid web address.")
    return candidate


async def amazon_handler(args: AmazonArgs, ctx: ToolContext) -> ToolResult:
    query = args.query.strip()
    if args.max_price:
        query = f"{query} under {args.max_price} rupees"
    url = "https://www.amazon.in/s?k=" + quote_plus(query)
    if args.open_page:
        opened = await asyncio.to_thread(_open, url)
        if not opened:
            return ToolResult.failure("Windows did not report a default browser.")
    ctx.audit.record(ctx.actor, "browser.amazon", Risk.LOW.value,
                     {"query": args.query, "max_price": args.max_price}, "ok")
    return ToolResult.success(
        f"Opened Amazon search results for '{query}'. I did not scrape prices, ratings "
        "or availability, so I cannot compare listings or place an order.",
        url=url, compared=False,
    )


async def maps_handler(args: MapsArgs, ctx: ToolContext) -> ToolResult:
    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={quote_plus(args.origin)}"
        f"&destination={quote_plus(args.destination)}"
        f"&travelmode={args.mode}"
    )
    opened = await asyncio.to_thread(_open, url)
    if not opened:
        return ToolResult.failure("Windows did not report a default browser.")
    ctx.audit.record(ctx.actor, "browser.maps", Risk.LOW.value,
                     {"origin": args.origin, "destination": args.destination}, "ok")
    return ToolResult.success(
        f"Opened Google Maps directions from {args.origin} to {args.destination}. "
        "Live traffic and route details require a Maps API key, so I did not invent them.",
        url=url,
    )


async def open_url_handler(args: OpenUrlArgs, ctx: ToolContext) -> ToolResult:
    url = _validate_url(args.url)
    opened, label, error = await asyncio.to_thread(_open_in_browser, url, args.browser)
    if not opened:
        return ToolResult.failure(error)
    ctx.audit.record(ctx.actor, "browser.open", Risk.LOW.value,
                     {"url": url, "browser": label}, "ok")
    return ToolResult.success(f"Opened {url} in {label}.", url=url, browser=label)


BROWSERS: dict[str, tuple[str, ...]] = {
    "chrome": ("chrome.exe",),
    "google chrome": ("chrome.exe",),
    "edge": ("msedge.exe",),
    "microsoft edge": ("msedge.exe",),
    "firefox": ("firefox.exe",),
    "mozilla firefox": ("firefox.exe",),
    "brave": ("brave.exe",),
}


def resolve_browser(name: str) -> tuple[str, Path] | None:
    """Find a browser executable by the name the user used."""
    key = normalize(name)
    if not key:
        return None
    exes = BROWSERS.get(key)
    if exes is None:
        for browser_key, candidates in BROWSERS.items():
            if key in browser_key or browser_key in key:
                exes = candidates
                break
    if not exes:
        return None
    for exe in exes:
        found = _registry_app_path(exe)
        if found:
            return exe, found
        which = shutil.which(exe)
        if which:
            return exe, Path(which)
        for base in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            if not base:
                continue
            candidate = Path(base) / exe
            if candidate.exists():
                return exe, candidate
            candidate = Path(base) / "Google" / "Chrome" / exe
            if candidate.exists():
                return exe, candidate
    return None


def _open_in_browser(url: str, browser: str) -> tuple[bool, str, str]:
    """Returns (opened, browser_label, error)."""
    if not browser.strip():
        opened = webbrowser.open(url, new=2)
        return (opened, "default browser",
                "" if opened else "Windows did not report a default browser.")
    resolved = resolve_browser(browser)
    if resolved is None:
        return False, browser, (
            f"{browser} does not appear to be installed, so I did not open anything in it. "
            "Try another browser, or let me use the default."
        )
    exe, path = resolved
    try:
        subprocess.Popen([str(path), url], close_fds=True)
        return True, exe, ""
    except OSError as exc:
        return False, exe, f"Windows refused to start {exe}: {exc.strerror or exc}"


def _search_url(query: str, engine: str) -> str:
    engine_key = normalize(engine or "google")
    if "youtube" in engine_key:
        return "https://www.youtube.com/results?search_query=" + quote_plus(query)
    if "amazon" in engine_key:
        return "https://www.amazon.in/s?k=" + quote_plus(query)
    if "github" in engine_key:
        return "https://github.com/search?q=" + quote_plus(query)
    if "google" in engine_key or not engine_key:
        return "https://www.google.com/search?q=" + quote_plus(query)
    return "https://www.google.com/search?q=" + quote_plus(f"{engine_key} {query}")


def find_alias(ctx: ToolContext, name: str) -> dict[str, str] | None:
    """Match a spoken site name against the user-editable alias table."""
    wanted = normalize(name)
    if not wanted:
        return None
    best: tuple[int, dict[str, str]] | None = None
    for row in ctx.repo.list_site_aliases():
        candidates = [normalize(row["name"])] + [
            normalize(alias) for alias in row.get("aliases", [])
        ]
        if wanted in candidates:
            return {"name": row["name"], "url": row["url"]}
        for candidate in candidates:
            if candidate and (wanted in candidate or candidate in wanted):
                score = len(candidate)
                if best is None or score > best[0]:
                    best = (score, {"name": row["name"], "url": row["url"]})
    return best[1] if best else None


class SearchInArgs(BrowserArgs):
    query: str = Field(min_length=1, max_length=300)
    engine: str = Field(default="google", max_length=40,
                        description="google, youtube, amazon or github")


async def search_in_handler(args: SearchInArgs, ctx: ToolContext) -> ToolResult:
    url = _search_url(args.query, args.engine)
    opened, label, error = await asyncio.to_thread(_open_in_browser, url, args.browser)
    if not opened:
        return ToolResult.failure(error)
    ctx.audit.record(ctx.actor, "browser.search", Risk.LOW.value,
                     {"query": args.query, "engine": args.engine, "browser": label}, "ok")
    engine_name = normalize(args.engine or "google")
    return ToolResult.success(
        f"Searching {engine_name} for '{args.query}' in {label}. "
        "I cannot read the results back without a search API, so I will not summarise them "
        "unless you paste them here.",
        url=url, browser=label, results_readable=False,
    )


class OpenSiteArgs(BrowserArgs):
    name: str = Field(min_length=1, max_length=120,
                      description="Site or alias, e.g. 'instagram' or 'college portal'.")
    query: str = Field(default="", max_length=200,
                       description="Optional search to run once the site is open.")


async def open_site_handler(args: OpenSiteArgs, ctx: ToolContext) -> ToolResult:
    alias = find_alias(ctx, args.name)
    if alias is None:
        return ToolResult.failure(
            f"'{args.name}' is not in the saved site list, so nothing was opened. "
            "Add it under Settings > Sites, or give me the full web address.",
            known=[row["name"] for row in ctx.repo.list_site_aliases()],
        )
    url = _validate_url(alias["url"])
    if args.query.strip():
        host = urlparse(url).netloc.lower()
        if "youtube" in host:
            url = "https://www.youtube.com/results?search_query=" + quote_plus(args.query)
        else:
            url = "https://www.google.com/search?q=" + quote_plus(f"site:{host} {args.query}")
    opened, label, error = await asyncio.to_thread(_open_in_browser, url, args.browser)
    if not opened:
        return ToolResult.failure(error)
    ctx.audit.record(ctx.actor, "browser.open_site", Risk.LOW.value,
                     {"site": alias["name"], "browser": label}, "ok")
    return ToolResult.success(
        f"Opening {alias['name']} in {label}.", url=url, site=alias["name"], browser=label,
    )


class ShortcutArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


async def shortcut_handler(args: ShortcutArgs, ctx: ToolContext) -> ToolResult:
    """Open a Desktop or Start Menu shortcut by name.

    The target of a .lnk is unknown to JARVIS until Windows resolves it, so this
    tool asks for confirmation rather than launching an unverified program.
    """
    hits = await asyncio.to_thread(scan_shortcuts, args.name)
    if not hits:
        alias = find_alias(ctx, args.name)
        suggestion = (
            f" There is a saved website called {alias['name']} ({alias['url']}); "
            "say 'open " + alias["name"] + "' and I will open that instead."
            if alias else ""
        )
        return ToolResult.failure(
            f"No shortcut matching '{args.name}' was found on the Desktop or in the "
            f"Start Menu.{suggestion} I only looked in those folders, not the whole disk.",
            found=False,
        )
    if len(hits) > 1:
        names = ", ".join(path.stem for path in hits[:6])
        return ToolResult.success(
            f"Several shortcuts match '{args.name}': {names}. Tell me which one to open.",
            candidates=[str(path) for path in hits[:6]],
            needs_choice=True,
            found=True,
        )
    target = hits[0]
    opened, message = await asyncio.to_thread(paths.open_path, target)
    if not opened:
        return ToolResult.failure(message)
    ctx.audit.record(ctx.actor, "shortcut.open", Risk.MODERATE.value,
                     {"shortcut": str(target)}, "ok")
    return ToolResult.success(f"Opened {target.stem}.", path=str(target))


def build() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="browser.search",
            description="Open a search in Google, YouTube, Amazon or GitHub, optionally in a "
                        "named browser. Does not read the results back.",
            args_model=SearchInArgs,
            risk=Risk.LOW,
            handler=search_in_handler,
            confirm=False,
            category="browser",
        ),
        ToolDefinition(
            name="browser.open_site",
            description="Open a saved website alias (Instagram, college portal, ...) in a "
                        "specific browser, optionally running a search on that site.",
            args_model=OpenSiteArgs,
            risk=Risk.LOW,
            handler=open_site_handler,
            confirm=False,
            category="browser",
        ),
        ToolDefinition(
            name="shortcuts.open",
            description="Find and open a Desktop or Start Menu shortcut by name.",
            args_model=ShortcutArgs,
            risk=Risk.MODERATE,
            handler=shortcut_handler,
            category="browser",
        ),
        ToolDefinition(
            name="browser.amazon",
            description="Open Amazon search results, optionally including a budget in rupees.",
            args_model=AmazonArgs,
            risk=Risk.LOW,
            handler=amazon_handler,
            category="browser",
        ),
        ToolDefinition(
            name="browser.maps",
            description="Open Google Maps directions between two places.",
            args_model=MapsArgs,
            risk=Risk.LOW,
            handler=maps_handler,
            category="browser",
        ),
        ToolDefinition(
            name="browser.open_url",
            description="Open a specific http(s) URL in the default browser.",
            args_model=OpenUrlArgs,
            risk=Risk.LOW,
            handler=open_url_handler,
            category="browser",
        ),
    ]
