"""Risk classification and filesystem boundaries for tool execution."""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

from database.repository import Repository


class Risk(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class PermissionError_(Exception):
    """Raised when a tool targets a path outside the approved boundaries."""


def resolve_path(raw: str | Path) -> Path:
    expanded = os.path.expandvars(str(raw))
    return Path(expanded).expanduser().resolve()


def is_within(path: Path, root: Path) -> bool:
    try:
        return path == root or path.is_relative_to(root)
    except OSError:
        return False


def _blocked_roots() -> list[Path]:
    candidates = [
        os.environ.get("SystemRoot", r"C:\Windows"),
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramData", r"C:\ProgramData"),
        os.environ.get("APPDATA", r""),
        os.environ.get("LOCALAPPDATA", r""),
        str(Path.home() / ".ssh"),
        str(Path.home() / ".aws"),
        str(Path.home() / ".config"),
    ]
    return [resolve_path(p) for p in candidates if p]


def _secret_roots(blocked: list[Path]) -> list[Path]:
    """Where a *read* leaks a secret, as opposed to where a write would persist.

    LOCALAPPDATA itself is not in here: JARVIS renders speech into %TEMP%, which
    sits under it, so banning the whole tree would refuse the assistant's own
    files. The browser profile stores are named instead, because that is where
    saved passwords actually live.
    """
    local = os.environ.get("LOCALAPPDATA", "")
    roots = [path for path in blocked
             if not (local and is_within(path, resolve_path(local)))]
    if local:
        base = resolve_path(local)
        roots += [base / "Google" / "Chrome" / "User Data",
                  base / "Microsoft" / "Edge" / "User Data"]
    return roots


class PermissionPolicy:
    """Decides whether an action needs explicit user confirmation."""

    def __init__(self, repo: Repository, read_roots: list[Path], write_roots: list[Path]):
        self.repo = repo
        self.read_roots = read_roots
        self.write_roots = write_roots
        self._blocked = _blocked_roots()
        self._secret_paths = _secret_roots(self._blocked)

    @property
    def auto_confirm_moderate(self) -> bool:
        return bool(self.repo.get_setting("permissions.auto_confirm_moderate", False))

    def requires_confirmation(self, risk: Risk, confirm_override: bool | None) -> bool:
        if confirm_override is not None:
            return confirm_override
        if risk is Risk.HIGH:
            return True
        if risk is Risk.MODERATE:
            return not self.auto_confirm_moderate
        return False

    def ensure_readable(self, path: Path) -> Path:
        resolved = path.resolve()
        if any(is_within(resolved, blocked) for blocked in self._secret_paths):
            raise PermissionError_(
                f"Access denied: {resolved} holds credentials or system files.")
        if not any(is_within(resolved, root) for root in self.read_roots):
            raise PermissionError_(
                f"Access denied: {resolved} is outside the approved folders. "
                "Add it with JARVIS_EXTRA_ROOTS or move the file into an approved folder."
            )
        return resolved

    def ensure_writable(self, path: Path) -> Path:
        resolved = path.resolve()
        if any(is_within(resolved, blocked) for blocked in self._blocked):
            raise PermissionError_(f"Access denied: {resolved} is a protected system folder.")
        if not any(is_within(resolved, root) for root in self.write_roots):
            raise PermissionError_(
                f"Access denied: JARVIS may not write to {resolved}. Allowed: "
                + ", ".join(str(root) for root in self.write_roots)
            )
        return resolved

    def describe_boundaries(self) -> dict[str, list[str]]:
        return {
            "read": [str(p) for p in self.read_roots],
            "write": [str(p) for p in self.write_roots],
        }
