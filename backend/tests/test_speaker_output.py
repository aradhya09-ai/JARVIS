"""Regressions for speaker output, the route a reply takes when the browser tab
is not allowed to make a sound.

Chrome blocks audio in a window that has never been clicked, which is every
window a wake-word assistant speaks into first. These cover the clip length the
UI times itself against, the refusal to play silence, the purge an interrupt has
to actually perform, and the temporary clip files being cleaned up.
"""
from __future__ import annotations

import asyncio
import io
import struct
import wave
from pathlib import Path

import pytest
from fastapi import HTTPException

from api import voice_routes
from api.schemas import TTSRequest
from voice import audio_output


def wav_bytes(amp: int, frames: int = 8000, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<%dh" % frames, *[amp] * frames))
    return buffer.getvalue()


class FakeWinsound:
    SND_FILENAME = 0x20000
    SND_MEMORY = 0x1
    SND_ASYNC = 0x10
    SND_NODEFAULT = 0x2
    SND_PURGE = 0x40

    def __init__(self) -> None:
        self.calls: list[tuple[object, int]] = []

    def PlaySound(self, sound, flags):  # noqa: N802 - mirrors the Windows API
        self.calls.append((sound, flags))


@pytest.fixture
def fake_winsound(monkeypatch):
    monkeypatch.setattr(audio_output, "_playing", None)
    monkeypatch.setattr(audio_output, "_stale", [])
    fake = FakeWinsound()
    monkeypatch.setattr(audio_output, "winsound", fake)
    return fake


def _purges(fake: FakeWinsound) -> list:
    return [call for call in fake.calls if call[1] == fake.SND_PURGE]


def test_duration_comes_from_the_wav_header():
    assert audio_output.duration_ms(wav_bytes(9000, frames=8000, rate=16000)) == 500
    assert audio_output.duration_ms(wav_bytes(9000, frames=16000, rate=8000)) == 2000


def test_play_starts_asynchronously_from_a_temporary_clip(fake_winsound):
    length = audio_output.play(wav_bytes(9000))
    assert length == 500
    sound, flags = fake_winsound.calls[-1]
    assert flags & fake_winsound.SND_FILENAME, "Windows cannot play async from memory"
    assert flags & fake_winsound.SND_ASYNC
    assert Path(sound).exists()


def test_the_previous_clip_is_deleted_when_the_next_one_starts(fake_winsound):
    audio_output.play(wav_bytes(9000))
    path = Path(fake_winsound.calls[-1][0])
    audio_output.play(wav_bytes(8000, frames=16000))
    assert not path.exists(), "one clip per reply, then it is gone"


def test_stop_purges_playback(fake_winsound):
    audio_output.play(wav_bytes(9000))
    path = Path(fake_winsound.calls[-1][0])
    audio_output.stop()
    assert fake_winsound.calls[-1] == (None, fake_winsound.SND_PURGE)
    assert not path.exists()


def test_every_play_purges_before_it_starts(fake_winsound):
    audio_output.play(wav_bytes(9000))
    audio_output.play(wav_bytes(9000))
    # purge, clip, purge, clip
    assert len(_purges(fake_winsound)) == 2


def test_clips_left_by_a_killed_backend_are_cleared_on_the_next_start(
    fake_winsound, tmp_path, monkeypatch
):
    monkeypatch.setattr(audio_output, "_temp_dir", tmp_path)
    monkeypatch.setattr(audio_output, "_swept", False)
    orphan = tmp_path / "reply-orphan.wav"
    orphan.write_bytes(wav_bytes(9000))
    audio_output.play(wav_bytes(9000))
    assert not orphan.exists()


def test_play_refuses_on_platforms_without_winsound(monkeypatch):
    monkeypatch.setattr(audio_output, "winsound", None)
    assert not audio_output.available()
    with pytest.raises(RuntimeError):
        audio_output.play(wav_bytes(9000))


def test_availability_explains_the_temporary_clip(monkeypatch):
    monkeypatch.setattr(audio_output, "winsound", FakeWinsound())
    detail = str(audio_output.availability()["detail"])
    assert "temporary" in detail and "microphone audio is never saved" in detail


class StubTTS:
    def __init__(self, audio: bytes) -> None:
        self.audio = audio

    async def synthesize(self, text, **_kwargs):  # noqa: ANN001
        return self.audio, {"voice": "David", "devanagari": False, "romanized": False}


class StubServices:
    """Only the pieces the speak route touches."""

    def __init__(self, audio: bytes) -> None:
        self.tts = StubTTS(audio)
        self.repo = type("Repo", (), {"get_setting": staticmethod(lambda _k, d=None: d)})()
        self.audit = type("Audit", (), {"record": lambda *a, **k: None})()


def test_speak_route_reports_the_length_it_played(fake_winsound):
    result = asyncio.run(
        voice_routes.tts_speak(TTSRequest(text="Good afternoon."), StubServices(wav_bytes(9000)))
    )
    assert result["duration_ms"] == 500
    assert result["engine"] == "winsound"
    assert fake_winsound.calls, "the reply has to reach the speakers"


def test_speak_route_refuses_silence_instead_of_playing_it(fake_winsound):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(voice_routes.tts_speak(TTSRequest(text="hello"), StubServices(wav_bytes(0))))
    assert caught.value.status_code == 503
    assert fake_winsound.calls == [], "silent audio must never start a playback"


def test_speak_route_says_so_when_the_platform_has_no_speakers(monkeypatch):
    monkeypatch.setattr(audio_output, "winsound", None)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            voice_routes.tts_speak(TTSRequest(text="hello"), StubServices(wav_bytes(9000)))
        )
    assert caught.value.status_code == 503


def test_stop_route_purges_playback(fake_winsound):
    asyncio.run(voice_routes.tts_speak_stop(StubServices(wav_bytes(9000))))
    assert fake_winsound.calls[-1] == (None, fake_winsound.SND_PURGE)
