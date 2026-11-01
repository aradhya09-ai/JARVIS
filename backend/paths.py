"""Windows known-folder resolution.

Personal folders must be asked of the shell, not guessed from ``Path.home()``:
this account keeps Desktop, Documents and Pictures under OneDrive, so
``~/Desktop`` is not where the user's files actually are. Everything that needs
a user folder goes through :func:`known_folder` so the redirection is honoured
in one place instead of being re-derived per tool.
"""
from __future__ import annotations

import logging
import os
import string
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"

# FOLDERID_* values from knownfolders.h.
_FOLDER_IDS = {
    "desktop": "{B4BFCC3A-DB21-45E0-BDE6-542E581E5198}",
    "documents": "{FDD39AD0-2350-4D52-93FB-970643797A78}",
    "downloads": "{7D83EE9D-D76A-41DA-A1C7-986FA299F7F7}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "videos": "{18980B14-B20B-48D5-8933-B49658E0B606}",
    "screenshots": "{B7BEDE81-DF94-4682-A7D8-57A52620B86F}",
    "appdata": "{3EB685DB-65F9-4CCA-A880-03A572F49306}",
    "local_appdata": "{F1B32785-6FBA-4FCF-9D57-7E8ECFB2D620}",
    "start_menu": "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}",
    "programs": "{AEF48710-5E67-42CD-AA9F-71D8A362837F}",
}

# HKCU\...\Explorer\User Shell Folders value names, used when the shell API is
# unavailable and as a cross-check.
_REGULAR_PATHS = {
    "desktop": "Desktop",
    "documents": "Personal",
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "music": "My Music",
    "pictures": "My Pictures",
    "videos": "My Video",
    "screenshots": "{B7BEDE81-DF94-4682-A7D8-57A52620B86F}",
    "appdata": "AppData",
    "local_appdata": "Local AppData",
    "start_menu": "Start Menu",
    "programs": "Programs",
}

# What the user calls a folder, and the shell folder behind it.
FOLDER_ALIASES: dict[str, str] = {
    "desktop": "desktop",
    "my desktop": "desktop",
    "documents": "documents",
    "document": "documents",
    "my documents": "documents",
    "downloads": "downloads",
    "download": "downloads",
    "my downloads": "downloads",
    "pictures": "pictures",
    "images": "pictures",
    "photos": "pictures",
    "music": "music",
    "videos": "videos",
    "movies": "videos",
    "screenshots": "screenshots",
    "screenshot": "screenshots",
    "home": "home",
    "user folder": "home",
    "start menu": "start_menu",
}

_cache: dict[str, Path | None] = {}


def _from_shell_api(folder_id: str) -> Path | None:
    if not IS_WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    def make_guid(raw_id: str) -> GUID | None:
        import uuid

        try:
            parsed = uuid.UUID(raw_id)
        except (ValueError, AttributeError):
            return None
        guid = GUID()
        guid.Data1 = parsed.fields[0]
        guid.Data2 = parsed.fields[1]
        guid.Data3 = parsed.fields[2]
        guid.Data4[:] = list(parsed.bytes[8:])
        return guid

    guid = make_guid(folder_id)
    if guid is None:
        return None

    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    ole32.CoTaskMemFree.restype = None
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    # The out parameter is a raw pointer: letting ctypes marshal it as
    # c_wchar_p would swallow the buffer it owns and leak it on every call.
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD,
                                             wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]

    buffer = ctypes.c_void_p()
    hr = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0x1000, None, ctypes.byref(buffer))
    # KF_FLAG_DEFAULT keeps the shell's own fallbacks, e.g. a redirected
    # Screenshots folder that was never created yet.
    if hr != 0 or not buffer:
        return None
    try:
        value = ctypes.wstring_at(buffer).strip()
    finally:
        ole32.CoTaskMemFree(buffer)
    if not value:
        return None
    return Path(os.path.expandvars(value))


def _from_registry(key: str) -> Path | None:
    if not IS_WINDOWS:
        return None
    import winreg

    value_name = _REGULAR_PATHS.get(key)
    if not value_name:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as handle:
            raw, _ = winreg.QueryValueEx(handle, value_name)
    except OSError:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    expanded = os.path.expandvars(raw.strip())
    if "%USERPROFILE%" in expanded:  # REG_SZ entries are not always expanded
        expanded = expanded.replace("%USERPROFILE%", str(Path.home()))
    return Path(expanded)


def known_folder(key: str, *, create: bool = False) -> Path | None:
    """Resolve a shell folder key such as ``"downloads"`` to a real path."""
    resolved_key = FOLDER_ALIASES.get(key.strip().lower(), key.strip().lower())
    if resolved_key == "home":
        return Path.home()
    if resolved_key not in _FOLDER_IDS:
        return None
    if resolved_key in _cache:
        cached = _cache[resolved_key]
        return _prepare(cached, create) if cached else None

    folder_id = _FOLDER_IDS[resolved_key]
    found = _from_shell_api(folder_id) or _from_registry(resolved_key)
    if found is None:
        logger.debug("Known folder %s unresolved by shell API and registry", resolved_key)
    _cache[resolved_key] = found
    return _prepare(found, create)


def _prepare(path: Path | None, create: bool) -> Path | None:
    if path is None:
        return None
    if create:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("Could not create %s", path, exc_info=True)
    return path


def all_known_folders() -> dict[str, str]:
    """Resolved folder map for the diagnostics endpoint (unresolved -> empty)."""
    out: dict[str, str] = {}
    for key in _FOLDER_IDS:
        path = known_folder(key)
        out[key] = str(path) if path else ""
    out["home"] = str(Path.home())
    return out


@dataclass
class FolderRef:
    """A folder plus the label the user used to ask for it."""

    key: str
    label: str
    path: Path | None
    note: str = ""


def resolve_folder(request: str) -> FolderRef | None:
    """Map free text ('my downloads', 'Desktop', 'screenshots') to a folder."""
    text = (request or "").strip().lower().rstrip("/\\")
    if not text:
        return None
    key = FOLDER_ALIASES.get(text)
    if key is None:
        for alias, mapped in FOLDER_ALIASES.items():
            if text in (f"my {alias}", f"the {alias}", alias, f"{alias} folder"):
                key = mapped
                break
    if key is None:
        return None
    label = key.replace("_", " ").title()
    if key in ("screenshots",):
        return FolderRef(key=key, label="Screenshots", path=screenshots_dir(create=False))
    return FolderRef(key=key, label=label, path=known_folder(key))


def screenshots_dir(*, create: bool = False) -> Path:
    """Where screenshots belong: the shell Screenshots folder, else Pictures.

    ``Pictures/Screenshots`` is what Windows itself uses when the dedicated
    known folder has never been registered, so the fallback is not a guess.
    """
    folder = known_folder("screenshots")
    if folder is not None:
        return _prepare(folder, create) or folder
    pictures = known_folder("pictures")
    base = pictures if pictures is not None else Path.home()
    return _prepare(base / "Screenshots", create) or (base / "Screenshots")


def shortcut_roots() -> list[Path]:
    """Directories that legitimately hold app shortcuts, for name lookups.

    Deliberately narrow: Desktop plus the user and all-users Start Menu, so a
    'open the Instagram shortcut' request never turns into a full-disk scan.
    """
    roots: list[Path] = []
    for key in ("desktop", "start_menu", "programs"):
        path = known_folder(key)
        if path and path.exists() and path not in roots:
            roots.append(path)
    program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    all_users = program_data / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if all_users.exists() and all_users not in roots:
        roots.append(all_users)
    return roots


def open_path(path: Path) -> tuple[bool, str]:
    """Ask the shell to open a file or folder. Returns (succeeded, message)."""
    if path is None or not path.exists():
        return False, f"{path} does not exist."
    try:
        if IS_WINDOWS:
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif _mac_open():
            subprocess.run(["open", str(path)], check=False, timeout=10)
        else:
            subprocess.run(["xdg-open", str(path)], check=False, timeout=10)
        return True, f"Opened {path.name}."
    except OSError as exc:
        return False, f"Windows refused to open {path.name}: {exc.strerror or exc}"


def reveal_path(path: Path) -> tuple[bool, str]:
    """Open the containing folder with the item selected (Explorer)."""
    if path is None or not path.exists():
        return False, f"{path} does not exist."
    if not IS_WINDOWS:
        return open_path(path.parent if path.is_file() else path)
    try:
        subprocess.run(["explorer", "/select,", str(path)], check=False, timeout=10)
        return True, f"Showed {path.name} in its folder."
    except OSError as exc:
        return False, f"Could not open the folder: {exc.strerror or exc}"


def _mac_open() -> bool:
    return Path("/usr/bin/open").exists()


def friendly_target(path: Path | None) -> str:
    """A short spoken label for a path, never the raw absolute string.

    'Downloads' reads out well; 'C:\\Users\\aradh\\OneDrive\\Desktop\\x\\y.png'
    does not, and the user only hears it when they did not ask for a location.
    """
    if path is None:
        return "the default location"
    for key in _FOLDER_IDS:
        folder = known_folder(key)
        if folder and _same_or_parent(folder, path):
            label = key.replace("_", " ").title()
            try:
                relative = path.relative_to(folder)
            except ValueError:
                continue
            if str(relative) in ("", "."):
                return label
            return f"{label}/{relative}".replace("\\", "/")
    return path.name


def _same_or_parent(folder: Path, path: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def drive_root(path: Path) -> str:
    """'C' for a path on C:, else '' — used for storage reporting."""
    as_str = str(path)
    if len(as_str) > 1 and as_str[1] == ":" and as_str[0] in string.ascii_letters:
        return as_str[0].upper()
    return ""
