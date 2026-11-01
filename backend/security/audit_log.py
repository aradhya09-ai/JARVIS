"""Append-only audit trail (SQLite + bounded log file)."""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from database.repository import Repository


class AuditLog:
    def __init__(self, repo: Repository, log_path: Path):
        self.repo = repo
        self.logger = logging.getLogger("jarvis.audit")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if not self.logger.handlers:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            self.logger.addHandler(handler)

    def record(
        self,
        actor: str,
        action: str,
        risk: str,
        detail: dict[str, Any] | None = None,
        outcome: str = "ok",
    ) -> None:
        payload = detail or {}
        self.repo.audit(actor, action, risk, payload, outcome)
        self.logger.info(
            json.dumps(
                {"actor": actor, "action": action, "risk": risk, "outcome": outcome,
                 "detail": payload},
                ensure_ascii=False,
                default=str,
            )
        )

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.repo.list_audit(limit)
