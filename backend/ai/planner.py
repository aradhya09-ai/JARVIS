"""Intent planning: deterministic command grammar first, local/cloud model second.

The model only proposes a tool name and arguments. The executor independently
validates arguments and enforces risk-based confirmations, so a model can never
bypass the permission layer.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from ai.model_router import ModelUnavailable, ModelRouter, SYSTEM_PROMPT, clock_context
from config import Settings
from database.repository import Repository
from tools.base import ToolRegistry, ToolValidationError

PlanKind = Literal["tool", "reply"]

WAKE_WORD = re.compile(r"^\s*(?:(?:hey|ok|okay|hi|hello)\s+)?jarvis\b[\s,.:;!-]*", re.I)

# The engine sometimes hands back the end of the previous sentence in front of
# the new one, so an address word spoken mid-stream is where the command starts.
_WAKE_RESUME = re.compile(r"(?:hey|ok|okay|hi|hello)\s+jarvis\b[\s,.:;!?-]*", re.I)

GENERIC_HOST_LABELS = {"www", "com", "co", "in", "org", "net", "gov", "edu"}

# "remember this" with nothing after it is a request to be asked, not a fact.
_BARE_MEMORY_POINTERS = {"this", "that", "it", "thisthing", "thatthing", "yeh", "ye", "thisis"}

FOLDERS = {
    "downloads": "Downloads", "download": "Downloads",
    "desktop": "Desktop", "documents": "Documents", "docs": "Documents",
    "pictures": "Pictures", "photos": "Pictures", "music": "Music",
    "videos": "Videos",
}

SITES = {
    "amazon": "https://www.amazon.in",
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "chatgpt": "https://chatgpt.com",
    "google": "https://www.google.com",
    "maps": "https://www.google.com/maps",
    "google maps": "https://www.google.com/maps",
    "netflix": "https://www.netflix.com",
    "linkedin": "https://www.linkedin.com",
    "stack overflow": "https://stackoverflow.com",
}

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

# Spoken browser names mapped to the executable keys tools/browser_tools knows.
BROWSER_WORDS = {
    "google chrome": "chrome", "chrome": "chrome",
    "microsoft edge": "edge", "msedge": "edge", "ms edge": "edge", "edge": "edge",
    "mozilla firefox": "firefox", "firefox": "firefox",
    "brave browser": "brave", "brave": "brave",
}
BROWSER_PHRASES = sorted(BROWSER_WORDS, key=len, reverse=True)
# These words are also ordinary nouns, so they only count when nothing follows.
AMBIGUOUS_BROWSER_WORDS = {"edge", "brave"}

SEARCH_ENGINES = {"youtube": "youtube", "amazon": "amazon", "github": "github",
                  "google": "google", "bing": "bing"}
ENGINE_PHRASES = sorted(SEARCH_ENGINES, key=len, reverse=True)

# Which app to use and where to look only tune an action; they never make a
# second clause ask for something the fused plan left out. The engines that are
# also destinations (YouTube, Amazon, GitHub) stay objects.
MODIFIER_WORDS = set(BROWSER_WORDS) | set(BROWSER_WORDS.values()) | {"google", "bing"}

# Boundaries between back-to-back commands, including the Hinglish conjunctions.
CLAUSE_SPLIT = re.compile(
    r"\s*(?:and\s+then|and\s+ke\s+baad|then|,|;|\band\b|\baur\b|\bphir\b|\bplus\b)\s*", re.I)
CLAUSE_FILLER = re.compile(r"^(?:please|kindly|now|also|just|ek|for me)\s+", re.I)
# A clause that only points at something ("open it") is not a new command, and
# guessing which earlier object it meant would run the wrong tool.
PRONOUN_OBJECT = re.compile(
    r"\b(?:it|they|them|that|this|these|those|there|us|ye|yeh|woh|woo|iska|uska|"
    r"unhe|isime|usme|wahan)\b", re.I)


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _is_extra(step: dict[str, Any], tools: set[str], named: set[str]) -> bool:
    """Whether a clause asks for something the whole-sentence plan left out.

    A rule that fuses clauses already names their objects: 'open Chrome and
    search for recursion' becomes one browser search whose browser argument is
    Chrome, so nothing is missing. A rule that matched a substring is silent
    about the other clauses, and its own tool cannot stand in for them.
    """
    if str(step.get("tool") or "") in tools:
        return False
    entities = [str(value) for value in (step.get("args") or {}).values()
                if isinstance(value, str) and value.strip()]
    if not entities:
        return True
    covered = False
    for value in entities:
        clean = _norm(value)
        if clean in named:
            covered = True
            continue
        if clean in MODIFIER_WORDS:
            continue
        # A clause that only opens a site is covered when the fused plan used
        # that site's name; 'https://www.youtube.com/' is the same 'youtube'.
        if clean.startswith("http"):
            host = {label for label in urlparse(value).netloc.lower().split(".")
                    if label not in GENERIC_HOST_LABELS}
            if host & named:
                covered = True
                continue
        return True
    return not covered


@dataclass
class Plan:
    kind: PlanKind = "reply"
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    # Ordered follow-up actions. When present the executor runs tool/args first
    # and then each step in order, stopping at the first failure.
    steps: list[dict[str, Any]] = field(default_factory=list)
    reply: str | None = None
    explanation: str = ""
    planner: str = "rules"
    intent: str | None = None

    @property
    def is_tool(self) -> bool:
        return self.kind == "tool" and (self.tool is not None or bool(self.steps))

    def sequence(self) -> list[dict[str, Any]]:
        """The whole action list, single-tool plans included."""
        if self.tool:
            return [{"tool": self.tool, "args": dict(self.args)}, *self.steps]
        return [dict(step) for step in self.steps]


def strip_wake_word(text: str) -> str:
    cleaned = WAKE_WORD.sub("", text).strip()
    resume = None
    for hit in _WAKE_RESUME.finditer(cleaned):
        resume = hit
    # The words after the last address are the command; the ones before it are
    # the tail of whatever was said last time. A trailing address alone leaves
    # the sentence where it was.
    if resume and cleaned[resume.end():].strip(" ,.!?"):
        cleaned = cleaned[resume.end():]
    return cleaned.strip(" ,.!?")


def _clean_name(raw: str) -> str:
    value = raw.strip().strip("\"'")
    value = re.sub(r"^(?:the|my|a|an|ek|mera|meri|mere)\s+", "", value, flags=re.I)
    value = re.sub(r"\s+(?:app|application|please|now|for me)\s*$", "", value, flags=re.I)
    return value.strip(" .,!?")


def _detect_folder(text: str) -> str:
    lowered = text.lower()
    for key, canonical in FOLDERS.items():
        if re.search(rf"\b{re.escape(key)}\b", lowered):
            return canonical
    return "Downloads"


def _browser_name(text: str) -> str | None:
    """A browser word used as the object of the command, e.g. 'open Chrome and ...'.

    'edge' and 'brave' are ordinary English words, so they only count when the
    phrase ends there — 'open the edge cases file' must not launch Edge.
    """
    lowered = " ".join(text.lower().split())
    for phrase in BROWSER_PHRASES:
        match = re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lowered)
        if not match:
            continue
        if phrase in AMBIGUOUS_BROWSER_WORDS and lowered[match.end():].strip():
            continue
        return BROWSER_WORDS[phrase]
    return None


def _browser_in(text: str) -> str | None:
    """Only a prepositional reference: 'in Edge', 'using Chrome', 'chrome me'."""
    lowered = " ".join(text.lower().split())
    for phrase in BROWSER_PHRASES:
        match = re.search(
            rf"\b(?:in|using|on|with|se|mein|pe)\s+{re.escape(phrase)}\b", lowered
        )
        if not match:
            continue
        if phrase in AMBIGUOUS_BROWSER_WORDS and lowered[match.end():].strip():
            continue
        return BROWSER_WORDS[phrase]
    return None


def _engine_in(text: str) -> str | None:
    """The site to search, when the speaker names one: 'search YouTube for ML basics'."""
    for phrase in ENGINE_PHRASES:
        if re.search(rf"\b(?:on|in|from)\s+{re.escape(phrase)}\b", text, re.I):
            return SEARCH_ENGINES[phrase]
    for phrase in ENGINE_PHRASES:
        if re.search(rf"\b{re.escape(phrase)}\b", text, re.I):
            return SEARCH_ENGINES[phrase]
    return None


def _strip_engine_phrase(text: str) -> str:
    """Remove the site word from the query: the engine argument already carries it."""
    engine = _engine_in(text)
    if engine is None:
        return text.strip(" .,!?")
    for phrase in ENGINE_PHRASES:
        if SEARCH_ENGINES[phrase] != engine:
            continue
        cleaned = re.sub(rf"\b(?:on|in|from)\s+{re.escape(phrase)}\b", " ", text, flags=re.I)
        cleaned = re.sub(rf"\b{re.escape(phrase)}\b", " ", cleaned, flags=re.I)
        return " ".join(cleaned.split()).strip(" .,!?")
    return text.strip(" .,!?")


def _strip_browser_phrase(text: str) -> str:
    cleaned = re.sub(
        r"\b(?:in|using|on|with)\s+(?:google\s+|microsoft\s+)?(?:chrome|msedge|edge|firefox|brave)\b",
        " ", text, flags=re.I,
    )
    return " ".join(cleaned.split()).strip(" .,!?")


def _search_topic(raw: str) -> tuple[str, str]:
    """Split a spoken search tail into (engine, query).

    'YouTube for ML basics in Edge' has to reach the tool as engine=youtube and
    query='ML basics', otherwise the site name is searched for as a word.
    """
    text = _strip_browser_phrase(raw.strip(" .,!?"))
    engine = _engine_in(text) or "google"
    query = _strip_engine_phrase(text)
    query = re.sub(r"^(?:for|about|on|of)\s+", "", query, flags=re.I).strip(" .,!?")
    return engine, query


def _scalar_type(meta: dict[str, Any]) -> str:
    """The type label for one argument in the compact catalog.

    A nullable field (`int | None`) arrives as `anyOf` with no `type` key, so
    reading `meta["type"]` alone reported every optional integer as a string.
    """
    direct = meta.get("type")
    if isinstance(direct, str) and direct != "null":
        return direct
    for branch in meta.get("anyOf") or meta.get("oneOf") or []:
        if isinstance(branch, dict) and branch.get("type") not in (None, "null"):
            return str(branch["type"])
        ref = branch.get("$ref", "") if isinstance(branch, dict) else ""
        if ref:
            return ref.rsplit("/", 1)[-1]
    ref = meta.get("$ref", "")
    return ref.rsplit("/", 1)[-1] if ref else "string"


_ABBREVIATIONS = ("e.g", "i.e", "etc", "vs")


def _first_sentence(text: str) -> str:
    """One short clause of a tool description, for the catalog line.

    Splitting on ". " alone truncated "…most recent matching file, e.g. a PDF"
    mid-phrase, which reads to the model like a broken tool.
    """
    parts = text.split(". ")
    kept: list[str] = []
    for index, part in enumerate(parts):
        tail = part.strip().rsplit(",", 1)[-1].strip().rstrip(".")
        is_abridged = index + 1 < len(parts) and tail.lower() in _ABBREVIATIONS
        kept.append(part)
        if not is_abridged:
            break
    return ". ".join(kept).strip().rstrip(".")


REMINDER_TRIGGER = (r"\bremind(?:s|er|ers)?\b|\byaad\s+dil\w*|\bdilva\s*de\b|"
                    r"\bdhila\s*dene\b")

RELATIVE_UNITS = {
    # Offsets are seconds, so "in 4 seconds" survives; rounded to minutes it became 1.
    "second": 1, "sec": 1, "secs": 1, "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "ghanta": 3600, "ghante": 3600, "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "din": 86400, "day": 86400, "days": 86400, "week": 604800, "weeks": 604800,
}
RELATIVE_PATTERN = re.compile(
    r"\b(?:in|after)?\s*(\d{1,4}|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|fifteen|twenty|thirty|forty|sixty)\s*(seconds?|secs?|minutes?|mins?|"
    r"ghante?|hours?|hrs?|din|days?|weeks?)\b(?:\s*(?:baad|se|ke\s+baad))?",
    re.I,
)
DAYPART_SHIFT = {
    "morning": 0, "subah": 0, "prabhat": 0, "afternoon": 12, "dopahar": 12,
    "noon": 12, "evening": 12, "shaam": 12, "sandhya": 12,
    "night": 12, "raat": 12, "ratribhakt": 12, "tonight": 12,
}
WEEKDAY_WORDS = {
    "monday": 0, "mon": 0, "somwar": 0, "tuesday": 1, "tue": 1, "mangalwar": 1,
    "wednesday": 2, "wed": 2, "budhwar": 2, "thursday": 3, "thu": 3, "guruwar": 3,
    "friday": 4, "fri": 4, "shukrwar": 4, "saturday": 5, "sat": 5, "shanivar": 5,
    "sunday": 6, "sun": 6, "ravivar": 6,
}
CLOCK_PHRASE = re.compile(    r"(?:\b(?:at|around|by|ko)\s+(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?|"
    r"[a-z]+\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b"
    r"|\b\d{1,2}(?::\d{2})?\s*(?:baje|o'?clock)\b"
    r"|\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.)\b"
    r"|\b[a-z]+\s+\d{1,2}\s*(?:a\.?m\.?|p\.?m\.)\b)",
    re.I,
)


def _reminder_relative(text: str) -> int | None:
    """Seconds into the future for 'in 20 minutes' / 'in 4 seconds' / '4 minute baad'."""
    if re.search(r"\bhalf\s+an?\s+hour\b", text, re.I):
        return 30 * 60
    match = RELATIVE_PATTERN.search(text)
    if not match:
        return None
    count = NUMBER_WORDS.get(match.group(1).lower(), None)
    if count is None:
        try:
            count = int(match.group(1))
        except ValueError:
            return None
    unit = match.group(2).lower().rstrip("s")
    unit = {"min": "minute", "sec": "second", "hr": "hour", "ghanta": "ghanta"}.get(unit, unit)
    unit_seconds = RELATIVE_UNITS.get(unit, None)
    if unit_seconds is None or count <= 0:
        return None
    return max(1, count * unit_seconds)


def _daypart_hour(text: str, hour: int) -> tuple[int, bool]:
    """Shift a bare hour using morning/evening words; returns (hour, applied)."""
    for word, shift in DAYPART_SHIFT.items():
        if re.search(rf"\b{word}\b", text, re.I):
            if shift and hour < 12:
                return hour + 12, True
            if not shift and hour == 12:
                return 0, True
            return hour, True
    return hour, False


def _reminder_clock(text: str) -> tuple[int | None, int, bool]:
    """Read only an explicit clock phrase, so 'chapter 7 at 9 pm' does not become 07:00."""
    match = CLOCK_PHRASE.search(text)
    if not match:
        return None, 0, False
    fragment = match.group(0)
    hour, minute, ambiguous = _parse_clock(fragment)
    if hour is None:
        return None, 0, False
    shifted, applied = _daypart_hour(text, hour)
    if applied:
        return shifted % 24, minute, False
    return hour, minute, ambiguous


def _reminder_date(text: str, now: datetime) -> tuple[object, bool]:
    """Which calendar day the reminder is for: (date, tomorrow_was_said)."""
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if iso:
        try:
            return datetime.strptime(iso.group(0), "%Y-%m-%d").date(), False
        except ValueError:
            return now.date(), False
    for word, index in WEEKDAY_WORDS.items():
        if re.search(rf"\b(?:on\s+)?{word}\b", text, re.I):
            ahead = (index - now.weekday()) % 7 or 7
            return (now + timedelta(days=ahead)).date(), False
    if re.search(r"\b(tomorrow|kal)\b", text, re.I):
        return (now + timedelta(days=1)).date(), True
    return now.date(), False


def _reminder_moment(text: str, now: datetime) -> tuple[datetime | None, str]:
    """Absolute instant for a spoken reminder, plus why it could not be read."""
    hour, minute, ambiguous = _reminder_clock(text)
    if ambiguous:
        return None, "ambiguous"
    if hour is None:
        return None, "missing"
    day, said_tomorrow = _reminder_date(text, now)
    moment = datetime(day.year, day.month, day.day, hour, minute)
    if moment <= now and not said_tomorrow:
        moment += timedelta(days=1)
    if moment <= now:
        return None, "past"
    return moment, ""


def _reminder_body(text: str) -> str:
    """Strip the trigger and every time phrase so only the thing to remember is left."""
    body = re.sub(r"\b(?:hey\s+)?jarvis\b[,!.]?\s*", " ", text, flags=re.I)
    body = re.sub(r"\b(?:please|kindly|plz)\b", " ", body, flags=re.I)
    body = re.sub(r"\bremind\s+(?:me|mujhe|muje)\b(?:\s+(?:to|about|that|ka|ki))?\s*",
                  " ", body, flags=re.I)
    body = re.sub(r"\b(?:mujhe|muje|mai|main)\b", " ", body, flags=re.I)
    body = re.sub(r"\b(?:set|put|make|keep|lag[aā]o|de)\s+(?:up\s+)?"
                  r"(?:an?\s+|ek\s+|apn[aā]\s+)?alarms?\b"
                  r"(?:\s+(?:of|for|to|ke\s+liye|ko))?\s*", " ", body, flags=re.I)
    body = re.sub(r"\balarms?\b", " ", body, flags=re.I)
    body = re.sub(r"\b(?:from\s+now|se\s+abhi|abhi\s+se|abhi)\b", " ", body, flags=re.I)
    body = re.sub(r"\bset\s+(?:up\s+)?(?:a\s+|ek\s+)?reminders?\b(?:\s+(?:to|for|about))?\s*",
                  " ", body, flags=re.I)
    body = re.sub(r"\breminders?\b", " ", body, flags=re.I)
    body = re.sub(r"\byaad\s+dil\w*\b(?:\s+(?:do|de|dijiye))?", " ", body, flags=re.I)
    body = RELATIVE_PATTERN.sub(" ", body)
    body = re.sub(r"\b(?:in|after)?\s*\d{1,4}\s*(?:minute|second|hour|week|day)s?\s*baad\b",
                  " ", body, flags=re.I)
    body = CLOCK_PHRASE.sub(" ", body)
    body = re.sub(r"\bin\s+(?:the\s+)?(?:morning|evening|afternoon|night|subah|shaam|raat)\b",
                  " ", body, flags=re.I)
    body = re.sub(r"\b(?:today|tomorrow|aaj|kal|tonight|subah|shaam|raat|morning|evening|"
                  r"night|afternoon|dopahar)\b", " ", body, flags=re.I)
    body = re.sub(r"\b(?:at|around|by|ko|on|date)\b", " ", body, flags=re.I)
    body = re.sub(r"\s+", " ", body)
    body = body.strip(" ,.;:!-")
    return re.sub(r"^(?:to|ki|ka|ke|that|about)\s+", "", body, flags=re.I).strip(" ,.;:")


def _parse_clock(text: str) -> tuple[int | None, int, bool]:
    lowered = text.lower()
    match = re.search(
        r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        r"(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\b(?:\s*(?:baje|o'?clock))?",
        lowered,
    )
    if not match:
        return None, 0, False
    raw_hour, raw_minute, meridiem = match.groups()
    hour = NUMBER_WORDS.get(raw_hour, None)
    if hour is None:
        hour = int(raw_hour)
    minute = int(raw_minute) if raw_minute else 0
    if hour > 23 or minute > 59:
        return None, 0, False
    ambiguous = False
    if meridiem:
        pm = meridiem.startswith("p")
        if pm and hour < 12:
            hour += 12
        if not pm and hour == 12:
            hour = 0
    elif hour <= 12:
        ambiguous = hour != 0 and hour != 12
    return hour, minute, ambiguous


class Planner:
    def __init__(self, settings: Settings, repo: Repository, registry: ToolRegistry,
                 router: ModelRouter):
        self.settings = settings
        self.repo = repo
        self.registry = registry
        self.router = router
        self._head: str | None = None
        self._rules = [
            self._rule_time,
            self._rule_capabilities,
            self._rule_greeting,
            self._rule_cancel,
            self._rule_task_list,
            self._rule_briefing,
            self._rule_reminder,
            self._rule_automation,
            self._rule_phone_battery,
            self._rule_phone_status,
            self._rule_alarm,
            self._rule_reels,
            self._rule_phone_open_app,
            self._rule_whatsapp,
            self._rule_contact_search,
            self._rule_call,
            self._rule_presentation,
            self._rule_code_project,
            self._rule_run_tests,
            self._rule_image,
            self._rule_amazon,
            self._rule_maps,
            self._rule_organize,
            self._rule_duplicates,
            self._rule_file_search,
            self._rule_summarize_file,
            self._rule_system_stats,
            self._rule_brightness,
            self._rule_volume,
            self._rule_lock,
            self._rule_chained,
            self._rule_screenshot,
            self._rule_running_apps,
            self._rule_configured_apps,
            self._rule_close_app,
            self._rule_switch_app,
            self._rule_where_saved,
            self._rule_latest_file,
            self._rule_open_folder,
            self._rule_browser_intent,
            self._rule_open_site,
            self._rule_open_app,
            self._rule_web_search,
            self._rule_memory,
            self._rule_integrations,
        ]

    # ------------------------------------------------------------------ api
    async def plan(self, text: str, history: list[dict[str, str]] | None = None) -> Plan:
        cleaned = self._clean(text)
        if not cleaned:
            return Plan(reply="Yes? Give me a command.", planner="rules")
        single = self._first_rule(cleaned)
        compound = self._compound(cleaned, single)
        if compound:
            return compound
        if single:
            return single
        model_plan, failure = await self._model_plan(cleaned, history or [])
        if model_plan:
            return model_plan
        if failure:
            # The model was the right route and genuinely failed; say why instead
            # of implying the request was unintelligible.
            return Plan(reply=failure, planner="model-error")
        return Plan(
            reply=(
                "I could not map that to a supported action. Try commands like "
                "'open VS Code', 'battery status', 'organize Downloads', "
                "'make a presentation on AI', or 'what can you do?'."
            ),
            planner="fallback",
        )

    def _first_rule(self, text: str) -> Plan | None:
        for rule in self._rules:
            try:
                plan = rule(text)
            except Exception:  # noqa: BLE001 - a bad rule must not break planning
                plan = None
            if plan:
                return plan
        return None

    def _compound(self, text: str, single: Plan | None) -> Plan | None:
        """Run every clause of "open Chrome, mute the sound and screenshot".

        A rule matches the whole sentence and the first one that fits wins, so
        the actions after it were dropped in silence. Each clause is planned on
        its own, and the split is used only when every clause names an action
        the rules understand and the whole-sentence plan still left something
        out. That leaves an ordinary sentence alone: "what is the time and date"
        has a clause that is a question, "create a folder and open it" has a
        clause that is only a pronoun, and "open Chrome and search for
        recursion" really is one browser search that already names Chrome.
        """
        clauses = [CLAUSE_FILLER.sub("", piece).strip(" .,!?")
                   for piece in CLAUSE_SPLIT.split(text)]
        clauses = [clause for clause in clauses if clause]
        if not 2 <= len(clauses) <= 4:
            return None
        if any(PRONOUN_OBJECT.search(clause) for clause in clauses):
            return None
        steps: list[dict[str, Any]] = []
        for clause in clauses:
            plan = self._first_rule(clause)
            if not plan or plan.kind != "tool" or not plan.is_tool:
                return None
            for step in plan.sequence():
                if step not in steps:
                    steps.append(step)
        if len(steps) < 2:
            return None
        planned = single.sequence() if single else []
        tools = {str(step.get("tool") or "") for step in planned}
        named = {_norm(value) for step in planned
                 for value in (step.get("args") or {}).values()
                 if isinstance(value, str) and value.strip()}
        if not any(_is_extra(step, tools, named) for step in steps):
            return None
        return Plan(
            kind="tool",
            tool=steps[0]["tool"],
            args=dict(steps[0]["args"]),
            steps=steps[1:],
            intent="compound",
            planner="rules",
            explanation=" and ".join(clauses),
        )

    def _clean(self, text: str) -> str:
        cleaned = strip_wake_word(text).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    # ------------------------------------------------------------- planning
    def _tool(self, tool: str, args: dict[str, Any], intent: str,
              explanation: str = "") -> Plan:
        return Plan(kind="tool", tool=tool, args=args, intent=intent,
                    planner="rules", explanation=explanation or intent)

    def _sequence(self, steps: list[tuple[str, dict[str, Any]]], intent: str,
                  explanation: str = "") -> Plan:
        first_tool, first_args = steps[0]
        return Plan(
            kind="tool",
            tool=first_tool,
            args=first_args,
            steps=[{"tool": tool, "args": args} for tool, args in steps[1:]],
            intent=intent,
            planner="rules",
            explanation=explanation or intent,
        )

    def _reply(self, text: str, intent: str = "chat") -> Plan:
        return Plan(kind="reply", reply=text, intent=intent, planner="rules")

    # ---------------------------------------------------------------- rules
    def _rule_time(self, text: str) -> Plan | None:
        if re.search(r"\b(what(?:'s| is) the time|what time is it|current time|today'?s date|"
                     r"what(?:'s| is) the date|kitne baje|aaj ka din|aaj ki tareekh|"
                     r"din kya hai|tareekh kya|time kya hai|samay kya)\b", text, re.I):
            now = datetime.now()
            return self._reply(
                f"It is {now.strftime('%H:%M')} on {now.strftime('%A, %d %B %Y')}.", "time"
            )
        return None

    def _rule_capabilities(self, text: str) -> Plan | None:
        if re.search(r"\b(what can you do|capabilities|what are you able|help me|^help$|"
                     r"features|kya kar sakte)\b", text, re.I):
            return self._tool("assistant.capabilities", {}, "capabilities")
        return None

    def _rule_greeting(self, text: str) -> Plan | None:
        if re.fullmatch(r"(hi|hello|hey|hey jarvis|namaste|good (morning|afternoon|evening)|"
                        r"good morning jarvis)[.!]*", text.strip(), re.I):
            return self._reply(
                "Hello. JARVIS is online and ready. Ask me to open an app, check telemetry, "
                "organize files, or draft a presentation.", "greeting"
            )
        if re.fullmatch(r"(thanks|thank you|shukriya)[.!]*", text.strip(), re.I):
            return self._reply("You're welcome.", "greeting")
        return None

    def _rule_cancel(self, text: str) -> Plan | None:
        if re.fullmatch(r"(stop|cancel|abort)[.!]*", text.strip(), re.I) or (
            re.search(r"\b(stop|cancel|abort|band karo|ruko)\b", text, re.I)
            and re.search(r"\b(task|job|current|everything|all|it|sab)\b", text, re.I)
        ):
            return self._tool("tasks.cancel", {}, "cancel_task")
        return None

    def _rule_task_list(self, text: str) -> Plan | None:
        if re.search(r"\btasks?\b|\bkaam\b", text, re.I) and re.search(
            r"\b(running|active|pending|status|list|show|which|tell|kya)\b", text, re.I
        ):
            return self._tool("tasks.list", {"limit": 10}, "list_tasks")
        return None

    def _rule_briefing(self, text: str) -> Plan | None:
        if re.search(r"\b(briefing|brief me|morning brief|daily brief|briefing do)\b", text, re.I):
            return self._tool("assistant.briefing", {}, "briefing")
        return None

    def _rule_reminder(self, text: str) -> Plan | None:
        if not re.search(REMINDER_TRIGGER, text, re.I):
            return None
        if re.search(r"\b(cancel|delete|remove|band|rehne|hat[aā]o)\b", text, re.I):
            fragment = re.sub(
                r"\b(?:cancel|delete|remove|the|my|a|an|band|kar|karo|do|de|ko|ka|ki|"
                r"se|rehne|dena|hat[aā]o|reminders?|jarvis|please|dijiye)\b",
                " ", text, flags=re.I,
            )
            fragment = " ".join(fragment.split()).strip(" .,;:")
            if fragment and fragment.lower() not in {"all", "everything", "sab"}:
                return self._tool("reminders.cancel", {"fragment": fragment[:120]},
                                  "reminder_cancel")
            return self._reply(
                "Say which reminder to cancel — for example 'cancel the normalization "
                "reminder'. I do not clear everything without a name.", "reminder")
        if re.search(r"\b(list|show|tell|pending|kya|kaun|which|how many|kitne)\b", text, re.I) \
                and not re.search(r"\bremind\s+(?:me|mujhe|muje)\b", text, re.I):
            return self._tool("reminders.list", {}, "reminder_list")
        if re.search(r"\b(what|where)\b.*\breminders?\b", text, re.I):
            return self._tool("reminders.list", {}, "reminder_list")
        offset = _reminder_relative(text)
        if offset is not None:
            body = _reminder_body(text)
            if not body:
                return self._reply("Remind you to do what?", "reminder")
            return self._tool(
                "reminders.set", {"text": body[:300], "seconds": offset}, "reminder_set",
                explanation=f"Reminder in {offset} second(s): {body}",
            )
        moment, reason = _reminder_moment(text, datetime.now())
        if reason == "ambiguous":
            hour, _, _ = _reminder_clock(text)
            alternative = (hour + 12) % 24 if hour is not None else None
            return self._reply(
                f"Did you mean {hour:02d}:00 or {alternative:02d}:00? Add am or pm and "
                "I will set the reminder.", "reminder")
        if reason:
            return self._reply(
                "A reminder needs a time — 'remind me to revise normalization at 9 pm' or "
                "'in 20 minutes'.", "reminder")
        body = _reminder_body(text)
        if not body:
            return self._reply("Remind you about what?", "reminder")
        return self._tool(
            "reminders.set",
            {"text": body[:300], "due_at": moment.isoformat(timespec="seconds")},
            "reminder_set",
            explanation=f"Reminder at {moment:%H:%M on %A %d %B}: {body}",
        )

    def _rule_automation(self, text: str) -> Plan | None:
        if re.search(r"\b(automation|automate|routine|workflow)\b", text, re.I):
            return self._reply(
                "The automation builder is NOT IMPLEMENTED. JARVIS can execute each step on "
                "command, but there is no scheduler for chained routines yet.", "automation"
            )
        return None

    def _rule_phone_battery(self, text: str) -> Plan | None:
        if re.search(r"\b(phone|mobile|samsung|android)\b", text, re.I) and re.search(
            r"\b(battery|charge|charging)\b", text, re.I
        ):
            return self._tool("phone.battery", {}, "phone_battery")
        return None

    def _rule_phone_status(self, text: str) -> Plan | None:
        if re.search(r"\b(phone|mobile|android)\b", text, re.I) and re.search(
            r"\b(connected|connection|paired|pairing|status)\b", text, re.I
        ):
            return self._tool("phone.status", {}, "phone_status")
        return None

    def _rule_alarm(self, text: str) -> Plan | None:
        if not re.search(r"\balarm\b", text, re.I):
            return None
        if re.search(r"\b(cancel|delete|remove|band)\b", text, re.I):
            return self._reply(
                "Cancelling an alarm is not exposed by the phone bridge yet; open the Clock app "
                "on your phone to remove it.", "alarm"
            )
        # 'an alarm in 4 seconds' is a JARVIS timer, not a phone-clock entry:
        # the bridge only accepts an hour and a minute and needs the phone paired.
        offset = _reminder_relative(text)
        if offset is not None and not CLOCK_PHRASE.search(text):
            body = _reminder_body(text) or "Alarm"
            return self._tool(
                "reminders.set", {"text": body[:300], "seconds": offset}, "alarm",
                explanation=f"Alarm in {offset} second(s): {body}",
            )
        hour, minute, ambiguous = _parse_clock(text)
        if hour is None:
            return self._reply(
                "What time should the alarm be for? For example: 'set an alarm for 5:30 am on my phone'.",
                "alarm",
            )
        if ambiguous:
            alternative = hour + 12 if hour < 12 else hour - 12
            return self._reply(
                f"That time is ambiguous. Did you mean {hour:02d}:{minute:02d} or "
                f"{alternative:02d}:{minute:02d}? Say the time with am or pm.", "alarm"
            )
        return self._tool(
            "phone.alarm", {"time": f"{hour:02d}:{minute:02d}", "label": "JARVIS alarm"},
            "alarm",
        )

    def _rule_reels(self, text: str) -> Plan | None:
        if re.search(r"\b(reels?|shorts|instagram)\b", text, re.I) and re.search(
            r"\b(scroll|auto)\b", text, re.I
        ):
            if re.search(r"\b(stop|band|ruko)\b", text, re.I):
                return self._tool("phone.reels_stop", {}, "reels_stop")
            return self._tool("phone.reels_start", {}, "reels_start")
        return None

    def _rule_whatsapp(self, text: str) -> Plan | None:
        if "whatsapp" not in text.lower():
            return None
        quoted = re.search(r"[\"“']([^\"”']{1,500})[\"”']", text)
        message = quoted.group(1).strip() if quoted else ""
        contact = ""
        contact_patterns = [
            r"(?:to|for)\s+(.+?)\s+(?:on\s+whatsapp|saying|with\s+the\s+message|ke\s+liye)",
            r"whatsapp\s+(?:to\s+)?(.+?)(?:\s+(?:saying|with|about)|$)",
            r"message\s+(.+?)\s+(?:on\s+whatsapp|saying)",
        ]
        for pattern in contact_patterns:
            match = re.search(pattern, text, re.I)
            if match:
                contact = _clean_name(match.group(1))
                break
        if contact.lower() in {"message", "a message", "the message", "msg", "draft"}:
            contact = ""
        if not message:
            patterns = [
                r"\bsend\s+(.+?)\s+to\b",
                r"\b(?:saying|message(?:\s+is)?|likho|bhejo)\s+[\"']?(.+?)[\"']?"
                r"(?:\s+(?:to|on)\b|$)",
            ]
            for pattern in patterns:
                match = re.search(pattern, text, re.I)
                if match:
                    message = match.group(1).strip().strip("\"'")
                    break
        if not contact:
            return self._reply(
                "Who should I open WhatsApp for? Say, for example, 'send Hi to my sister on WhatsApp'.",
                "whatsapp",
            )
        if not message:
            return self._reply(
                f"What message should I draft for {contact}? I will open the chat with the text "
                "ready — you still need to press send on your phone.", "whatsapp",
            )
        return self._tool(
            "phone.whatsapp",
            {"contact": contact, "message": message, "send": False},
            "whatsapp",
            "Draft only; sending is never automated.",
        )

    def _rule_contact_search(self, text: str) -> Plan | None:
        if not re.search(r"\bcontacts?\b|\bphonebook\b", text, re.I):
            return None
        match = re.search(
            r"(?:find|search|look\s*up|dhoondo?)\s+(?:for\s+)?(.+?)(?:\s+in\s+(?:my\s+)?contacts?|$)",
            text, re.I,
        )
        if not match:
            return self._reply("Which contact should I look for?", "contact_search")
        return self._tool("phone.find_contact",
                          {"contact": _clean_name(match.group(1))}, "contact_search")

    def _rule_call(self, text: str) -> Plan | None:
        match = re.match(r"^(?:call|dial)\s+(.+)$", text.strip(), re.I)
        if not match:
            return None
        contact = re.sub(r"\s+on\s+my\s+phone\b", "", match.group(1), flags=re.I)
        contact = _clean_name(contact)
        if not contact or contact.lower() in {"it", "someone"}:
            return self._reply("Who should I call?", "call")
        return self._tool("phone.call", {"contact": contact}, "call",
                          "Opens the dialer; the call is not placed automatically.")

    def _rule_phone_open_app(self, text: str) -> Plan | None:
        if not re.search(r"\b(on|in)\s+my\s+phone\b|\bphone\s+(?:pe|par)\b", text, re.I):
            return None
        match = re.search(r"(?:open|launch|start|kholo|khol)\s+(?:the\s+|my\s+)?(.+?)"
                          r"(?:\s+on\s+my\s+phone|$)", text, re.I)
        if not match:
            return None
        return self._tool("phone.open_app", {"app": _clean_name(match.group(1))}, "phone_open_app")

    def _rule_presentation(self, text: str) -> Plan | None:
        lowered = text.lower()
        wants = bool(re.search(r"\b(presentation|deck|slides?|ppt)\b", lowered)) or (
            "powerpoint" in lowered and re.search(r"\b(create|make|build|generate|banao|bana)\b",
                                                   lowered)
        )
        if not wants:
            return None
        if re.search(r"\b(open|launch)\s+(powerpoint|ppt)\s*$", lowered):
            return None
        topic = ""
        match = re.search(r"\b(?:on|about|par)\s+(.+)", text, re.I)
        if match:
            topic = match.group(1)
        else:
            topic = re.sub(
                r"\b(?:create|make|build|generate|prepare|banao|bana)\b|\b(?:a|an|the)\b|"
                r"\bpowerpoint\b|\bpresentation\b|\bdeck\b|\bppt\b|\bslides?\b|"
                r"\bfor\s+me\b|\bplease\b",
                " ", text, flags=re.I,
            )
        topic = re.sub(
            r"\b(?:with\s+\d{1,2}\s*(?:slides?|slide)?|and\s+open\b.*|open\s+it\b.*|"
            r"in\s+powerpoint\b.*|for\s+(?:students|executives|beginners|investors|"
            r"developers|my\s+team|class))",
            " ", topic, flags=re.I,
        )
        topic = re.sub(r"\s+", " ", topic).strip(" .,!?:;")
        if len(topic) < 2:
            return self._reply("What topic should the presentation cover?", "presentation")
        slides_match = re.search(r"\b(\d{1,2})\s*(?:slides?|slide)\b", text, re.I)
        slides = int(slides_match.group(1)) if slides_match else 8
        slides = max(4, min(20, slides))
        audience_match = re.search(
            r"\bfor\s+(students|executives|beginners|investors|developers|my\s+team|class|"
            r"a\s+general\s+audience|general\s+audience)\b", text, re.I,
        )
        audience = audience_match.group(1) if audience_match else "general audience"
        open_after = bool(re.search(
            r"\bopen\s+(?:it|the\s+(?:file|deck|presentation))\b|\bin\s+powerpoint\b|\bkholo\b",
            text, re.I,
        ))
        return self._tool(
            "docs.create_presentation",
            {"topic": topic, "slides": slides, "audience": audience, "open_after": open_after},
            "presentation",
        )

    def _rule_code_project(self, text: str) -> Plan | None:
        if not re.search(r"\b(python|react|fastapi|flask|calculator|script|project)\b",
                         text, re.I):
            return None
        if not re.search(r"\b(create|make|build|generate|banao|bana|new|starter|start)\b",
                         text, re.I):
            return None
        lowered = text.lower()
        if "calculator" in lowered or "calc" in lowered:
            kind, name = "calculator", "calculator"
        elif "react" in lowered and ("fastapi" in lowered or "backend" in lowered):
            kind, name = "react_fastapi", "react-fastapi-starter"
        elif "fastapi" in lowered:
            kind, name = "fastapi_starter", "fastapi-starter"
        elif "python" in lowered or "script" in lowered:
            kind, name = "python_script", "python-starter"
        else:
            return None
        named = re.search(r"\b(?:called|named)\s+[\"']?([\w -]{1,60})", text, re.I)
        if named:
            name = named.group(1).strip()
        return self._tool(
            "code.create_project",
            {
                "kind": kind,
                "name": name,
                "open_in_vscode": bool(re.search(r"\bvs ?code\b|\bcode\s+mein\b|\bvscode\b",
                                                 text, re.I)),
            },
            "create_project",
        )

    def _rule_run_tests(self, text: str) -> Plan | None:
        if re.search(r"\b(run|execute|chalao|chala|start)\b", text, re.I) and re.search(
            r"\b(tests?|unit\s+tests?)\b", text, re.I
        ):
            match = re.search(r"([A-Za-z]:\\[^\s\"']+|(?:\.[\\/])[^\s\"']+)", text)
            if match:
                return self._tool("code.run_tests", {"project_dir": match.group(1)}, "run_tests")
            candidates = []
            if self.settings.project_root.exists():
                candidates = sorted(
                    (p for p in self.settings.project_root.iterdir() if p.is_dir()),
                    key=lambda p: p.stat().st_mtime, reverse=True,
                )
            if not candidates:
                return self._reply(
                    "Tell me the project folder to test, for example: "
                    f"'{self.settings.project_root}\\calculator'.", "run_tests",
                )
            return self._tool("code.run_tests", {"project_dir": str(candidates[0])}, "run_tests")
        return None

    def _rule_image(self, text: str) -> Plan | None:
        if not re.search(r"\b(image|picture|wallpaper|illustration|artwork|photo)\b", text, re.I):
            return None
        if not re.search(r"\b(generate|create|make|draw|design|banao|bana)\b", text, re.I):
            return None
        prompt = re.sub(
            r"^(?:please\s+)?(?:generate|create|make|draw|design|banao|bana)\s+"
            r"(?:me\s+)?(?:an?\s+)?(?:image|picture|illustration|artwork|photo|wallpaper)\s*(?:of|for)?\s*",
            "", text, flags=re.I,
        ).strip()
        if len(prompt) < 2:
            prompt = text
        return self._tool("image.generate", {"prompt": prompt}, "image_generate")

    def _rule_amazon(self, text: str) -> Plan | None:
        if "amazon" not in text.lower():
            return None
        query = ""
        patterns = [
            r"(?:find|search|look\s+for|buy)\s+(?:me\s+)?(.+?)\s+(?:on|at)\s+amazon\b",
            r"amazon\s+(?:for|me\s+for)\s+(.+?)(?:[.?!]|$)",
            r"search\s+amazon\s+for\s+(.+?)(?:[.?!]|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                query = match.group(1)
                break
        if not query:
            match = re.search(r"amazon\s+(.+)", text, re.I)
            query = match.group(1) if match else "deals"
        price = re.search(
            r"\b(?:under|below|less\s+than|upto|up\s+to|kam)\s*"
            r"(?:rs\.?|₹|inr|rupees)?\s*(\d{2,7})", text, re.I,
        )
        query = re.sub(r"\b(?:under|below|less\s+than|upto|up\s+to)\s*.*$", "", query, flags=re.I)
        query = re.sub(r"\s+", " ", query).strip(" .,!?")
        args: dict[str, Any] = {"query": query or "deals", "open_page": True}
        if price:
            args["max_price"] = int(price.group(1))
        return self._tool("browser.amazon", args, "amazon")

    def _rule_maps(self, text: str) -> Plan | None:
        if not re.search(r"\b(route|directions|navigate|drive|travel)\b", text, re.I):
            return None
        match = re.search(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?:[.?!]|$)", text, re.I)
        if not match:
            return None
        return self._tool(
            "browser.maps",
            {"origin": match.group(1).strip(), "destination": match.group(2).strip()},
            "maps",
        )

    def _rule_organize(self, text: str) -> Plan | None:
        if not re.search(r"\b(organize|organise|arrange|sort|clean\s*up|saaf)\b", text, re.I):
            return None
        folder = _detect_folder(text)
        if re.search(r"\b(undo|revert|wapas|restore)\b", text, re.I):
            return self._tool("files.undo_organize", {"folder": folder}, "organize_undo")
        if re.search(r"\b(apply|confirm|go\s+ahead|do\s+it|kar\s+do|chala\s+do)\b", text, re.I):
            return self._tool("files.organize_apply", {"folder": folder}, "organize_apply")
        return self._tool("files.organize_preview", {"folder": folder}, "organize_preview")

    def _rule_duplicates(self, text: str) -> Plan | None:
        if re.search(r"\bduplicates?\b|\bsame\s+files?\b", text, re.I):
            return self._tool("files.duplicates", {"folder": _detect_folder(text)}, "duplicates")
        return None

    def _rule_file_search(self, text: str) -> Plan | None:
        if not re.search(r"\b(files?|folders?)\b", text, re.I):
            return None
        if not re.search(r"\b(find|search|locate|look\s+for|dhoondo?)\b", text, re.I):
            return None
        if re.search(r"\bcontacts?\b", text, re.I):
            return None
        extension = None
        ext_match = re.search(
            r"\b(pdf|docx?|xlsx?|pptx?|png|jpe?g|gif|mp4|mkv|mp3|wav|zip|rar|txt|csv|"
            r"py|js|ts|json)\s+files?\b", text, re.I,
        )
        if ext_match:
            extension = ext_match.group(1).lower()
        elif re.search(r"\bimages?\b|\bphotos?\b", text, re.I):
            extension = ".png"
        query = ""
        query_match = re.search(r"\b(?:for|named|called)\s+(.+?)(?:\s+in\s+|\s+inside\s+|$)",
                                text, re.I)
        if query_match:
            query = query_match.group(1).strip(" .,!?")
            query = re.sub(rf"\b{extension}\b", "", query or "", flags=re.I).strip() if extension else query
            query = re.sub(r"\bfiles?\b", "", query, flags=re.I).strip()
        if not query and not extension:
            return self._reply(
                "What name or extension should I search for, and in which folder?", "file_search"
            )
        return self._tool(
            "files.search",
            {"folder": _detect_folder(text), "query": query or "",
             "extension": extension, "limit": 50},
            "file_search",
        )

    def _rule_summarize_file(self, text: str) -> Plan | None:
        if not re.search(r"\bsummar(?:y|ize|ise)\b", text, re.I):
            return None
        if not re.search(r"\b(file|document|pdf|pptx|presentation|report)\b", text, re.I):
            return None
        path_match = re.search(r"[\"']([A-Za-z]:\\[^\"']+)[\"']|([A-Za-z]:\\[^\s\"']+)", text)
        if not path_match:
            return self._reply(
                "Give me the full path of the file to summarise, for example: "
                "'summarize C:\\Users\\me\\Downloads\\report.pptx'.", "summarize_file",
            )
        path = path_match.group(1) or path_match.group(2)
        return self._tool("files.summarize", {"path": path}, "summarize_file")

    #: A question about the concept, not about this machine.
    _STATS_CONCEPT = re.compile(
        r"\b(why|difference|versus|meaning|means|defines?|definition|explain|describe|"
        r"works?\b|used\s+for|called)\b")

    def _rule_system_stats(self, text: str) -> Plan | None:
        lowered = text.lower()
        # "network", "memory" and "performance" are ordinary English words, so a
        # topic question used to be executed as a machine check. Only route to the
        # stats tool when the sentence asks about *this* computer. "what is the
        # battery level" does ask about this computer, but the old pattern read it
        # as a definition question and spent 32 seconds of model time refusing it.
        if self._STATS_CONCEPT.search(lowered):
            return None
        if re.search(
            r"\bwhat\s+is\s+(?:a|an)\b|\bhow\s+(?:do|does|can)\s+(?:a|an|the|I)\b", lowered,
        ):
            return None
        asked = bool(re.search(
            r"\b(check|show|tell|report|status|usage|used|free|left|available|monitor|"
            r"how\s+much|how'?s|how\s+is|what'?s|what\s+is|batao|dekho|kitna|kitni|kaisa)\b",
            lowered,
        ))
        if not asked and len(lowered.split()) > 4:
            return None
        sections: list[str] = []
        if re.search(r"\b(battery|charge|charging)\b", lowered):
            sections.append("battery")
        if re.search(r"\b(ram|memory)\b", lowered):
            sections.append("memory")
        if re.search(r"\b(disk|storage|space|ssd|hard\s+drive)\b", lowered):
            sections.append("disk")
        if re.search(r"\b(network|internet|wifi|wi-fi|online|connection)\b", lowered):
            sections.append("network")
        if re.search(r"\b(cpu|processor)\b", lowered):
            sections.append("cpu")
        if re.search(r"\b(uptime|how\s+long\s+.*(?:on|running))\b", lowered):
            sections.append("uptime")
        if re.search(r"\bprocess(?:es)?\b", lowered):
            sections.append("processes")
        if not sections and re.search(
            r"\b(system\s+(?:status|health|info)|how\s+is\s+(?:my\s+)?(?:computer|laptop|pc|system)|"
            r"performance|telemetry)\b", lowered,
        ):
            sections = ["cpu", "memory", "battery", "disk", "network", "uptime"]
        if not sections:
            return None
        return self._tool("system.stats", {"sections": sections}, "system_stats")

    def _rule_brightness(self, text: str) -> Plan | None:
        lowered = text.lower()
        if not re.search(
            r"\b(brightness|brighten|brighter|dim|roshni|light\s*level)\b", lowered
        ) and not re.search(r"\bbright\b|\bkam\s+light\b", lowered):
            return None
        # "night light" is a different Settings page, not a brightness level.
        if re.search(r"\bnight\s*light\b", lowered):
            return None
        args: dict[str, Any] = {"step": 20}
        explicit = re.search(r"\b(\d{1,3})\s*(?:percent|per\s*cent|%)\b", lowered)
        if not explicit:
            explicit = re.search(r"\b(?:to|at|kar)\s*(\d{1,3})\b", lowered)
        if re.search(r"\b(what(?:'s| is)?|how\s+much|current|kitni|bat[a]o)\b", lowered) and not explicit:
            args["action"] = "get"
        elif explicit and 0 <= int(explicit.group(1)) <= 100:
            args["action"] = "set"
            args["percent"] = int(explicit.group(1))
        elif re.search(r"\b(max|full|poori|poora|100)\b", lowered):
            args["action"] = "max"
        elif re.search(r"\b(min|minimum|lowest|sabse\s+kam)\b", lowered):
            args["action"] = "min"
        elif re.search(r"\b(up|increase|badha|raise|more|higher|karo)\b", lowered) and not re.search(
            r"\b(down|decrease|kam|lower|reduce)\b", lowered
        ):
            args["action"] = "up"
        elif re.search(r"\b(down|decrease|kam|lower|reduce|dhire)\b", lowered):
            args["action"] = "down"
        elif re.search(r"\b(bright|brighten|brighter|roshni)\b", lowered):
            args["action"] = "up"
        elif re.search(r"\b(dim)\b", lowered):
            args["action"] = "down"
        else:
            return None
        return self._tool("system.brightness", args, "brightness")

    def _rule_volume(self, text: str) -> Plan | None:
        lowered = text.lower()
        if not re.search(r"\b(volume|mute|unmute|louder|aawaz|awaaz|speaker volume)\b", lowered) \
                and not re.search(r"\bsound\s+(?:up|down)\b|\blower\s+(?:your\s+)?voice\b|"
                                  r"\bloud\b|\bkam\b.*\b(awaz|awaaz|aawaz|volume)\b", lowered):
            return None
        # Microphone levels and notification sounds are not the master output level.
        if re.search(r"\b(mic|microphone|record|ringtone|notification sounds?)\b", lowered):
            return None
        args: dict[str, Any] = {"step": 10}
        explicit = re.search(r"\b(\d{1,3})\s*(?:percent|per\s*cent|%)?", lowered) if re.search(
            r"\d+\s*(?:percent|%)|\b(?:to|at)\s+\d+\b", lowered) else None
        reading = re.search(r"\b(what(?:'s| is)|how\s+much|current|kitni|batao|check)\b", lowered)
        if re.search(r"\bmute\b|\bchup\b", lowered) and not re.search(r"\bunmute\b|\bnahi\b", lowered):
            args["action"] = "mute"
        elif re.search(r"\bunmute\b|\bmute\s+hata", lowered):
            args["action"] = "unmute"
        elif explicit and 0 <= int(explicit.group(1)) <= 100:
            args["action"] = "set"
            args["percent"] = int(explicit.group(1))
        elif reading and not re.search(r"\b(up|down|badha|kam|increase|decrease|loud)\b", lowered):
            args["action"] = "get"
        elif re.search(r"\b(up|increase|badha|raise|louder|zyada)\b", lowered) and not re.search(
            r"\b(down|decrease|kam|lower|reduce)\b", lowered
        ):
            args["action"] = "up"
        elif re.search(r"\b(down|decrease|kam|lower|reduce|dhire)\b", lowered):
            args["action"] = "down"
        else:
            return None
        return self._tool("system.volume", args, "volume")

    def _rule_lock(self, text: str) -> Plan | None:
        if re.search(r"\block\b.*\b(computer|laptop|screen|pc|workstation|windows)\b",
                     text, re.I) or re.search(r"\block\s+kar\s+do\b", text, re.I):
            return self._tool("system.lock", {}, "lock")
        return None

    def _rule_screenshot(self, text: str) -> Plan | None:
        if re.search(r"\b(take\s+(?:a\s+)?screenshot|capture\s+(?:the\s+)?screen|"
                     r"screen\s+shot|screenshot\s+le\s+lo)\b", text, re.I):
            return self._tool("system.screenshot", {}, "screenshot")
        return None

    def _rule_running_apps(self, text: str) -> Plan | None:
        if re.search(r"\bwhat(?:'s| is)\s+running\b", text, re.I) or (
            re.search(r"\b(running|open)\b", text, re.I)
            and re.search(r"\b(applications?|apps?|windows?|programs?|processes)\b", text, re.I)
            and not re.search(r"\b(open|launch|start)\s+[A-Za-z]", text, re.I)
        ):
            return self._tool("app.list_running", {}, "list_running")
        return None

    def _rule_configured_apps(self, text: str) -> Plan | None:
        if re.search(r"\b(which|what|list)\b", text, re.I) and re.search(
            r"\bapps?\b|\bapplications?\b", text, re.I
        ) and re.search(r"\b(open|installed|available|configured|can you)\b", text, re.I):
            return self._tool("app.list_configured", {}, "list_configured")
        return None

    def _rule_close_app(self, text: str) -> Plan | None:
        match = re.match(r"^(?:close|quit|exit|band\s+karo)\s+(.+)$", text.strip(), re.I)
        if not match:
            return None
        name = _clean_name(match.group(1))
        if name.lower() in {"this", "this app", "this application", "it"}:
            return self._reply("Which application should I close?", "close_app")
        return self._tool("app.close", {"name": name}, "close_app")

    def _rule_switch_app(self, text: str) -> Plan | None:
        match = re.match(r"^(?:switch\s+to|focus(?:\s+on)?|bring\s+up|go\s+to)\s+(.+)$",
                         text.strip(), re.I)
        if not match:
            return None
        return self._tool("app.switch", {"name": _clean_name(match.group(1))}, "switch_app")

    def _rule_open_site(self, text: str) -> Plan | None:
        match = re.match(r"^open\s+(.+)$", text.strip(), re.I)
        if not match:
            return None
        name = _clean_name(match.group(1)).lower()
        url = self._site_url(name)
        if url:
            return self._tool("browser.open_url", {"url": url}, "open_site")
        if re.match(r"^[\w-]+(\.[\w-]+)+$", name):
            return self._tool("browser.open_url", {"url": f"https://{name}"}, "open_site")
        return None

    def _site_url(self, name: str) -> str | None:
        """Saved aliases first, then the built-in list.

        The user's own sites (college portal, a repository) live in the database
        and are editable in Settings, so they must be consulted before the
        hard-coded defaults.
        """
        wanted = " ".join(name.lower().split())
        if not wanted:
            return None
        try:
            rows = self.repo.list_site_aliases()
        except Exception:  # noqa: BLE001 - a broken table must not break planning
            rows = []
        for row in rows:
            candidates = [row["name"].lower(), *[str(a).lower() for a in row.get("aliases", [])]]
            if wanted in candidates:
                return row["url"]
        return SITES.get(wanted)

    def _rule_browser_intent(self, text: str) -> Plan | None:
        """'Open X and search for Y', 'open Instagram in Edge', 'open the shortcut'.

        Runs before the plain open-app and open-site rules, otherwise
        'open Chrome and search for recursion' loses everything after 'Chrome'.
        """
        stripped = text.strip()

        shortcut = re.match(
            r"^(?:open|launch|kholo|khol)\s+(?:the\s+|my\s+|ek\s+)?(.+?)"
            r"\s*(?:shortcut|short\s*cut|lnk|shortcut\s+pe)\s*$",
            stripped, re.I,
        ) or re.match(r"^(.+?)\s+shortcut\s+(?:kholo|open\s+karo|khol)\s*(?:na|do|deo)?$",
                      stripped, re.I)
        if shortcut:
            name = _clean_name(shortcut.group(1))
            if name:
                return self._tool("shortcuts.open", {"name": name}, "open_shortcut")

        browser = _browser_in(stripped)

        combined = re.match(
            r"^(?:open|launch|start)\s+(.+?)\s*(?:(?:and|then|phir|aur)\s+)?"
            r"(?:search|google|look\s*up|dhundo|khojo)\s+(?:for\s+|about\s+)?(.+)$",
            stripped, re.I,
        )
        if combined:
            subject, tail = combined.group(1), combined.group(2)
            engine, query = _search_topic(tail)
            # "open YouTube search for X" names the site first and says nothing
            # about where to search, so the words belong to YouTube rather than a
            # Google results page.
            if engine == "google":
                engine = _engine_in(subject) or engine
            named_browser = browser or _browser_name(subject)
            if not query:
                return None
            if named_browser:
                return self._tool(
                    "browser.search",
                    {"query": query, "browser": named_browser, "engine": engine},
                    "open_browser_search",
                )
            site = _clean_name(subject).lower()
            if self._site_url(site):
                return self._tool(
                    "browser.open_site", {"name": site, "query": query, "browser": ""},
                    "open_site_search",
                )
            return None

        site_in_browser = re.match(
            r"^(?:open|launch|kholo)\s+(.+?)\s*(?:in|using|me|pe|par)\s+"
            r"(chrome|edge|msedge|firefox|brave|google chrome|microsoft edge)\b",
            stripped, re.I,
        ) or re.match(
            r"^(?:open|launch)\s+(chrome|edge|msedge|firefox|brave|google chrome|microsoft edge)"
            r"\s*(?:and|then|aur)\s+(?:open\s+)?(.+)$",
            stripped, re.I,
        )
        if site_in_browser:
            first, second = site_in_browser.groups()
            # The two patterns capture the browser in different positions.
            if BROWSER_WORDS.get((first or "").lower()):
                browser_name, site_name = BROWSER_WORDS[first.lower()], second
            elif BROWSER_WORDS.get((second or "").lower()):
                browser_name, site_name = BROWSER_WORDS[second.lower()], first
            else:
                return None
            if not site_name or not site_name.strip():
                return None
            url = self._site_url(_clean_name(site_name).lower())
            if url:
                return self._tool("browser.open_url", {"url": url, "browser": browser_name},
                                  "open_site_in_browser")
            return self._tool("browser.open_site", {"name": _clean_name(site_name),
                                                    "browser": browser_name},
                              "open_site_in_browser")

        if browser and re.match(r"^(?:open|launch|start)\s+", stripped, re.I):
            return None  # a plain 'open Chrome' belongs to app.open

        return None

    def _rule_chained(self, text: str) -> Plan | None:
        """Two-action requests that really do need ordering."""
        stripped = text.strip()
        # "open my latest screenshot" wants the file that already exists, so a
        # fresh capture must not be taken just because both words appear.
        if re.search(r"\b(screenshot|screen)\b", stripped, re.I) and re.search(
                r"\b(open|show|dekh|dikha|dikhao)\b", stripped, re.I) and not re.search(
                r"\b(latest|newest|last|recent|previous|my|earlier)\b", stripped, re.I):
            return self._sequence(
                [("system.screenshot", {}),
                 ("files.open_recent", {"kind": "screenshot", "within_days": 1})],
                "screenshot_and_open",
            )
        folder = re.match(
            r"^create\s+(?:a\s+|the\s+)?(?:new\s+)?folder\s+(?:named\s+|called\s+|name\s+)?"
            r"[\"']?([^\"']+?)[\"']?\s*(?:in\s+(.+?)\s*)?(?:and\s+open\s+it\s*)?$",
            stripped, re.I,
        )
        if folder:
            name = folder.group(1).strip(" .,!?\"'")
            parent = (folder.group(2) or "").strip()
            steps: list[tuple[str, dict[str, Any]]] = [
                ("files.create_folder", {"parent": parent, "name": name})
            ]
            if re.search(r"\band\s+open\s+it\b", stripped, re.I):
                steps.append(("files.open_folder", {"folder": parent or "documents"}))
            return self._sequence(steps, "create_folder")
        return None

    def _rule_open_folder(self, text: str) -> Plan | None:
        """'Open Downloads' must open the folder, not an app called Downloads."""
        match = re.match(
            r"^(?:open|kholo|khol)\s+(?:my\s+|the\s+)?(downloads?|desktop|documents?|docs|"
            r"pictures|photos|videos?|music|screenshots?|home)\s*(?:folder)?$",
            text.strip(), re.I,
        )
        if not match:
            return None
        return self._tool("files.open_folder", {"folder": match.group(1).lower()}, "open_folder")

    def _rule_latest_file(self, text: str) -> Plan | None:
        if not re.search(r"\b(latest|newest|last|recent|just)\b", text, re.I):
            return None
        match = re.search(
            r"\b(latest|newest|last|recent|just)\b[^.]*?\b(screenshot|picture|photo|image|"
            r"file|document|pdf|presentation)\b", text, re.I)
        if not match:
            return None
        kind = match.group(2).lower()
        bucket = "screenshot" if kind == "screenshot" else (
            "image" if kind in ("picture", "photo", "image") else "document")
        return self._tool("files.open_recent", {"kind": bucket, "within_days": 30},
                          "open_latest_file")

    def _rule_where_saved(self, text: str) -> Plan | None:
        if re.search(r"\b(where|kahan|kidhar)\b", text, re.I) and re.search(
                r"\b(save[d]?|stored?|rakha|put|screenshot|file|it)\b", text, re.I):
            if re.search(r"\byou\b|\bjarvis\b|\b_saved\b|\bsaved\b|\bstored\b|\brakha\b", text, re.I):
                return self._tool("files.locate_last", {}, "where_saved")
        return None

    def _rule_open_app(self, text: str) -> Plan | None:
        stripped = text.strip()
        match = re.match(r"^(?:open|launch|start|kholo|khol)\s+(.+)$", stripped, re.I)
        if not match:
            # Hinglish puts the verb last: "mera VS Code open karo", "notepad kholo".
            match = re.match(
                r"^(.+?)\s+(?:open|launch|start|kholo|khol)\s*"
                r"(?:karo|kar do|do|deo|dena|na)?$",
                stripped, re.I)
        if not match:
            return None
        name = _clean_name(match.group(1))
        if not name:
            return None
        return self._tool("app.open", {"name": name}, "open_app")

    def _rule_web_search(self, text: str) -> Plan | None:
        stripped = text.strip()
        # Hinglish puts the site first: "youtube par database search karo".
        site_first = re.match(
            r"^(youtube|amazon|github)\s+(?:par|pe|on|me)\s+(.+?)\s*"
            r"(?:search|dhundo|khojo|google)\s*(?:karo|kar\s*do|do|deo|dena|na)?$",
            stripped, re.I,
        )
        if site_first:
            query = _clean_name(site_first.group(2))
            if query:
                return self._tool(
                    "browser.search",
                    {"query": query, "engine": SEARCH_ENGINES[site_first.group(1).lower()],
                     "browser": _browser_in(stripped) or ""},
                    "web_search",
                )
        match = re.match(
            r"^(?:search|google|look\s+up|browse)\b(?:\s+(?:the\s+web|online))?"
            r"(?:\s+(?:for|about|on|in))?\s+(.+)$",
            stripped, re.I,
        )
        if not match:
            match = re.search(r"\bsearch\s+(?:the\s+web|online)\s+for\s+(.+)$", stripped, re.I)
        if not match:
            return None
        engine, query = _search_topic(match.group(1))
        if not query:
            # "search YouTube" with no topic is just opening the site.
            return self._tool("browser.open_site", {"name": engine}, "open_site")
        return self._tool(
            "browser.search",
            {"query": query, "engine": engine, "browser": _browser_in(stripped) or ""},
            "web_search",
        )

    def _rule_memory(self, text: str) -> Plan | None:
        if re.match(r"^remember\b", text, re.I):
            body = re.sub(r"^remember\s+(?:that\s+)?", "", text, flags=re.I).strip()
            # "remember this: X" is the same as "remember X".
            body = re.sub(r"^(?:this|that|it|yeh|ye)\s*[:\-]\s*", "", body, flags=re.I).strip()
            body = body.strip(" .!?,;:")
            if not body or body.lower().replace(" ", "") in _BARE_MEMORY_POINTERS:
                # Storing the word "this" as a lifetime fact is worse than asking.
                return Plan(
                    reply="What should I remember, Ma'am? Say it and I will save it, "
                          "or type it in the memory box.",
                    intent="memory_capture",
                )
            match = re.match(r"(.+?)\s+(?:is|are|=)\s+(.+)", body, re.I)
            if match:
                key = re.sub(r"^my\s+", "", match.group(1).strip(), flags=re.I)
                return self._tool("memory.set", {"key": key, "value": match.group(2).strip()},
                                  "memory_set")
            key = "note-" + re.sub(r"[^a-z0-9]+", "-", body.lower()).strip("-")[:40]
            return self._tool("memory.set", {"key": key, "value": body.strip()}, "memory_set")
        if re.search(r"\b(what do you (know|remember)|memories|memory list|yaad)\b", text, re.I):
            if re.search(r"\b(know|about me|profile|stored|meri)\b", text, re.I):
                return self._tool("knowledge.list", {}, "knowledge_list")
            return self._tool("memory.list", {}, "memory_list")
        match = re.match(r"^forget\s+(?:about\s+)?(.+)$", text.strip(), re.I)
        if match:
            key = match.group(1).strip()
            if key.lower() in {"everything", "all", "sab"}:
                return self._tool("memory.forget", {}, "memory_forget")
            return self._tool("memory.forget", {"key": key}, "memory_forget")
        return None

    def _rule_integrations(self, text: str) -> Plan | None:
        if not re.search(r"\b(emails?|inbox|gmail|outlook|calendar|assignments?|moodle|github)\b",
                         text, re.I):
            return None
        if not re.search(r"\b(check|summar|show|upcoming|read|any|list|draft|important|"
                         r"deadlines?|tell)\b", text, re.I):
            return None
        lowered = text.lower()
        if "calendar" in lowered and ("assignment" in lowered or "moodle" in lowered):
            return self._tool("integrations.status", {}, "integrations")
        if "github" in lowered:
            provider = "github"
        elif "moodle" in lowered or "assignment" in lowered:
            provider = "moodle"
        elif "calendar" in lowered:
            provider = "google_calendar"
        elif "outlook" in lowered:
            provider = "outlook"
        elif "gmail" in lowered or "email" in lowered or "inbox" in lowered:
            provider = "gmail"
        else:
            provider = "google_calendar"
        return self._tool("integrations.summary", {"provider": provider}, "integrations")

    # ------------------------------------------------------------ model path
    #: Memory writes are never the model's call to make. qwen2.5:3b picked
    #: knowledge.remember for "which river is longest in India" and then failed
    #: its own argument validation, so the user got a storage error instead of an
    #: answer. Explicit "remember …" / "forget …" is handled by _rule_memory, and
    #: the memory dialog writes through the repository, so hiding these from the
    #: catalog costs nothing that is not already covered by a rule.
    MODEL_HIDDEN_TOOLS = frozenset({"knowledge.remember", "memory.set", "memory.forget"})

    def _stable_head(self) -> str:
        """Persona, tool catalog and rules — byte-identical on every request.

        Ollama re-reads any part of the prompt that differs from the last one, so
        keeping it frozen is what lets the second command in a conversation reuse
        the first command's reading. Built once per process for the same reason.

        The catalog is one short line per tool rather than a JSON dump of every
        pydantic schema. The full schema round-trip measured 3,826 tokens, and a
        cache miss on that costs ~85s of prefill on this hardware — the line form
        carries the same routing information (name, argument names, whether each
        is required, allowed values) in a fraction of the length. The model still
        answers in JSON; the executor validates against the real schema anyway.
        """
        if self._head is None:
            self._head = (
                f"{SYSTEM_PROMPT}\n\n"
                "You are the planner. Choose exactly one tool call or reply. "
                f"Tools are listed as name(arg:type, arg?:type) - purpose.\n"
                f"{self._catalog_lines()}\n\n"
                "Rules:\n"
                "- Use only listed tools and only their documented arguments.\n"
                "- Never fabricate telemetry, files, contacts, prices, emails or events.\n"
                "- Email/calendar/Moodle/GitHub reading is NOT IMPLEMENTED; reply honestly.\n"
                "- Never request purchases, sending WhatsApp messages, or security bypasses.\n"
                "- For any sensitive action the app will ask the user to confirm; do not claim it ran.\n"
                "- Match the user's language (English, Hindi or Hinglish). Be concise.\n"
                "- A question you can simply answer (introduce yourself, explain something, chat) "
                "is an action=reply, not a failed tool lookup.\n"
                "- Only pick a tool that changes something (remember, forget, write, launch, "
                "delete, send) when the user asks for that change. Asking what two things "
                "differ is never a reason to store or send anything.\n"
                "- Output JSON only, one of:\n"
                '  {"action":"tool","tool":"name","args":{...},"why":"short reason"}\n'
                '  {"action":"reply","text":"concise answer"}\n'
            )
        return self._head

    def _catalog_lines(self) -> str:
        lines = []
        for tool in self.registry.all():
            if tool.name in self.MODEL_HIDDEN_TOOLS:
                continue
            schema = tool.args_model.model_json_schema()
            required = set(schema.get("required", []))
            args = []
            for key, meta in schema.get("properties", {}).items():
                if not isinstance(meta, dict):
                    continue
                allowed = meta.get("enum") or (
                    meta.get("items", {}).get("enum") if isinstance(meta.get("items"), dict) else None
                )
                kind = "|".join(str(value) for value in allowed) if allowed else _scalar_type(meta)
                args.append(f"{key}{'' if key in required else '?'}:{kind}")
            lines.append(f"{tool.name}({', '.join(args)}) - {_first_sentence(tool.description)}")
        return "\n".join(lines)

    def _context_tail(self) -> str:
        """What changes between requests: remembered preferences, then the clock."""
        parts = []
        memories = self.repo.get_memory() or []
        if memories:
            parts.append("Known user preferences: " + "; ".join(
                f"{row['key']}={row['value']}" for row in memories[:12]
            ))
        parts.append(clock_context(str(self.repo.get_setting('assistant.user_name') or '')))
        return "\n".join(parts)

    def warm_messages(self) -> list[dict[str, str]]:
        """The prompt a real command will send, for the keep-warm ping to prime."""
        return [
            {"role": "system", "content": f"{self._stable_head()}\n{self._context_tail()}"},
            {"role": "user", "content": "ok"},
        ]

    async def _model_plan(self, text: str,
                          history: list[dict[str, str]]) -> tuple[Plan | None, str | None]:
        system = f"{self._stable_head()}\n{self._context_tail()}"
        messages = [{"role": "system", "content": system}]
        for item in history[-4:]:
            if item.get("role") in {"user", "assistant"} and item.get("content"):
                messages.append({"role": item["role"], "content": str(item["content"])[:800]})
        messages.append({"role": "user", "content": text})
        try:
            raw = await self.router.complete(messages, json_mode=True)
        except ModelUnavailable as exc:
            # str(exc) already carries the specific reason (server unreachable,
            # model not installed, timeout, invalid reply).
            return None, str(exc)
        provider = self.router.active_name()
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S).strip()
        try:
            data = json.loads(cleaned)
        except (ValueError, TypeError):
            data = None
        if data is None:
            # A 3B model does not always honour json mode. If it answered in
            # prose, that prose is the reply; only discard structured-looking
            # garbage.
            if cleaned and not cleaned.startswith(("{", "[")):
                return Plan(reply=cleaned[:1500], planner=f"model:{provider}"), None
            return None, (
                f"The model {provider.split(':')[-1] if ':' in provider else provider} "
                "returned an unreadable reply. Try rephrasing."
            )
        if not isinstance(data, dict):
            return None, "The model returned a reply that was not in the expected shape."
        action = str(data.get("action", "")).lower()
        if action == "tool":
            tool_name = str(data.get("tool", ""))
            if tool_name in self.MODEL_HIDDEN_TOOLS and not re.search(
                r"\b(remember|store|save|note\s+down|forget|yaad|rakhna)\b", text, re.I
            ):
                # It reached for a memory write on a plain question. Nothing is
                # stored, and the question is not answered with a storage error.
                return Plan(
                    reply=str(data.get("text") or "").strip() or (
                        "I did not save anything. Say 'remember …' when you want "
                        "it kept."
                    ),
                    planner=f"model:{provider}",
                ), None
            if not self.registry.has(tool_name):
                return Plan(
                    reply="I cannot run that action because no matching tool is registered.",
                    planner=f"model:{provider}",
                ), None
            try:
                validated = self.registry.validate(tool_name, data.get("args") or {})
            except ToolValidationError as exc:
                return Plan(reply=f"I did not run that: {exc}",
                            planner=f"model:{provider}"), None
            return Plan(
                kind="tool", tool=tool_name, args=validated.model_dump(),
                explanation=str(data.get("why", ""))[:300],
                planner=f"model:{provider}", intent=tool_name,
            ), None
        reply = str(data.get("text", "")).strip()
        if reply or action in {"reply", "clarify", "answer"}:
            if reply:
                return Plan(reply=reply, planner=f"model:{provider}"), None
        return None, None
