"""Safe file operations: previews before bulk changes, no deletions, undo manifests."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

import paths
from security.permissions import Risk
from tools.base import ToolContext, ToolDefinition, ToolError, ToolResult

CATEGORIES: dict[str, set[str]] = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff", ".heic"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".xls", ".xlsx",
                  ".csv", ".ppt", ".pptx", ".epub"},
    "Videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Code": {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".cs",
             ".go", ".rs", ".rb", ".php", ".html", ".css", ".scss", ".json", ".yml",
             ".yaml", ".toml", ".sql", ".sh", ".bat", ".ps1"},
    "Installers": {".exe", ".msi", ".msix", ".appx", ".iso"},
}

def category_for(path: Path) -> str:
    extension = path.suffix.lower()
    for category, extensions in CATEGORIES.items():
        if extension in extensions:
            return category
    return "Other"


def resolve_folder(raw: str) -> Path:
    """Known folders come from the shell, so OneDrive redirection is honoured.

    ``Path.home() / 'Desktop'`` is wrong on this machine, and a tool that opens
    or saves to the wrong Desktop fails silently.
    """
    text = (raw or "").strip()
    if not text:
        return Path.home()
    folder = paths.resolve_folder(text)
    if folder and folder.path:
        return folder.path
    if text.lower() in ("screenshots", "screenshot"):
        return paths.screenshots_dir()
    return Path(os.path.expandvars(text)).expanduser()


def _guarded_folder(ctx: ToolContext, raw: str, write: bool) -> Path:
    folder = resolve_folder(raw)
    if not folder.exists() or not folder.is_dir():
        raise ToolError(f"Folder not found: {folder}")
    resolved = folder.resolve()
    return ctx.policy.ensure_writable(resolved) if write else ctx.policy.ensure_readable(resolved)


def _unique_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    index = 1
    while True:
        candidate = destination.with_name(f"{destination.stem} ({index}){destination.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def scan_folder(folder: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        try:
            if not entry.is_file():
                continue
            files.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "size": entry.stat().st_size,
                    "category": category_for(entry),
                }
            )
        except OSError:
            continue
    return files


def build_organize_plan(folder: Path) -> dict[str, Any]:
    files = scan_folder(folder)
    moves = []
    for item in files:
        destination = _unique_destination(folder / item["category"] / item["name"])
        moves.append(
            {
                "source": item["path"],
                "destination": str(destination),
                "category": item["category"],
                "size": item["size"],
            }
        )
    counts: dict[str, int] = {}
    for move in moves:
        counts[move["category"]] = counts.get(move["category"], 0) + 1
    return {
        "folder": str(folder),
        "file_count": len(moves),
        "categories": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "moves": moves,
        "total_bytes": sum(move["size"] for move in moves),
    }


def apply_organize_plan(folder: Path, ctx: ToolContext) -> dict[str, Any]:
    plan = build_organize_plan(folder)
    manifest_dir = folder / ".jarvis"
    manifest_dir.mkdir(exist_ok=True)
    manifest_path = manifest_dir / f"organize-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    moved: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    for move in plan["moves"]:
        source = Path(move["source"])
        destination = Path(move["destination"])
        try:
            ctx.policy.ensure_writable(source)
            ctx.policy.ensure_writable(destination)
            destination.parent.mkdir(exist_ok=True)
            destination = _unique_destination(destination)
            shutil.move(str(source), str(destination))
            moved.append({"from": str(source), "to": str(destination)})
        except (OSError, shutil.Error) as exc:
            failures.append({"source": str(source), "error": str(exc)})
    manifest_path.write_text(
        json.dumps({"folder": str(folder), "moved": moved, "at": datetime.now().isoformat()},
                   indent=2),
        encoding="utf-8",
    )
    return {
        "folder": str(folder),
        "moved": len(moved),
        "failed": len(failures),
        "failures": failures[:10],
        "manifest": str(manifest_path),
    }


def undo_last_organize(folder: Path) -> dict[str, Any]:
    manifests = sorted((folder / ".jarvis").glob("organize-*.json"))
    if not manifests:
        raise ToolError(f"No organize manifest found in {folder}. Nothing to undo.")
    manifest_path = manifests[-1]
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored, skipped = 0, 0
    for move in reversed(data.get("moved", [])):
        source, destination = Path(move["from"]), Path(move["to"])
        try:
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
                restored += 1
            else:
                skipped += 1
        except OSError:
            skipped += 1
    manifest_path.unlink(missing_ok=True)
    return {"restored": restored, "skipped": skipped, "manifest": str(manifest_path)}


def search_files(folder: Path, query: str, extension: str | None, limit: int) -> list[dict[str, Any]]:
    needle = query.lower().strip()
    ext = extension.lower().strip()
    if ext and not ext.startswith("."):
        ext = "." + ext
    results: list[dict[str, Any]] = []
    scanned = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__", ".venv"}]
        for name in files:
            scanned += 1
            if scanned > 30000 or len(results) >= limit:
                break
            if ext and Path(name).suffix.lower() != ext:
                continue
            if needle and needle not in name.lower():
                continue
            path = Path(root) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            results.append({"name": name, "path": str(path), "size": size})
        if scanned > 30000 or len(results) >= limit:
            break
    results.sort(key=lambda item: item["name"].lower())
    return results


def find_duplicates(folder: Path, min_size_kb: int, max_groups: int = 12) -> list[dict[str, Any]]:
    minimum = min_size_kb * 1024
    by_size: dict[int, list[Path]] = {}
    scanned = 0
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__", ".venv"}]
        for name in files:
            scanned += 1
            if scanned > 20000:
                break
            path = Path(root) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size >= minimum:
                by_size.setdefault(size, []).append(path)
    groups: list[dict[str, Any]] = []
    for size, paths in sorted(by_size.items(), key=lambda kv: -kv[0]):
        if len(paths) < 2:
            continue
        by_hash: dict[str, list[Path]] = {}
        for path in paths[:20]:
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            by_hash.setdefault(digest, []).append(path)
        for digest, matches in by_hash.items():
            if len(matches) > 1:
                groups.append(
                    {
                        "hash": digest[:12],
                        "size": size,
                        "files": [str(p) for p in matches],
                    }
                )
                if len(groups) >= max_groups:
                    return groups
    return groups


def summarize_file(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    size = path.stat().st_size
    if suffix in {".txt", ".md", ".csv", ".json", ".log", ".py", ".js", ".ts", ".html", ".css"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        return {
            "kind": "text",
            "size": size,
            "lines": text.count("\n") + 1,
            "excerpt": text[:1500],
        }
    if suffix == ".pptx":
        from pptx import Presentation

        presentation = Presentation(str(path))
        titles = []
        for slide in presentation.slides:
            for shape in slide.shapes:
                if shape.has_text_frame and shape.text_frame.text.strip():
                    titles.append(shape.text_frame.text.strip().splitlines()[0])
                    break
        return {"kind": "pptx", "size": size, "slides": len(presentation.slides), "outline": titles}
    if suffix in {".pdf", ".doc", ".docx"}:
        return {
            "kind": "unsupported",
            "size": size,
            "message": f"Extracting text from {suffix} requires an optional package that is not installed.",
        }
    return {
        "kind": "binary",
        "size": size,
        "message": f"No text extractor is available for {suffix or 'this file type'}.",
    }


class FolderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str = Field(default="Downloads", max_length=512)


class OrganizeApplyArgs(FolderArgs):
    pass


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str = Field(default="Downloads", max_length=512)
    query: str = Field(default="", max_length=200)
    extension: str | None = Field(default=None, max_length=20)
    limit: int = Field(default=50, ge=1, le=200)


class DuplicatesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str = Field(default="Downloads", max_length=512)
    min_size_kb: int = Field(default=512, ge=1, le=102400)


class MoveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(max_length=1024)
    destination_folder: str = Field(max_length=1024)


class RenameArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=1024)
    new_name: str = Field(min_length=1, max_length=255)


class CreateFolderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent: str = Field(default="", max_length=1024,
                        description="Where to create it. Empty means Documents.")
    name: str = Field(min_length=1, max_length=120)


class PathArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=1024)


async def preview_handler(args: FolderArgs, ctx: ToolContext) -> ToolResult:
    folder = _guarded_folder(ctx, args.folder, write=True)
    plan = await asyncio.to_thread(build_organize_plan, folder)
    if plan["file_count"] == 0:
        return ToolResult.success(f"{folder} has no loose files to organize.", plan=plan)
    breakdown = ", ".join(f"{count} {name.lower()}" for name, count in plan["categories"].items())
    ctx.note(f"Organize preview: {plan['file_count']} files in {folder}")
    return ToolResult.success(
        f"I found {plan['file_count']} loose files in {folder.name} ({breakdown}). "
        "Nothing has been moved yet — review the plan and confirm to apply it.",
        plan=plan,
    )


async def apply_handler(args: OrganizeApplyArgs, ctx: ToolContext) -> ToolResult:
    folder = _guarded_folder(ctx, args.folder, write=True)
    result = await asyncio.to_thread(apply_organize_plan, folder, ctx)
    ctx.audit.record(ctx.actor, "files.organize", Risk.HIGH.value, result, "ok")
    if result["failed"]:
        return ToolResult.failure(
            f"Moved {result['moved']} files, but {result['failed']} could not be moved.",
            **result,
        )
    return ToolResult.success(
        f"Moved {result['moved']} files into category folders in {folder.name}. "
        f"An undo manifest was saved to {result['manifest']}.",
        **result,
    )


async def undo_handler(args: FolderArgs, ctx: ToolContext) -> ToolResult:
    folder = _guarded_folder(ctx, args.folder, write=True)
    result = await asyncio.to_thread(undo_last_organize, folder)
    ctx.audit.record(ctx.actor, "files.undo_organize", Risk.HIGH.value, result, "ok")
    return ToolResult.success(
        f"Restored {result['restored']} files to their original locations "
        f"({result['skipped']} skipped because the original path was occupied).",
        **result,
    )


async def search_handler(args: SearchArgs, ctx: ToolContext) -> ToolResult:
    folder = _guarded_folder(ctx, args.folder, write=False)
    if not args.query and not args.extension:
        return ToolResult.failure("Give me a name or extension to search for.")
    results = await asyncio.to_thread(
        search_files, folder, args.query, args.extension, args.limit
    )
    if not results:
        filters = " ".join(filter(None, [args.query, args.extension]))
        return ToolResult.success(f"No files matching '{filters}' were found in {folder}.", results=[])
    return ToolResult.success(
        f"Found {len(results)} matching files in {folder}.", results=results
    )


async def duplicates_handler(args: DuplicatesArgs, ctx: ToolContext) -> ToolResult:
    folder = _guarded_folder(ctx, args.folder, write=False)
    groups = await asyncio.to_thread(find_duplicates, folder, args.min_size_kb)
    if not groups:
        return ToolResult.success(
            f"No duplicate candidates found in {folder} above {args.min_size_kb} KB.", groups=[]
        )
    wasted = sum(group["size"] * (len(group["files"]) - 1) for group in groups)
    return ToolResult.success(
        f"Found {len(groups)} duplicate groups, roughly {wasted / 1024**2:.1f} MB of extra copies. "
        "JARVIS did not delete anything.",
        groups=groups,
    )


async def move_handler(args: MoveArgs, ctx: ToolContext) -> ToolResult:
    source = Path(os.path.expandvars(args.source)).expanduser()
    ctx.policy.ensure_writable(source)
    if not source.exists():
        return ToolResult.failure(f"Source file not found: {source}")
    destination_folder = Path(os.path.expandvars(args.destination_folder)).expanduser()
    ctx.policy.ensure_writable(destination_folder)
    destination_folder.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination(destination_folder / source.name)
    await asyncio.to_thread(shutil.move, str(source), str(destination))
    return ToolResult.success(f"Moved {source.name} to {destination_folder}.", destination=str(destination))


async def rename_handler(args: RenameArgs, ctx: ToolContext) -> ToolResult:
    path = Path(os.path.expandvars(args.path)).expanduser()
    ctx.policy.ensure_writable(path)
    if not path.exists():
        return ToolResult.failure(f"File not found: {path}")
    target = path.with_name(args.new_name)
    ctx.policy.ensure_writable(target)
    if target.exists():
        return ToolResult.failure(f"{target.name} already exists. Nothing was overwritten.")
    await asyncio.to_thread(path.rename, target)
    return ToolResult.success(f"Renamed {path.name} to {target.name}.", path=str(target))


async def create_folder_handler(args: CreateFolderArgs, ctx: ToolContext) -> ToolResult:
    # "Create a folder named X" rarely names the parent; Documents is the least
    # surprising default, and the reply says so instead of printing a path.
    parent = resolve_folder(args.parent or "documents")
    ctx.policy.ensure_writable(parent)
    target = parent / args.name
    ctx.policy.ensure_writable(target)
    if target.exists():
        return ToolResult.failure(f"{args.name} already exists in {parent.name}.")
    target.mkdir(parents=True)
    ctx.repo.add_artifact("folder", str(target), args.name, ctx.task_id)
    return ToolResult.success(
        f"Created {args.name} in {paths.friendly_target(parent)}.", path=str(target)
    )


async def summarize_handler(args: PathArgs, ctx: ToolContext) -> ToolResult:
    path = Path(os.path.expandvars(args.path)).expanduser()
    ctx.policy.ensure_readable(path)
    if not path.is_file():
        return ToolResult.failure(f"File not found: {path}")
    info = await asyncio.to_thread(summarize_file, path)
    if info["kind"] == "text":
        summary = (
            f"{path.name} is a text file with {info['lines']} lines. Preview:\n"
            f"{info['excerpt'][:900]}"
        )
    elif info["kind"] == "pptx":
        summary = (
            f"{path.name} has {info['slides']} slides. Outline: "
            + " | ".join(info["outline"][:10])
        )
    else:
        summary = info["message"]
    return ToolResult.success(summary, file=info, path=str(path))


class OpenFolderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder: str = Field(description="Known folder name or path, e.g. 'Downloads' or 'screenshots'.")


async def open_folder_handler(args: OpenFolderArgs, ctx: ToolContext) -> ToolResult:
    folder = resolve_folder(args.folder)
    if not folder.exists():
        return ToolResult.failure(
            f"The {args.folder} folder does not exist at {folder}.",
            hint="Check the folder name, or create it first.",
        )
    opened, message = await asyncio.to_thread(paths.open_path, folder)
    if not opened:
        return ToolResult.failure(message)
    label = (paths.resolve_folder(args.folder).label if paths.resolve_folder(args.folder) else "") \
        or folder.name
    ctx.repo.add_artifact("folder", str(folder), label, ctx.task_id)
    # The user asked to open a folder, not to hear its absolute path read out.
    return ToolResult.success(f"{label} opened.", path=str(folder), label=label)


class OpenRecentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(default="any", description="'screenshot', 'document', 'image' or 'any'.")
    hint: str = Field(default="", description="Optional name fragment to match.")
    within_days: int = Field(default=7, ge=1, le=365)


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_DOC_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".csv"}


def _candidate_roots(ctx: ToolContext) -> list[Path]:
    roots: list[Path] = []
    for key in ("screenshots", "downloads", "documents", "desktop"):
        folder = resolve_folder(key)
        if folder.exists():
            roots.append(folder)
    roots.append(ctx.settings.output_dir)
    return list(dict.fromkeys(roots))


def _newest_match(roots: list[Path], suffixes: set[str], hint: str, within_days: int) -> list[Path]:
    cutoff = datetime.now().timestamp() - within_days * 86400
    matches: list[Path] = []
    for root in roots:
        try:
            entries = sorted(root.glob("*"), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file() or entry.suffix.lower() not in suffixes:
                    continue
                if entry.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            if hint and hint.lower() not in entry.name.lower():
                continue
            matches.append(entry)
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches


async def open_recent_handler(args: OpenRecentArgs, ctx: ToolContext) -> ToolResult:
    kind = (args.kind or "any").strip().lower()
    suffixes = (
        _IMAGE_SUFFIXES if kind in ("screenshot", "image")
        else _DOC_SUFFIXES if kind == "document"
        else _IMAGE_SUFFIXES | _DOC_SUFFIXES
    )

    recorded = ctx.repo.latest_artifact("screenshot" if kind == "screenshot" else None)
    recorded_path = Path(recorded["path"]) if recorded and recorded.get("path") else None
    if recorded_path and recorded_path.exists() and recorded_path.suffix.lower() in suffixes:
        opened, message = await asyncio.to_thread(paths.open_path, recorded_path)
        if opened:
            return ToolResult.success(
                f"Opened {paths.friendly_target(recorded_path)}.",
                path=str(recorded_path),
            )
        return ToolResult.failure(message)

    # Nothing was recorded, so the file predates JARVIS or came from elsewhere:
    # search the handful of folders that can plausibly hold it, never the disk.
    matches = await asyncio.to_thread(
        _newest_match, _candidate_roots(ctx), suffixes, args.hint.strip(), args.within_days
    )
    if not matches:
        return ToolResult.failure(
            f"I could not find a matching file from the last {args.within_days} days in "
            "Screenshots, Downloads, Documents, Desktop or the JARVIS output folder.",
            hint="Give me part of the name and I will search again.",
        )
    if len(matches) > 3:
        listing = ", ".join(path.name for path in matches[:5])
        return ToolResult.success(
            "Several recent files match. Tell me which one: " + listing,
            candidates=[str(path) for path in matches[:8]],
            needs_choice=True,
        )
    target = matches[0]
    opened, message = await asyncio.to_thread(paths.open_path, target)
    if not opened:
        return ToolResult.failure(message)
    return ToolResult.success(f"Opened {target.name}.", path=str(target))


class LocateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reveal: bool = Field(default=True, description="Open the containing folder in Explorer.")


async def locate_last_handler(args: LocateArgs, ctx: ToolContext) -> ToolResult:
    """Answers 'where did you save it?' — the only time a path is offered."""
    recorded = ctx.repo.latest_artifact()
    path = Path(recorded["path"]) if recorded and recorded.get("path") else None
    if path is None or not path.exists():
        return ToolResult.failure(
            "I have no saved file to point at yet. Take a screenshot or generate a "
            "document first.",
        )
    friendly = paths.friendly_target(path)
    if not args.reveal:
        return ToolResult.success(f"{path.name} is in {friendly}.", path=str(path))
    shown, message = await asyncio.to_thread(paths.reveal_path, path)
    summary = f"{path.name} is in {friendly}." + (f" {message}" if shown else "")
    return ToolResult.success(summary, path=str(path), revealed=shown)


def build() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="files.organize_preview",
            description="Scan a folder and propose a category-based move plan without moving anything.",
            args_model=FolderArgs,
            risk=Risk.LOW,
            handler=preview_handler,
            category="files",
        ),
        ToolDefinition(
            name="files.organize_apply",
            description="Apply the category-based move plan to a folder (bulk file moves).",
            args_model=OrganizeApplyArgs,
            risk=Risk.HIGH,
            handler=apply_handler,
            category="files",
        ),
        ToolDefinition(
            name="files.undo_organize",
            description="Undo the most recent organize operation in a folder.",
            args_model=FolderArgs,
            risk=Risk.HIGH,
            handler=undo_handler,
            category="files",
        ),
        ToolDefinition(
            name="files.search",
            description="Search files by name fragment or extension inside an approved folder.",
            args_model=SearchArgs,
            risk=Risk.LOW,
            handler=search_handler,
            category="files",
        ),
        ToolDefinition(
            name="files.duplicates",
            description="Find duplicate file candidates by content hash (never deletes).",
            args_model=DuplicatesArgs,
            risk=Risk.LOW,
            handler=duplicates_handler,
            category="files",
        ),
        ToolDefinition(
            name="files.move",
            description="Move one file to another approved folder.",
            args_model=MoveArgs,
            risk=Risk.MODERATE,
            handler=move_handler,
            category="files",
        ),
        ToolDefinition(
            name="files.rename",
            description="Rename one file without overwriting anything.",
            args_model=RenameArgs,
            risk=Risk.MODERATE,
            handler=rename_handler,
            category="files",
        ),
        ToolDefinition(
            name="files.create_folder",
            description="Create a new folder inside an approved location.",
            args_model=CreateFolderArgs,
            risk=Risk.MODERATE,
            handler=create_folder_handler,
            category="files",
        ),
        ToolDefinition(
            name="files.summarize",
            description="Summarize a text or PowerPoint file that JARVIS is allowed to read.",
            args_model=PathArgs,
            risk=Risk.LOW,
            handler=summarize_handler,
            category="files",
        ),
        ToolDefinition(
            name="files.open_folder",
            description="Open a Windows known folder (Downloads, Desktop, Documents, "
                        "Pictures, Screenshots) or a given path in File Explorer.",
            args_model=OpenFolderArgs,
            risk=Risk.LOW,
            handler=open_folder_handler,
            confirm=False,
            category="files",
        ),
        ToolDefinition(
            name="files.open_recent",
            description="Open the most recent matching file, e.g. the latest screenshot.",
            args_model=OpenRecentArgs,
            risk=Risk.LOW,
            handler=open_recent_handler,
            confirm=False,
            category="files",
        ),
        ToolDefinition(
            name="files.locate_last",
            description="Say where the file JARVIS last saved is, and show it in Explorer.",
            args_model=LocateArgs,
            risk=Risk.LOW,
            handler=locate_last_handler,
            confirm=False,
            category="files",
        ),
    ]
