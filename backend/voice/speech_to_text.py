"""Backend speech-to-text via faster-whisper when installed.

The desktop UI uses the browser's SpeechRecognition by default. This module is
for offline transcription of recorded audio; it reports availability honestly.
"""
from __future__ import annotations

import asyncio
import tempfile
import threading
from pathlib import Path

from config import Settings


class SpeechToText:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = threading.Lock()
        self._error: str | None = None

    def availability(self) -> dict[str, object]:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return {
                "available": False,
                "engine": "faster-whisper",
                "detail": (
                    "faster-whisper is not installed. The UI will use the browser's speech "
                    "recognition instead. To enable offline transcription run: "
                    "pip install faster-whisper"
                ),
            }
        if self._error:
            return {"available": False, "engine": "faster-whisper", "detail": self._error,
                    "model": self.settings.stt_model}
        return {
            "available": True,
            "engine": "faster-whisper",
            "model": self.settings.stt_model,
            "device": self.settings.stt_device,
            "detail": "First transcription downloads the model once, then runs on CPU.",
        }

    def _load(self):
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    self.settings.stt_model,
                    device=self.settings.stt_device,
                    compute_type="int8",
                )
            return self._model

    async def transcribe(self, audio: bytes, suffix: str = ".webm", language: str | None = None) -> dict:
        status = self.availability()
        if not status["available"]:
            raise RuntimeError(str(status["detail"]))
        if len(audio) < 200:
            raise ValueError("Audio recording is too short.")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(audio)
            path = Path(handle.name)
        try:
            model = await asyncio.to_thread(self._load)
            segments, info = await asyncio.to_thread(
                model.transcribe, str(path), language=language or None, vad_filter=True
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return {
                "text": text,
                "language": getattr(info, "language", language),
                "duration": getattr(info, "duration", None),
            }
        finally:
            path.unlink(missing_ok=True)
