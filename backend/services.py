"""Shared service container stored on app.state."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai.model_router import ModelRouter
from ai.planner import Planner
from config import Settings
from database.repository import Repository
from events import EventBus
from executor import TaskExecutor
from phone.device_bridge import PhoneBridge
from security.audit_log import AuditLog
from security.authentication import PairingCodes, RateLimiter, SecurityService
from security.permissions import PermissionPolicy
from tools.base import ToolRegistry
from voice.speech_to_text import SpeechToText
from voice.text_to_speech import TextToSpeech


@dataclass
class Services:
    settings: Settings
    repo: Repository
    bus: EventBus
    registry: ToolRegistry
    policy: PermissionPolicy
    audit: AuditLog
    security: SecurityService
    pairing: PairingCodes
    rate_limiter: RateLimiter
    router: ModelRouter
    planner: Planner
    executor: TaskExecutor
    bridge: PhoneBridge
    stt: SpeechToText
    tts: TextToSpeech
    reminders: Any = None
    started_at: float = 0.0
    serve_dir: Any = None
