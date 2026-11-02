"""Runtime configuration for the JARVIS backend.

Values are read from the repository-root ``.env`` file (if present) and the
process environment. Secrets never appear in source control.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

import paths

BACKEND_DIR = Path(__file__).resolve().parent
REPO_DIR = BACKEND_DIR.parent


def _load_dotenv() -> None:
    path = REPO_DIR / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return Path(os.path.expandvars(raw)).expanduser()


HOME = Path.home()


def _split_paths(raw: str) -> list[Path]:
    if not raw.strip():
        return []
    return [Path(os.path.expandvars(p.strip())).expanduser() for p in raw.split(";") if p.strip()]


def _split_origins(raw: str) -> list[str]:
    """Browser origins allowed to call the API, on top of the built-in loopback ones.

    A bare "*" is dropped rather than honoured: these tools control a real desktop,
    so an open CORS policy would let any website the user visits drive them.
    """
    origins = []
    for part in raw.replace(";", ",").split(","):
        value = part.strip().rstrip("/")
        if value and value != "*":
            origins.append(value)
    return origins


def _split_clients(raw: str) -> list[str]:
    """Addresses allowed to reach the API from outside the machine.

    Empty by default, which keeps the service local-only. Entries are IPs or
    CIDRs, so a Tailscale address can be named exactly instead of opening the
    whole carrier range.
    """
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


@dataclass
class Settings:
    """Process-wide settings. User-editable preferences live in SQLite."""

    host: str = os.environ.get("JARVIS_HOST", "127.0.0.1")
    port: int = int(os.environ.get("JARVIS_PORT", "8765"))
    version: str = "0.1.0"

    data_dir: Path = _env_path("JARVIS_DATA_DIR", BACKEND_DIR / "data")
    output_dir: Path = _env_path("JARVIS_OUTPUT_DIR", HOME / "JARVIS")
    project_root: Path = _env_path("JARVIS_PROJECT_ROOT", HOME / "JARVIS" / "projects")
    extra_roots: list[Path] = field(
        default_factory=lambda: _split_paths(os.environ.get("JARVIS_EXTRA_ROOTS", ""))
    )
    # Set this to reach the dashboard from a tunnel host, e.g. a Tailscale Serve
    # https://<machine>.<tailnet>.ts.net address, without editing source.
    extra_origins: list[str] = field(
        default_factory=lambda: _split_origins(os.environ.get("JARVIS_EXTRA_ORIGINS", ""))
    )
    # Devices allowed to drive JARVIS from off the machine, as IPs or CIDRs. Empty
    # keeps the REST API local-only, which is the safe default for a service that
    # can mute a laptop or launch programs.
    remote_clients: list[str] = field(
        default_factory=lambda: _split_clients(os.environ.get("JARVIS_REMOTE_CLIENTS", ""))
    )

    ollama_url: str = os.environ.get("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    ollama_model: str = os.environ.get("JARVIS_OLLAMA_MODEL", "qwen2.5:3b")
    # A cold qwen2.5:3b on a laptop can exceed 60s just to page the model in, and
    # that looked like "no language model available" in the dashboard.
    ollama_timeout: float = float(os.environ.get("JARVIS_OLLAMA_TIMEOUT", "150"))
    ollama_keep_alive: str = os.environ.get("JARVIS_OLLAMA_KEEP_ALIVE", "15m")
    # Re-page the model while the user is still in conversation, then let it go.
    model_keep_warm_seconds: float = float(
        os.environ.get("JARVIS_MODEL_KEEP_WARM_SECONDS", "600"))

    cloud_base_url: str = os.environ.get("JARVIS_CLOUD_BASE_URL", "").rstrip("/")
    cloud_api_key: str = os.environ.get("JARVIS_CLOUD_API_KEY", "")
    cloud_model: str = os.environ.get("JARVIS_CLOUD_MODEL", "")

    image_provider: str = os.environ.get("JARVIS_IMAGE_PROVIDER", "none").lower()
    image_base_url: str = os.environ.get("JARVIS_IMAGE_BASE_URL", "http://127.0.0.1:7860").rstrip("/")
    image_api_key: str = os.environ.get("JARVIS_IMAGE_API_KEY", "")
    image_model: str = os.environ.get("JARVIS_IMAGE_MODEL", "")

    stt_model: str = os.environ.get("JARVIS_STT_MODEL", "base.en")
    stt_device: str = os.environ.get("JARVIS_STT_DEVICE", "cpu")

    auto_confirm_moderate: bool = _env_bool("JARVIS_AUTO_CONFIRM_MODERATE", False)
    task_history: bool = _env_bool("JARVIS_TASK_HISTORY", True)

    task_timeout_seconds: float = 120.0
    max_tasks_kept: int = 500
    max_audit_entries: int = 2000
    llm_max_tokens: int = 900
    # The planner's routing call only needs one small JSON object back.
    llm_route_max_tokens: int = int(os.environ.get("JARVIS_LLM_ROUTE_MAX_TOKENS", "220"))
    llm_temperature: float = 0.2
    phone_request_timeout: float = 12.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jarvis.db"

    @property
    def audit_log_path(self) -> Path:
        return self.data_dir / "audit.log"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.output_dir, self.project_root,
                     self.output_dir / "presentations", self.output_dir / "images"):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def read_roots(self) -> list[Path]:
        roots = [HOME, self.output_dir, self.project_root, *self.extra_roots]
        return _unique(roots)

    @property
    def write_roots(self) -> list[Path]:
        # The shell, not Path.home(), decides where Desktop/Documents live: this
        # account redirects them into OneDrive and ~/Desktop is not real.
        roots = [self.output_dir, self.project_root, *self.extra_roots]
        for key in ("downloads", "documents", "desktop", "screenshots"):
            folder = paths.known_folder(key)
            if folder:
                roots.append(folder)
        return _unique(roots)

    def lan_ip(self) -> str:
        """Best-effort LAN address (UDP connect sends no packets)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()


def _unique(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


settings = Settings()
