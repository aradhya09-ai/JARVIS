"""Regressions for the voice/AI fixes.

These cover behaviour that used to fail silently: Hindi text that produced a
valid-but-empty WAV, task results that never reached the speech layer, tool
rules that fired on topic questions, and a model outage reported as an
"unintelligible request".
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import struct
import wave

import pytest

from ai.model_router import ModelUnavailable
from ai.planner import Planner
from database.repository import DEFAULT_SETTINGS
from voice.text_to_speech import _is_silent, has_devanagari, romanize


@pytest.fixture(scope="session")
def services():
    import main
    return main._build_services()


def wav_bytes(amp: int, frames: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(struct.pack("<%dh" % frames, *[amp] * frames))
    return buffer.getvalue()


class DeadRouter:
    """Stands in for a machine with no usable model."""

    def __init__(self, detail: str) -> None:
        self.detail = detail

    async def complete(self, messages, json_mode=False):  # noqa: ANN001, ARG002
        raise ModelUnavailable(self.detail)

    def active_name(self) -> str:
        return "none"


def planner_with(services, detail: str) -> Planner:
    return Planner(services.settings, services.repo, services.registry, DeadRouter(detail))


def test_romanize_turns_devanagari_into_ascii():
    text = "नमस्ते अराध्या, मैं जार्विस हूँ।"
    assert has_devanagari(text)
    out = romanize(text)
    assert not has_devanagari(out)
    assert "namaste" in out.lower()
    # The nasal sign has its own sound, so the consonant before it keeps its vowel.
    assert romanize("कंप्यूटर") == "kanpyootar"


def test_is_silent_catches_the_empty_wav_sapi_returns():
    assert _is_silent(wav_bytes(0))
    assert not _is_silent(wav_bytes(9000))


def test_hindi_text_romanizes_when_no_hindi_voice_installed(services):
    if not shutil.which("powershell"):
        pytest.skip("Windows PowerShell is required for SAPI synthesis")
    audio, info = asyncio.run(services.tts.synthesize("नमस्ते अराध्या, मैं जार्विस हूँ।"))
    assert info["devanagari"] is True
    assert not _is_silent(audio), "synthesis returned audio the user cannot hear"
    if info["romanized"]:
        assert info.get("note"), "a transliterated reading has to be explained"


@pytest.mark.parametrize("text", [
    "what is my cpu usage",
    "check battery",
    "how much ram is free",
    "network status",
])
def test_machine_questions_use_the_stats_tool(services, text):
    plan = asyncio.run(planner_with(services, "offline").plan(text))
    assert plan.tool == "system.stats"


@pytest.mark.parametrize("text", [
    "Explain in one line how a neural network learns.",
    "what is a neural network",
    "how does memory management work in python",
])
def test_topic_questions_do_not_become_a_stats_call(services, text):
    plan = asyncio.run(planner_with(services, "offline").plan(text))
    assert plan.tool != "system.stats"


@pytest.mark.parametrize("text", [
    "what is the battery level",
    "how much battery is left",
    "battery status",
    "check my battery",
])
def test_asking_about_the_battery_is_a_reading_not_a_lesson(services, text):
    """'what is the battery level' used to cost a 32-second model call that then
    refused to answer, because the definition guard read 'what is the' as a
    concept question."""
    plan = asyncio.run(planner_with(services, "offline").plan(text))
    assert plan.tool == "system.stats"
    assert "battery" in plan.args["sections"]


class FixedJsonRouter:
    """A model that always answers with one JSON blob, however the prompt reads."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def complete(self, messages, json_mode=False):  # noqa: ANN001, ARG002
        return json.dumps(self.payload)

    def active_name(self) -> str:
        return "fake"


def test_memory_tools_are_never_offered_to_the_model(services):
    head = Planner(
        services.settings, services.repo, services.registry, FixedJsonRouter({})
    )._stable_head()
    for hidden in ("knowledge.remember", "memory.set", "memory.forget"):
        assert hidden not in head
    assert "app.open" in head


def test_a_memory_pick_on_a_plain_question_stores_nothing(services):
    router = FixedJsonRouter({
        "action": "tool", "tool": "knowledge.remember",
        "args": {"key": "rivers", "value": "Ganga"}, "why": "answering a question",
    })
    planner = Planner(services.settings, services.repo, services.registry, router)
    before = len(services.repo.list_knowledge())
    plan = asyncio.run(planner.plan("which river is longest in india"))
    assert plan.tool != "knowledge.remember"
    assert "did not save" in (plan.reply or "").lower()
    assert len(services.repo.list_knowledge()) == before


def test_an_explicit_remember_still_reaches_the_tool(services):
    """Hiding the tool from the catalog must not break the spoken save command."""
    plan = asyncio.run(planner_with(services, "offline").plan("remember my wifi is LabNet"))
    assert plan.tool == "memory.set"
    assert plan.args["value"] == "LabNet"


def test_model_outage_says_why_instead_of_calling_the_request_unclear(services):
    plan = asyncio.run(planner_with(
        services, "Ollama is not reachable at 127.0.0.1:11434. Start it with 'ollama serve'."
    ).plan("what is a neural network"))
    assert "no language model is available to interpret it" not in (plan.reply or "").lower()
    assert "ollama" in (plan.reply or "").lower()
    assert plan.planner == "model-error"


def test_unavailable_error_raises_an_exception_not_a_coroutine(services):
    async def run() -> ModelUnavailable:
        try:
            raise await services.router._unavailable_error()
        except ModelUnavailable as exc:
            return exc

    error = asyncio.run(run())
    assert isinstance(error, ModelUnavailable)


def test_message_events_carry_task_id(services):
    queue = services.bus.subscribe()
    try:
        services.executor._publish_message({
            "role": "assistant", "content": "CPU usage is 22%.",
            "id": 7, "at": "2026-01-01T00:00:00Z", "task_id": "abc123",
        })
        event = queue.get_nowait()
    finally:
        services.bus.unsubscribe(queue)
    assert event["type"] == "message.created"
    assert event["payload"]["task_id"] == "abc123"
    assert event["payload"]["id"] == 7


def test_windows_voice_choice_is_an_editable_setting():
    assert "voice.sapi_voice" in DEFAULT_SETTINGS
