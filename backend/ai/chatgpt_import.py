"""Local parser for a ChatGPT ``conversations.json`` export.

The export never leaves this machine: the desktop reads the file and posts its
text, this module parses it in memory, and nothing is written to the knowledge
store until the user approves specific entries. Imported rows are always
``source='imported'`` and ``verified=False`` so an old self-description is never
presented as a fact the user re-confirmed today.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

MAX_BYTES = 40_000_000
MAX_FACT_LENGTH = 220
MIN_FACT_LENGTH = 12

# Only sentences where the user describes themselves, their work or their plans
# become candidates. ChatGPT replies are never a source of facts about the user.
STATEMENT_PATTERN = re.compile(
    r"\b(i\s+am|i'?m|my\s+\w+|i\s+have|i\s+want|i\s+need|i\s+will|i\s+plan|"
    r"i\s+study|i\s+live|i\s+use|i\s+prefer|my\s+name|cgpa|semester|gate|"
    r"project|deadline|exam)\b",
    re.I,
)

CATEGORY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("goal", re.compile(r"\b(gate|mttech|m\.?tech|target|aim|apply|admission|iisc|"
                        r"preparation|rank|score)\b", re.I)),
    ("education", re.compile(r"\b(iit|patna|bsms|bs-ms|semester|cgpa|grade|course|"
                             r"lecture|college|university|syllabus)\b", re.I)),
    ("project", re.compile(r"\b(project|repo|github|build|app|prototype|deploy|"
                           r"architecture|codebase)\b", re.I)),
    ("schedule", re.compile(r"\b(class|lab|timetable|schedule|monday|tuesday|wednesday|"
                             r"thursday|friday|deadline|submission)\b", re.I)),
    ("progress", re.compile(r"\b(completed|done|finished|revised|practised|scored|"
                            r"attempted|streak)\b", re.I)),
    ("preference", re.compile(r"\b(preference|prefer|like|dislike|hinglish|language|"
                              r"style|format|explain|short|step-by-step)\b", re.I)),
    ("profile", re.compile(r"\b(name|age|live|city|role|student|aradhya)\b", re.I)),
)


class ImportFormatError(ValueError):
    """Raised when the uploaded file is not a ChatGPT conversations export."""


@dataclass
class Fact:
    id: str
    text: str
    category: str
    conversation: str
    conversation_id: str
    said_at: str | None
    duplicate_of: str | None = None
    conflicts_with: str | None = None
    reason: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "category": self.category,
            "conversation": self.conversation, "conversation_id": self.conversation_id,
            "said_at": self.said_at, "duplicate_of": self.duplicate_of,
            "conflicts_with": self.conflicts_with, "reason": self.reason,
        }


@dataclass
class Conversation:
    id: str
    name: str
    messages: int
    user_messages: int
    started: str | None
    updated: str | None
    excluded: bool = False
    title_only: bool = False

    def as_row(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "messages": self.messages,
            "user_messages": self.user_messages, "started": self.started,
            "updated": self.updated, "excluded": self.excluded,
            "title_only": self.title_only,
        }


@dataclass
class Preview:
    import_id: str
    conversations: list[Conversation] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    created_at: float = 0.0

    def find(self, fact_id: str) -> Fact | None:
        return next((fact for fact in self.facts if fact.id == fact_id), None)


def _stamp(value: Any) -> str | None:
    """ChatGPT writes unix seconds; show a plain local date instead."""
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value)).date().isoformat()
    except (OSError, ValueError, OverflowError):
        return None


def _parts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            return [str(item) for item in parts if isinstance(item, str)]
        if isinstance(parts, str):
            return [parts]
    return []


def _sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    pieces = re.split(r"(?<=[.!?])\s+", cleaned)
    return [piece.strip() for piece in pieces if piece.strip()]


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words[:7]) or "entry"


def _fact_id(text: str) -> str:
    digest = hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"cf_{_slug(text)[:32]}_{digest}"


def _category(text: str) -> str:
    for category, pattern in CATEGORY_RULES:
        if pattern.search(text):
            return category
    return "unverified"


def parse_export(raw: str | bytes) -> tuple[list[Conversation], list[Fact]]:
    """Read the export and pull self-describing user statements out of it."""
    payload = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    if len(payload.encode("utf-8", errors="replace")) > MAX_BYTES:
        raise ImportFormatError(
            f"That file is larger than the {MAX_BYTES // 1_000_000} MB limit for one import."
        )
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ImportFormatError(
            f"The file is not valid JSON ({exc.msg} at line {exc.lineno}). "
            "Upload the 'conversations.json' from the ChatGPT data export."
        ) from exc

    # Some exports wrap the list under 'conversations' instead of being one.
    if isinstance(data, dict):
        data = data.get("conversations") or data.get("history") or []
    if not isinstance(data, list):
        raise ImportFormatError("Expected a JSON array of conversations.")

    conversations: list[Conversation] = []
    facts: list[Fact] = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        conv_id = str(item.get("id") or f"row-{index}")
        name = str(item.get("title") or item.get("name") or "Untitled conversation").strip()
        mapping = item.get("mapping")
        texts: list[tuple[str, str | None]] = []
        if isinstance(mapping, dict):
            for node in mapping.values():
                message = (node or {}).get("message") if isinstance(node, dict) else None
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                author = message.get("author")
                if isinstance(author, dict) and author.get("role") not in (None, "user"):
                    continue
                for part in _parts(message.get("content")):
                    stamp = _stamp(message.get("create_time") or item.get("create_time"))
                    texts.append((part, stamp))
        elif isinstance(item.get("messages"), list):
            for message in item["messages"]:
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                stamp = _stamp(message.get("create_time") or item.get("create_time"))
                for part in _parts(message.get("content")):
                    texts.append((part, stamp))

        if not texts:
            continue
        said_at = texts[0][1]
        conversations.append(Conversation(
            id=conv_id, name=name[:200], messages=len(texts), user_messages=len(texts),
            started=said_at, updated=_stamp(item.get("update_time")),
        ))
        for text, stamp in texts:
            for sentence in _sentences(text):
                if not (MIN_FACT_LENGTH <= len(sentence) <= MAX_FACT_LENGTH):
                    continue
                if not STATEMENT_PATTERN.search(sentence):
                    continue
                facts.append(Fact(
                    id=_fact_id(sentence), text=sentence[:MAX_FACT_LENGTH],
                    category=_category(sentence), conversation=name[:200],
                    conversation_id=conv_id, said_at=stamp,
                ))
    return conversations, dedupe(facts)


def dedupe(facts: list[Fact]) -> list[Fact]:
    """Same sentence twice in an export is one fact, not two."""
    seen: dict[str, Fact] = {}
    unique: list[Fact] = []
    for fact in facts:
        existing = seen.get(fact.id)
        if existing is None:
            seen[fact.id] = fact
            unique.append(fact)
            continue
        existing.reason = "repeated in this export"
    return unique


def cross_check(facts: list[Fact], existing: list[dict[str, Any]]) -> list[str]:
    """Flag repeats of what is already stored, and statements that disagree."""
    notes: list[str] = []
    by_value: dict[str, Fact] = {}
    stored = {str(row.get("value") or "").strip().lower(): row for row in existing}

    for fact in facts:
        needle = fact.text.strip().lower()
        already = stored.get(needle)
        if already is not None:
            fact.duplicate_of = str(already.get("key"))
            fact.reason = f"already stored as '{already.get('key')}'"
            continue
        twin = by_value.get(needle)
        if twin is not None:
            fact.duplicate_of = twin.id
            fact.reason = "same sentence from another conversation"
            continue
        by_value[needle] = fact

    # A contradiction needs the same subject stated two ways: 'my cgpa is 8.1'
    # beside 'my cgpa is 8.74'. Only the second one gets flagged.
    subject_value = re.compile(r"\bmy\s+([a-z][a-z\s]{2,24}?)\s+(?:is|are|=)\s+(.+)$", re.I)
    subjects: dict[str, Fact] = {}
    for fact in facts:
        match = subject_value.search(fact.text)
        if not match:
            continue
        subject = re.sub(r"\s+", " ", match.group(1).lower()).strip()
        value = match.group(2).strip().lower()
        holder = subjects.get(subject)
        if holder is None:
            subjects[subject] = fact
            continue
        if holder.text.lower() == fact.text.lower():
            continue
        if _same_number(holder.text, value):
            continue
        fact.conflicts_with = holder.id
        fact.reason = f"disagrees with '{holder.text}'"

    duplicates = sum(1 for fact in facts if fact.duplicate_of)
    conflicts = sum(1 for fact in facts if fact.conflicts_with)
    if duplicates:
        notes.append(f"{duplicates} candidate(s) repeat something already in memory "
                     "or appear twice in the export.")
    if conflicts:
        notes.append(f"{conflicts} candidate(s) contradict another statement. Approve the "
                     "one you believe; JARVIS will not pick for you.")
    if not notes:
        notes.append("No duplicates or contradictions found among these candidates.")
    return notes


def _same_number(left: str, right_value: str) -> bool:
    """Treat '8.74' and '8.74 out of 10' as the same claim, not a conflict."""
    left_numbers = re.findall(r"\d+(?:\.\d+)?", left)
    right_numbers = re.findall(r"\d+(?:\.\d+)?", right_value)
    if not left_numbers or not right_numbers:
        return False
    return left_numbers[-1] in right_numbers


def make_id() -> str:
    return f"imp_{datetime.now().strftime('%Y%m%d%H%M%S')}_" \
        f"{hashlib.sha1(str(id(object())).encode()).hexdigest()[:6]}"
