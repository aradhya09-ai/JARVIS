"""Personal data endpoints: knowledge, sites and reminders.

Every one of these is a plain read or write against the local SQLite database.
Nothing here contacts a cloud service, and nothing here pretends an
integration (Google Calendar, Outlook) exists.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import re
import time

from fastapi import APIRouter, Depends, HTTPException

from ai.chatgpt_import import (
    ImportFormatError,
    Preview,
    cross_check,
    make_id,
    parse_export,
)
from api.deps import get_services
from api.schemas import (
    ImportApprove,
    ImportPreview,
    KnowledgePatch,
    KnowledgeUpload,
    KnowledgeWrite,
    ReminderWrite,
    SiteWrite,
)
from database.repository import KNOWLEDGE_CATEGORIES, SOURCES
from services import Services

router = APIRouter(prefix="/api", tags=["personal"])

REMINDER_NOTE = (
    "JARVIS checks reminders every 15 seconds while the backend is running. There is "
    "no Windows background task, so a reminder due with the app closed is delivered at "
    "the next start and marked 'late' instead of pretending it fired on time."
)


def _now() -> datetime:
    return datetime.now()


def _parse_iso(raw: str) -> datetime:
    """Parse a user-supplied instant and drop the zone.

    Stored times are naive local times throughout this app, so a zone-aware
    input is converted to local first rather than compared blindly.
    """
    text = raw.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Use an ISO date-time such as 2026-11-04T09:00 or 2026-11-04 09:00.",
        ) from exc
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


# ------------------------------------------------------------------ knowledge
@router.get("/knowledge")
async def list_knowledge(
    category: str | None = None, services: Services = Depends(get_services)
) -> dict:
    items = services.repo.list_knowledge(category=category)
    return {
        "categories": list(KNOWLEDGE_CATEGORIES),
        "sources": list(SOURCES),
        "items": items,
        "counts": _counts(items),
        "note": (
            "verified=False means JARVIS was told this once or inferred it. "
            "Confirm it before relying on it."
        ),
    }


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for item in items:
        key = str(item.get("category") or "unverified")
        tally[key] = tally.get(key, 0) + 1
    return tally


@router.post("/knowledge")
async def write_knowledge(payload: KnowledgeWrite,
                          services: Services = Depends(get_services)) -> dict:
    if payload.category not in KNOWLEDGE_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Category must be one of: {', '.join(KNOWLEDGE_CATEGORIES)}.",
        )
    if payload.source not in SOURCES:
        raise HTTPException(
            status_code=400, detail=f"Source must be one of: {', '.join(SOURCES)}."
        )
    item = services.repo.remember(
        payload.key, payload.value,
        category=payload.category, source=payload.source, verified=payload.verified,
        as_of=payload.as_of, note=payload.note,
    )
    services.audit.record("user", "knowledge.write", "low",
                          {"key": payload.key, "category": payload.category}, "ok")
    return {"item": item}


# Formats the browser can read as text. Binary documents (PDF, DOCX, images)
# are refused here rather than stored as mojibake; the file tools explain what
# an optional parser would unlock.
TEXT_UPLOAD_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".html", ".htm",
    ".xml", ".ini", ".yaml", ".yml", ".srt", ".sql", ".py", ".js", ".ts", ".rst",
}
STORED_TEXT_LIMIT = 60_000


def _memory_key(filename: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", filename.lower()).strip("-")[:48]
    return f"doc.{slug or 'upload'}"


@router.post("/knowledge/upload")
async def upload_knowledge(payload: KnowledgeUpload,
                           services: Services = Depends(get_services)) -> dict:
    """Remember a document the user hands over, for the lifetime of the database."""
    if payload.category not in KNOWLEDGE_CATEGORIES:
        raise HTTPException(status_code=400,
                            detail=f"Category must be one of: {', '.join(KNOWLEDGE_CATEGORIES)}.")
    filename = payload.filename.strip() or "untitled"
    suffix = Path(filename).suffix.lower()
    if suffix and suffix not in TEXT_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"JARVIS cannot read {suffix or 'this'} yet. Text files (.txt, .md, .csv, "
                ".json, .log, code) save directly; PDF, DOCX and images need an optional "
                "parser that is not installed. Paste the text instead."
            ),
        )
    text = payload.content.strip()
    if not text:
        raise HTTPException(status_code=400, detail="That file has no text to remember.")
    truncated = len(text) > STORED_TEXT_LIMIT
    if truncated:
        text = text[:STORED_TEXT_LIMIT]
    note = f"Uploaded from {filename} ({len(payload.content):,} characters)."
    if truncated:
        note += f" Stored the first {STORED_TEXT_LIMIT:,} characters."
    item = services.repo.remember(
        _memory_key(filename), text,
        category=payload.category, source="imported", verified=False, note=note,
    )
    services.audit.record("user", "knowledge.upload", "low",
                          {"key": item["key"], "chars": len(text)}, "ok")
    return {"item": item, "truncated": truncated}


@router.patch("/knowledge/{key}")
async def patch_knowledge(key: str, payload: KnowledgePatch,
                          services: Services = Depends(get_services)) -> dict:
    if services.repo.get_knowledge(key) is None:
        raise HTTPException(status_code=404, detail=f"No knowledge entry named '{key}'.")
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if fields.get("category") and fields["category"] not in KNOWLEDGE_CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category.")
    if fields.get("source") and fields["source"] not in SOURCES:
        raise HTTPException(status_code=400, detail="Unknown source.")
    item = services.repo.update_knowledge(key, **fields)
    services.audit.record("user", "knowledge.update", "low", {"key": key}, "ok")
    return {"item": item}


@router.delete("/knowledge/{key}")
async def remove_knowledge(key: str, services: Services = Depends(get_services)) -> dict:
    if not services.repo.delete_knowledge(key):
        raise HTTPException(status_code=404, detail=f"No knowledge entry named '{key}'.")
    services.audit.record("user", "knowledge.delete", "moderate", {"key": key}, "ok")
    return {"deleted": True}


# ---------------------------------------------------------------------- sites
@router.get("/sites")
async def list_sites(services: Services = Depends(get_services)) -> dict:
    return {"sites": services.repo.list_site_aliases()}


@router.put("/sites")
async def save_site(payload: SiteWrite, services: Services = Depends(get_services)) -> dict:
    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400,
                            detail="The address must start with http:// or https://.")
    name = payload.name.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="A site needs a name.")
    existing = {row["name"].lower() for row in services.repo.list_site_aliases()}
    services.repo.save_site_alias(name, url, [a.strip().lower() for a in payload.aliases if a.strip()])
    services.audit.record("user", "site.save", "low", {"name": name}, "ok")
    return {"saved": True, "site": {"name": name, "url": url},
            "was_new": name not in existing}


@router.delete("/sites/{name}")
async def delete_site(name: str, services: Services = Depends(get_services)) -> dict:
    if not services.repo.delete_site_alias(name.strip().lower()):
        raise HTTPException(
            status_code=409,
            detail="Built-in sites stay in the list so a typo cannot remove them. "
                   "Rename or edit them instead.",
        )
    services.audit.record("user", "site.delete", "low", {"name": name}, "ok")
    return {"deleted": True}


# ----------------------------------------------------------------- reminders
@router.get("/reminders")
async def list_reminders(include_finished: bool = False,
                         services: Services = Depends(get_services)) -> dict:
    return {
        "reminders": services.repo.list_reminders(include_finished),
        "note": REMINDER_NOTE,
    }


@router.post("/reminders")
async def add_reminder(payload: ReminderWrite,
                       services: Services = Depends(get_services)) -> dict:
    if payload.minutes is None and not payload.due_at.strip():
        raise HTTPException(status_code=400,
                            detail="A reminder needs a time or a number of minutes.")
    moment = (_now() + timedelta(minutes=max(1, payload.minutes))
              if payload.minutes is not None else _parse_iso(payload.due_at))
    if moment <= _now():
        raise HTTPException(
            status_code=400,
            detail="That time is already past. Give a future time, or say 'in 10 minutes'.",
        )
    reminder = services.repo.add_reminder(
        payload.text, moment.isoformat(timespec="seconds"),
        speak=payload.speak, source=payload.source,
    )
    services.audit.record("user", "reminder.add", "low", {"id": reminder["id"]}, "ok")
    return {"reminder": reminder, "note": REMINDER_NOTE}


@router.delete("/reminders/{reminder_id}")
async def cancel_reminder(reminder_id: int,
                          services: Services = Depends(get_services)) -> dict:
    if not services.repo.cancel_reminder(reminder_id):
        raise HTTPException(status_code=404, detail="No such reminder.")
    services.audit.record("user", "reminder.cancel", "low", {"id": reminder_id}, "ok")
    return {"cancelled": True}


@router.post("/reminders/{reminder_id}/test")
async def test_reminder(reminder_id: int,
                        services: Services = Depends(get_services)) -> dict:
    """Fire one reminder now so the notification-and-voice path can be checked."""
    payload = await services.reminders.deliver_now(reminder_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="No such reminder.")
    services.audit.record("user", "reminder.test", "low", {"id": reminder_id}, "ok")
    return {"delivered": payload, "note": "This was a test; the real due time is unchanged."}


# ------------------------------------------------------- ChatGPT export import
# A preview is held in memory only, until it is approved or times out. Nothing
# the model wrote counts: only user turns become candidates, and each approved
# row is stored unverified so an old self-description is not treated as current.
_PENDING_IMPORTS: dict[str, Any] = {}
_IMPORT_TTL_SECONDS = 1800
_IMPORT_CACHE_LIMIT = 8


def _prune_imports() -> None:
    cutoff = time.time() - _IMPORT_TTL_SECONDS
    for stale in [key for key, value in _PENDING_IMPORTS.items() if value.created_at < cutoff]:
        _PENDING_IMPORTS.pop(stale, None)


@router.post("/import/chatgpt/preview")
async def preview_import(payload: ImportPreview,
                        services: Services = Depends(get_services)) -> dict:
    _prune_imports()
    try:
        conversations, facts = parse_export(payload.raw)
    except ImportFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    notes = cross_check(facts, services.repo.list_knowledge())
    import_id = make_id()
    if len(_PENDING_IMPORTS) >= _IMPORT_CACHE_LIMIT:
        _PENDING_IMPORTS.pop(next(iter(_PENDING_IMPORTS)), None)
    preview = Preview(import_id=import_id, conversations=conversations,
                      facts=facts, notes=notes, created_at=time.time())
    _PENDING_IMPORTS[import_id] = preview
    services.audit.record("user", "import.chatgpt.preview", "low",
                          {"conversations": len(conversations), "candidates": len(facts)}, "ok")
    return {
        "import_id": import_id,
        "conversations": [row.as_row() for row in conversations],
        "candidates": [fact.as_row() for fact in facts],
        "notes": notes + [
            "Nothing has been stored yet. Choose which entries to approve.",
            "Approved rows are saved as source=imported and verified=false.",
            "Only your own messages are read; ChatGPT replies are never a fact about you.",
        ],
        "expires_in": _IMPORT_TTL_SECONDS,
    }


@router.post("/import/chatgpt/approve")
async def approve_import(payload: ImportApprove,
                         services: Services = Depends(get_services)) -> dict:
    _prune_imports()
    preview = _PENDING_IMPORTS.get(payload.import_id)
    if preview is None:
        raise HTTPException(
            status_code=404,
            detail="That preview has expired or was never created. Run the preview again; "
                   "JARVIS does not keep an import waiting indefinitely.",
        )
    skip = set(payload.excluded)
    wanted = set(payload.fact_ids)
    stored: list[dict[str, Any]] = []
    skipped_duplicate = 0
    for fact in preview.facts:
        if fact.id not in wanted or fact.conversation_id in skip:
            continue
        if fact.duplicate_of:
            skipped_duplicate += 1
            continue
        services.repo.remember(
            f"imported.{fact.id}", fact.text,
            category=fact.category, source="imported", verified=False,
            as_of=fact.said_at,
            note=f"From ChatGPT export, conversation '{fact.conversation}'"
                 f"{'. Conflicts with another statement' if fact.conflicts_with else ''}.",
        )
        stored.append(fact.as_row())
    services.audit.record("user", "import.chatgpt.approve", "moderate",
                          {"approved": len(stored), "excluded_conversations": len(skip)}, "ok")
    _PENDING_IMPORTS.pop(payload.import_id, None)
    return {
        "stored": len(stored),
        "items": stored,
        "duplicates_not_written": skipped_duplicate,
        "conflicts": sum(1 for row in stored if row["conflicts_with"]),
        "note": "Stored as unverified. Confirm each one you stand by and it becomes verified.",
    }


@router.delete("/import/chatgpt")
async def remove_imported(services: Services = Depends(get_services)) -> dict:
    """Delete everything that came from an export, leaving typed facts alone."""
    rows = services.repo.list_knowledge(source="imported")
    for row in rows:
        services.repo.delete_knowledge(str(row["key"]))
    services.audit.record("user", "import.chatgpt.delete", "high",
                          {"removed": len(rows)}, "ok")
    return {
        "deleted": len(rows),
        "note": "Only rows with source='imported' were removed. Facts you typed yourself stay.",
    }
