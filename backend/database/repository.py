"""Typed data access helpers used by the API and tools."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from config import settings
from database.db import Database


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(raw: str | None, default: Any) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


DEFAULT_APPS: list[dict[str, Any]] = [
    {
        "name": "VS Code",
        "aliases": ["vs code", "vscode", "visual studio code", "code editor", "code"],
        "executable": "Code.exe",
        "paths": [
            r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
            r"%PROGRAMFILES%\Microsoft VS Code\Code.exe",
        ],
    },
    {
        "name": "Chrome",
        "aliases": ["chrome", "google chrome", "browser"],
        "executable": "chrome.exe",
        "paths": [
            r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
            r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        ],
    },
    {
        "name": "Edge",
        "aliases": ["edge", "microsoft edge"],
        "executable": "msedge.exe",
        "paths": [
            r"%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe",
            r"%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe",
        ],
    },
    {
        "name": "Firefox",
        "aliases": ["firefox", "mozilla firefox"],
        "executable": "firefox.exe",
        "paths": [r"%PROGRAMFILES%\Mozilla Firefox\firefox.exe"],
    },
    {
        "name": "File Explorer",
        "aliases": ["file explorer", "explorer", "windows explorer", "files"],
        "executable": "explorer.exe",
        "paths": [],
    },
    {
        "name": "Notepad",
        "aliases": ["notepad", "text editor"],
        "executable": "notepad.exe",
        "paths": [],
    },
    {
        "name": "Calculator",
        "aliases": ["calculator", "calc"],
        "executable": "calc.exe",
        "paths": [],
    },
    {
        "name": "Paint",
        "aliases": ["paint", "mspaint"],
        "executable": "mspaint.exe",
        "paths": [],
    },
    {
        "name": "PowerPoint",
        "aliases": ["powerpoint", "ppt", "slides"],
        "executable": "POWERPNT.EXE",
        "paths": [r"%PROGRAMFILES%\Microsoft Office\root\Office16\POWERPNT.EXE"],
    },
    {
        "name": "Word",
        "aliases": ["word", "microsoft word"],
        "executable": "WINWORD.EXE",
        "paths": [r"%PROGRAMFILES%\Microsoft Office\root\Office16\WINWORD.EXE"],
    },
    {
        "name": "Excel",
        "aliases": ["excel", "microsoft excel"],
        "executable": "EXCEL.EXE",
        "paths": [r"%PROGRAMFILES%\Microsoft Office\root\Office16\EXCEL.EXE"],
    },
    {
        "name": "Terminal",
        "aliases": ["terminal", "windows terminal", "powershell"],
        "executable": "wt.exe",
        "paths": [],
    },
    {
        "name": "Command Prompt",
        "aliases": ["command prompt", "cmd"],
        "executable": "cmd.exe",
        "paths": [],
    },
    {
        "name": "Task Manager",
        "aliases": ["task manager", "taskmgr"],
        "executable": "taskmgr.exe",
        "paths": [],
    },
    {
        "name": "Settings",
        "aliases": ["settings", "windows settings"],
        "kind": "uri",
        "uri": "ms-settings:",
    },
    {
        "name": "Spotify",
        "aliases": ["spotify", "music"],
        "executable": "Spotify.exe",
        "paths": [r"%APPDATA%\Spotify\Spotify.exe"],
    },
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "ai.provider": "auto",
    "ai.ollama_model": settings.ollama_model,
    "ai.temperature": 0.2,
    "voice.tts_enabled": True,
    "voice.voice_name": "",
    "voice.sapi_voice": "",
    "voice.language": "en-IN",
    "voice.wake_word": "hey jarvis",
    "voice.wake_word_enabled": False,
    "voice.auto_listen": False,
    "voice.rate": 0,
    "voice.pitch": 0,
    "voice.style": "jarvis",
    "reminders.enabled": True,
    "privacy.memory_enabled": True,
    "privacy.task_history": True,
    "privacy.telemetry": False,
    "monitor.enabled": True,
    "monitor.interval_ms": 2000,
    "notifications.enabled": True,
    "permissions.auto_confirm_moderate": settings.auto_confirm_moderate,
    "apps.favorites": ["VS Code", "Chrome", "File Explorer", "PowerPoint"],
    "theme.name": "blue",
    "theme.accent": "#ff8a1e",
    "theme.reduced_motion": False,
    "startup.auto_start": False,
    "output.directory": str(settings.output_dir),
    "projects.directory": str(settings.project_root),
    # Empty means "ask the shell", which is what honours OneDrive redirection.
    "paths.screenshots": "",
    "browser.default": "chrome",
    "assistant.user_name": "Aradhya",
    "phone.device_name": "",
}

# Seed knowledge from the user's own brief. Every row is marked verified and
# user-sourced so the UI can show where a fact came from; anything the assistant
# only infers is written later with verified=False.
DEFAULT_KNOWLEDGE: list[dict[str, Any]] = [
    {"key": "name", "value": "Aradhya Srivastava", "category": "profile",
     "note": "Told to JARVIS directly."},
    {"key": "preferred_language", "value": "Hinglish for technical explanations",
     "category": "preference"},
    {"key": "learning_style", "value": "Step-by-step explanations", "category": "preference"},
    {"key": "dev_environment", "value": "Windows and VS Code", "category": "preference"},
    {"key": "solution_bias", "value": "Free-first and local-first where practical",
     "category": "preference"},
    {"key": "education.program", "value": "IIT Patna BS-MS, AI and Cybersecurity",
     "category": "education", "verified": False,
     "note": "Reported by the user; semester dates still need verifying."},
    {"key": "education.semester", "value": "Third semester (reported 2026)",
     "category": "education", "verified": False, "as_of": "2026"},
    {"key": "education.cgpa", "value": "8.74 (second semester)", "category": "progress",
     "verified": False, "as_of": "second semester",
     "note": "Historical. Treat as outdated until re-reported."},
    {"key": "goal.gate", "value": "GATE 2028 Data Science & AI (DA), target 75+",
     "category": "goal"},
    {"key": "goal.post_gate", "value": "M.Tech in CS/AI/Data Science at IISc or IITs",
     "category": "goal"},
    {"key": "project.jarvis", "value": "JARVIS Windows desktop AI assistant (this app)",
     "category": "project"},
    {"key": "project.ara9", "value": "ARAGE9 Movie Recommendation System",
     "category": "project", "verified": False},
    {"key": "project.scanverse", "value": "ScanVerse Smart Document Scanner",
     "category": "project", "verified": False},
    {"key": "project.scamshield", "value": "ScamShield scam-detection project",
     "category": "project", "verified": False},
    {"key": "project.araflow", "value": "AraFlow personal AI voice assistant",
     "category": "project", "verified": False},
    {"key": "learning.aiml", "value": "scikit-learn and PyTorch, project-based AI/ML",
     "category": "progress", "verified": False},
]

# The nine buckets the assistant keeps facts in. Kept separate on purpose: a
# guess, an app the user authorised, and a fact they stated outright must not
# look equally trustworthy in the UI or in a prompt.
KNOWLEDGE_CATEGORIES: tuple[str, ...] = (
    "profile", "education", "schedule", "progress", "goal",
    "project", "preference", "authorized", "unverified",
)

# Where a fact came from. "inferred" and "imported" rows are never presented as
# something the user stated.
SOURCES: tuple[str, ...] = ("user", "inferred", "imported", "app")

# Aliases the user can rename or delete in Settings. Kept small and generic;
# anything personal (college portal, repositories) is added by the user.
DEFAULT_SITES: list[dict[str, Any]] = [
    {"name": "google", "url": "https://www.google.com/", "aliases": ["search"]},
    {"name": "youtube", "url": "https://www.youtube.com/", "aliases": ["yt"]},
    {"name": "gmail", "url": "https://mail.google.com/", "aliases": ["mail"]},
    {"name": "github", "url": "https://github.com/", "aliases": []},
    {"name": "instagram", "url": "https://www.instagram.com/", "aliases": ["insta"]},
    {"name": "chatgpt", "url": "https://chat.openai.com/", "aliases": []},
    {"name": "linkedin", "url": "https://www.linkedin.com/", "aliases": []},
    {"name": "netflix", "url": "https://www.netflix.com/", "aliases": []},
    {"name": "amazon", "url": "https://www.amazon.in/", "aliases": []},
    {"name": "whatsapp web", "url": "https://web.whatsapp.com/", "aliases": ["whatsapp"]},
    {"name": "google maps", "url": "https://www.google.com/maps", "aliases": ["maps"]},
    {"name": "stack overflow", "url": "https://stackoverflow.com/", "aliases": ["so"]},
]


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ setup
    def seed(self) -> None:
        now = utc_now()
        for spec in DEFAULT_APPS:
            self.db.execute(
                "INSERT OR IGNORE INTO app_shortcuts (name, spec_json, builtin, added_at) "
                "VALUES (?, ?, 1, ?)",
                (spec["name"], _dumps(spec), now),
            )
        for site in DEFAULT_SITES:
            self.db.execute(
                "INSERT OR IGNORE INTO site_aliases (name, url, aliases, builtin, added_at) "
                "VALUES (?, ?, ?, 1, ?)",
                (site["name"], site["url"], _dumps(site["aliases"]), now),
            )
        # Ignored on conflict so an edited or deleted fact is never re-seeded
        # back over the user's own version on the next start.
        for item in DEFAULT_KNOWLEDGE:
            self.db.execute(
                "INSERT OR IGNORE INTO memory (key, value, kind, category, source, verified, "
                "as_of, note, created_at, updated_at) VALUES (?, ?, ?, ?, 'user', ?, ?, ?, ?, ?)",
                (
                    item["key"],
                    _dumps(item["value"]),
                    item["category"],
                    item["category"],
                    int(bool(item.get("verified", True))),
                    item.get("as_of"),
                    item.get("note"),
                    now,
                    now,
                ),
            )
        if self.get_setting("security.device_id") is None:
            self.set_setting("security.device_id", str(uuid.uuid4()))

    def prune(self) -> None:
        self.db.execute(
            "DELETE FROM tasks WHERE id NOT IN "
            "(SELECT id FROM tasks ORDER BY created_at DESC LIMIT ?)",
            (settings.max_tasks_kept,),
        )
        self.db.execute(
            "DELETE FROM task_events WHERE task_id NOT IN (SELECT id FROM tasks)"
        )
        self.db.execute(
            "DELETE FROM audit_log WHERE id NOT IN "
            "(SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)",
            (settings.max_audit_entries,),
        )
        self.db.execute(
            "DELETE FROM messages WHERE id NOT IN "
            "(SELECT id FROM messages ORDER BY id DESC LIMIT 400)"
        )

    # --------------------------------------------------------------- settings
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return DEFAULT_SETTINGS.get(key, default)
        return _loads(row["value"], default)

    def set_setting(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, _dumps(value), utc_now()),
        )

    def all_settings(self) -> dict[str, Any]:
        merged = dict(DEFAULT_SETTINGS)
        for row in self.db.query("SELECT key, value FROM settings"):
            merged[row["key"]] = _loads(row["value"], None)
        return merged

    def public_settings(self) -> dict[str, Any]:
        """Everything the interface may show: the signing secret stays inside.

        ``security.salt`` is the HMAC key behind device pairing, so a page that
        can read settings could mint its own token with it.
        """
        return {key: value for key, value in self.all_settings().items()
                if not key.startswith("security.")}

    # -------------------------------------------------------------- messages
    def add_message(self, role: str, content: str, task_id: str | None = None) -> dict[str, Any]:
        now = utc_now()
        row_id = self.db.execute(
            "INSERT INTO messages (at, role, content, task_id) VALUES (?, ?, ?, ?)",
            (now, role, content, task_id),
        )
        return {"id": row_id, "at": now, "role": role, "content": content, "task_id": task_id}

    def list_messages(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        )
        return list(reversed(rows))

    def clear_messages(self) -> None:
        self.db.execute("DELETE FROM messages")

    # ----------------------------------------------------------------- tasks
    def create_task(
        self,
        text: str,
        source: str,
        tool: str | None,
        args: dict[str, Any] | None,
        risk: str | None,
        status: str,
        planner: str,
        intent: str | None = None,
        confirmation_token: str | None = None,
        steps: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        task_id = uuid.uuid4().hex[:12]
        now = utc_now()
        self.db.execute(
            "INSERT INTO tasks (id, text, source, intent, tool, args_json, risk, status, "
            "planner, created_at, confirmation_token, steps_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, text, source, intent, tool, _dumps(args or {}), risk, status,
             planner, now, confirmation_token,
             _dumps(steps) if steps and len(steps) > 1 else None),
        )
        self.add_task_event(task_id, "info", f"Created ({status})")
        return self.get_task(task_id)  # type: ignore[return-value]

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "status", "result_json", "error", "started_at", "finished_at",
            "cancel_requested", "confirmation_token", "risk", "tool", "args_json",
        }
        updates = {}
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"Unsupported task field: {key}")
            updates[key] = _dumps(value) if key in {"result_json", "args_json"} else value
        if not updates:
            return self.get_task(task_id)
        assignments = ", ".join(f"{key} = ?" for key in updates)
        self.db.execute(
            f"UPDATE tasks SET {assignments} WHERE id = ?",
            (*updates.values(), task_id),
        )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _task_row(row) if row else None

    def confirmation_token(self, task_id: str) -> str | None:
        """Read the approval token without putting it in any task row.

        Only the executor, which is about to compare it, is allowed to see this.
        """
        row = self.db.query_one(
            "SELECT confirmation_token FROM tasks WHERE id = ?", (task_id,))
        return row["confirmation_token"] if row else None

    def list_tasks(self, limit: int = 40, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.db.query(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [_task_row(row) for row in rows]

    def running_task_texts(self) -> list[str]:
        rows = self.db.query(
            "SELECT text FROM tasks WHERE status IN ('running', 'pending', 'awaiting_confirmation')"
        )
        return [row["text"].strip().lower() for row in rows]

    def add_task_event(self, task_id: str, level: str, message: str) -> None:
        self.db.execute(
            "INSERT INTO task_events (task_id, at, level, message) VALUES (?, ?, ?, ?)",
            (task_id, utc_now(), level, message),
        )

    def task_events(self, task_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        )

    def recover_stale_tasks(self) -> int:
        """Tasks cannot survive a backend restart."""
        rows = self.db.query(
            "SELECT id FROM tasks WHERE status IN ('pending', 'running')"
        )
        for row in rows:
            self.update_task(
                row["id"],
                status="failed",
                error="Backend restarted while the task was running.",
                finished_at=utc_now(),
            )
            self.add_task_event(row["id"], "error", "Interrupted by backend restart")
        return len(rows)

    def delete_task(self, task_id: str) -> None:
        self.db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    # --------------------------------------------------------------- devices
    def upsert_device(
        self, device_id: str, name: str, token_hash: str, info: dict[str, Any]
    ) -> None:
        self.db.execute(
            "INSERT INTO devices (id, name, token_hash, paired_at, last_seen, info_json) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "name = excluded.name, token_hash = excluded.token_hash, last_seen = excluded.last_seen, "
            "info_json = excluded.info_json, revoked = 0",
            (device_id, name, token_hash, utc_now(), utc_now(), _dumps(info)),
        )

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM devices WHERE id = ?", (device_id,))
        if not row:
            return None
        row["info"] = _loads(row.pop("info_json"), {})
        return row

    def list_devices(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id, name, paired_at, last_seen, revoked FROM devices")
        return rows

    def touch_device(self, device_id: str) -> None:
        self.db.execute(
            "UPDATE devices SET last_seen = ? WHERE id = ?", (utc_now(), device_id)
        )

    def revoke_device(self, device_id: str) -> None:
        self.db.execute("UPDATE devices SET revoked = 1 WHERE id = ?", (device_id,))

    # --------------------------------------------------------- phone commands
    def create_phone_command(self, action: str, args: dict[str, Any]) -> str:
        command_id = uuid.uuid4().hex[:12]
        self.db.execute(
            "INSERT INTO phone_commands (id, action, args_json, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (command_id, action, _dumps(args), utc_now()),
        )
        return command_id

    def finish_phone_command(
        self, command_id: str, status: str, result: dict[str, Any]
    ) -> None:
        self.db.execute(
            "UPDATE phone_commands SET status = ?, result_json = ?, finished_at = ? WHERE id = ?",
            (status, _dumps(result), utc_now(), command_id),
        )

    # --------------------------------------------------------- notifications
    def add_notification(self, source: str, title: str, body: str) -> dict[str, Any]:
        now = utc_now()
        row_id = self.db.execute(
            "INSERT INTO notifications (at, source, title, body) VALUES (?, ?, ?, ?)",
            (now, source, title, body),
        )
        return {"id": row_id, "at": now, "source": source, "title": title, "body": body, "read": 0}

    def list_notifications(self, limit: int = 40) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)
        )

    def mark_notifications_read(self) -> None:
        self.db.execute("UPDATE notifications SET read = 1 WHERE read = 0")

    # ------------------------------------------------------------------ apps
    def list_apps(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM app_shortcuts ORDER BY name")
        apps = []
        for row in rows:
            spec = _loads(row["spec_json"], {})
            spec["builtin"] = bool(row["builtin"])
            apps.append(spec)
        return apps

    def save_app(self, spec: dict[str, Any], builtin: bool = False) -> None:
        self.db.execute(
            "INSERT INTO app_shortcuts (name, spec_json, builtin, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET spec_json = excluded.spec_json",
            (spec["name"], _dumps(spec), int(builtin), utc_now()),
        )

    def delete_app(self, name: str) -> bool:
        row = self.db.query_one(
            "SELECT builtin FROM app_shortcuts WHERE name = ?", (name,)
        )
        if row is None or row["builtin"]:
            return False
        self.db.execute("DELETE FROM app_shortcuts WHERE name = ?", (name,))
        return True

    # ------------------------------------------------------------------ misc
    def audit(
        self, actor: str, action: str, risk: str, detail: dict[str, Any], outcome: str
    ) -> None:
        self.db.execute(
            "INSERT INTO audit_log (at, actor, action, risk, detail_json, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (utc_now(), actor, action, risk, _dumps(detail), outcome),
        )

    def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        for row in rows:
            row["detail"] = _loads(row.pop("detail_json"), {})
        return rows

    def get_memory(self, key: str | None = None) -> Any:
        if key:
            row = self.db.query_one("SELECT * FROM memory WHERE key = ?", (key,))
            return _loads(row["value"], None) if row else None
        rows = self.db.query("SELECT * FROM memory ORDER BY key")
        for row in rows:
            row["value"] = _loads(row["value"], None)
        return rows

    def set_memory(self, key: str, value: Any, kind: str = "preference") -> None:
        self.db.execute(
            "INSERT INTO memory (key, value, kind, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, kind = excluded.kind, "
            "updated_at = excluded.updated_at",
            (key, _dumps(value), kind, utc_now()),
        )

    def delete_memory(self, key: str) -> None:
        self.db.execute("DELETE FROM memory WHERE key = ?", (key,))

    def clear_memory(self) -> None:
        self.db.execute("DELETE FROM memory")

    # ------------------------------------------------------- personal knowledge
    def remember(
        self,
        key: str,
        value: Any,
        *,
        category: str = "preference",
        source: str = "user",
        verified: bool = True,
        as_of: str | None = None,
        note: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        """Insert or update one knowledge entry.

        ``kind`` stays writable because older rows used it; ``category`` is the
        typed classification the new UI groups by.
        """
        now = utc_now()
        self.db.execute(
            "INSERT INTO memory (key, value, kind, category, source, verified, as_of, note, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, kind = excluded.kind, "
            "category = excluded.category, source = excluded.source, "
            "verified = excluded.verified, as_of = excluded.as_of, note = excluded.note, "
            "updated_at = excluded.updated_at",
            (
                key,
                _dumps(value),
                kind or category,
                category,
                source,
                int(verified),
                as_of,
                note,
                now,
                now,
            ),
        )
        return self.get_knowledge(key) or {}

    _KNOWLEDGE_COLUMNS = ("value", "kind", "category", "source", "verified", "as_of", "note")

    def update_knowledge(self, key: str, **fields: Any) -> dict[str, Any] | None:
        allowed = set(self._KNOWLEDGE_COLUMNS) | {"note", "as_of"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get_knowledge(key)
        assignments = ", ".join(f"{column} = ?" for column in updates)
        params: list[Any] = []
        for column, value in updates.items():
            params.append(_dumps(value) if column == "value" else int(value) if column == "verified" else value)
        params.append(utc_now())
        params.append(key)
        self.db.execute(
            f"UPDATE memory SET {assignments}, updated_at = ? WHERE key = ?", params
        )
        return self.get_knowledge(key)

    def _knowledge_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        row["value"] = _loads(row["value"], None)
        row["verified"] = bool(row["verified"])
        return row

    def get_knowledge(self, key: str) -> dict[str, Any] | None:
        return self._knowledge_row(
            self.db.query_one("SELECT * FROM memory WHERE key = ?", (key,))
        )

    def delete_knowledge(self, key: str) -> bool:
        if self.db.query_one("SELECT key FROM memory WHERE key = ?", (key,)) is None:
            return False
        self.db.execute("DELETE FROM memory WHERE key = ?", (key,))
        return True

    def list_knowledge(
        self, category: str | None = None, source: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memory"
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY category, key"
        return [self._knowledge_row(row) or row for row in self.db.query(sql, params)]

    # -------------------------------------------------------------- artifacts
    def add_artifact(
        self, kind: str, path: str, label: str | None = None, task_id: str | None = None
    ) -> int:
        return self.db.execute(
            "INSERT INTO artifacts (kind, path, label, task_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (kind, path, label, task_id, utc_now()),
        )

    def latest_artifact(self, kind: str | None = None) -> dict[str, Any] | None:
        if kind:
            return self.db.query_one(
                "SELECT * FROM artifacts WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)
            )
        return self.db.query_one("SELECT * FROM artifacts ORDER BY id DESC LIMIT 1")

    def list_artifacts(self, kind: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if kind:
            return self.db.query(
                "SELECT * FROM artifacts WHERE kind = ? ORDER BY id DESC LIMIT ?", (kind, limit)
            )
        return self.db.query("SELECT * FROM artifacts ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------- reminders
    def add_reminder(
        self, text: str, due_at: str, speak: bool = True, source: str = "manual"
    ) -> dict[str, Any]:
        reminder_id = self.db.execute(
            "INSERT INTO reminders (text, due_at, speak, source, created_at) VALUES (?, ?, ?, ?, ?)",
            (text, due_at, int(bool(speak)), source, utc_now()),
        )
        return self.get_reminder(int(reminder_id)) or {}

    def get_reminder(self, reminder_id: int) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        return _reminder_row(row)

    def list_reminders(self, include_finished: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM reminders"
        if not include_finished:
            sql += " WHERE cancelled = 0 AND fired = 0"
        sql += " ORDER BY cancelled, fired, due_at"
        return [_reminder_row(row) or {} for row in self.db.query(sql)]

    def due_reminders(self, now_iso: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM reminders WHERE cancelled = 0 AND fired = 0 AND due_at <= ? "
            "ORDER BY due_at",
            (now_iso,),
        )
        return [_reminder_row(row) or {} for row in rows]

    def next_reminder_due(self) -> str | None:
        """The soonest pending due-time, so the loop sleeps exactly that long
        instead of waking on a fixed tick."""
        row = self.db.query_one(
            "SELECT due_at FROM reminders WHERE cancelled = 0 AND fired = 0 "
            "ORDER BY due_at LIMIT 1"
        )
        return str(row["due_at"]) if row else None

    def mark_reminder_fired(self, reminder_id: int) -> None:
        self.db.execute(
            "UPDATE reminders SET fired = 1, fired_at = ? WHERE id = ?", (utc_now(), reminder_id)
        )

    def cancel_reminder(self, reminder_id: int) -> bool:
        if self.db.query_one("SELECT id FROM reminders WHERE id = ?", (reminder_id,)) is None:
            return False
        self.db.execute("UPDATE reminders SET cancelled = 1 WHERE id = ?", (reminder_id,))
        return True

    def delete_reminder(self, reminder_id: int) -> bool:
        if self.db.query_one("SELECT id FROM reminders WHERE id = ?", (reminder_id,)) is None:
            return False
        self.db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        return True

    # ---------------------------------------------------------- site aliases
    def list_site_aliases(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM site_aliases ORDER BY builtin DESC, name")
        for row in rows:
            row["aliases"] = _loads(row.get("aliases"), [])
            row["builtin"] = bool(row["builtin"])
        return rows

    def save_site_alias(
        self, name: str, url: str, aliases: Sequence[str] = (), builtin: bool = False
    ) -> None:
        self.db.execute(
            "INSERT INTO site_aliases (name, url, aliases, builtin, added_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET url = excluded.url, aliases = excluded.aliases",
            (name, url, _dumps(list(aliases)), int(builtin), utc_now()),
        )

    def delete_site_alias(self, name: str) -> bool:
        row = self.db.query_one("SELECT builtin FROM site_aliases WHERE name = ?", (name,))
        if row is None or row["builtin"]:
            return False
        self.db.execute("DELETE FROM site_aliases WHERE name = ?", (name,))
        return True

    def get_integration(self, provider: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM integration_config WHERE provider = ?", (provider,)
        )
        if not row:
            return None
        row["config"] = _loads(row.pop("config_json"), {})
        row["enabled"] = bool(row["enabled"])
        return row

    def set_integration(self, provider: str, config: dict[str, Any], enabled: bool) -> None:
        self.db.execute(
            "INSERT INTO integration_config (provider, config_json, enabled, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(provider) DO UPDATE SET "
            "config_json = excluded.config_json, enabled = excluded.enabled, "
            "updated_at = excluded.updated_at",
            (provider, _dumps(config), int(enabled), utc_now()),
        )

    def list_integrations(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM integration_config ORDER BY provider")
        for row in rows:
            row["config"] = _loads(row.pop("config_json"), {})
            row["enabled"] = bool(row["enabled"])
        return rows


def _task_row(row: dict[str, Any]) -> dict[str, Any]:
    task = dict(row)
    # Tasks are SELECT *, so the approval token has to come out here or it rides
    # along in /api/tasks, the socket snapshot and every task.updated event.
    task.pop("confirmation_token", None)
    task["args"] = _loads(task.pop("args_json", None), {})
    task["result"] = _loads(task.pop("result_json", None), None)
    task["cancel_requested"] = bool(task.get("cancel_requested"))
    steps = _loads(task.pop("steps_json", None), None)
    # A stored sequence always wins over the single tool, so a replayed task
    # cannot quietly drop the steps after the first one.
    task["steps"] = steps if isinstance(steps, list) and len(steps) > 1 else None
    return task


def _reminder_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["fired"] = bool(item.get("fired"))
    item["cancelled"] = bool(item.get("cancelled"))
    item["speak"] = bool(item.get("speak"))
    return item
