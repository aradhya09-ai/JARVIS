"""Regressions for the tone-shaping pass that sits between SAPI and the speakers.

The whole point of this module is a claim about sound, so these tests measure
sound: they synthesise synthetic clips in memory, shape them, and read the
result back. Ratios come from measurements on this machine's engine output
(22050 Hz, peak 0.97 full scale) with generous margins, so the suite says
"the rumble really was removed" rather than just "no exception was thrown".

Nothing here needs PowerShell, a voice, or an audio device.
"""
from __future__ import annotations

import math
import random
import struct
import wave
from io import BytesIO

import pytest

from api.schemas import TTSRequest
from voice import audio_shape
from voice.audio_shape import CEILING, _goertzel, shape

RATE = 22050


def _pack(samples: list[int], channels: int = 1, rate: int = RATE) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buffer.getvalue()


def tone(freq: float, amp: float = 12000.0, seconds: float = 0.4) -> bytes:
    frames = int(RATE * seconds)
    return _pack([int(amp * math.sin(2 * math.pi * freq * i / RATE)) for i in range(frames)])


def noise(seconds: float = 0.4, channels: int = 1, seed: int = 7) -> bytes:
    rng = random.Random(seed)
    samples: list[int] = []
    for _ in range(int(RATE * seconds)):
        value = rng.randint(-20000, 20000)
        samples.extend([value] * channels)
    return _pack(samples, channels)


def read(wav: bytes) -> tuple[tuple[int, int, int, int], list[float]]:
    """(channels, width, rate, frames) plus the samples normalised to -1..1."""
    reader = wave.open(BytesIO(wav), "rb")
    try:
        meta = (reader.getnchannels(), reader.getsampwidth(),
                reader.getframerate(), reader.getnframes())
        raw = reader.readframes(meta[3])
    finally:
        reader.close()
    ints = struct.unpack(f"<{len(raw) // 2}h", raw)
    return meta, [value / 32768.0 for value in ints]


def energy(samples: list[float], freq: float) -> float:
    return _goertzel(samples, RATE, freq) / max(len(samples), 1)


def ratio(freq: float, style: str) -> float:
    """How much of a pure tone survives the pass, relative to the engine clip."""
    _, before = read(tone(freq))
    _, after = read(shape(tone(freq), style))
    return energy(after, freq) / max(energy(before, freq), 1e-12)


# ---------------------------------------------------------------- the clarity claim

def test_rumble_below_the_speaker_range_is_cut_hard():
    assert ratio(30.0, "clear") < 0.25
    assert ratio(55.0, "clear") < 0.60


def test_the_cut_does_not_reach_into_the_voice_itself():
    # Removing mud is only a win if 85 Hz and up stay standing; a filter that
    # ate the body would sound "clearer" and be worse.
    assert ratio(85.0, "clear") > 0.60
    assert ratio(170.0, "clear") > 0.85


def test_presence_band_is_lifted():
    assert ratio(2800.0, "clear") > 1.15
    assert ratio(2800.0, "jarvis") > ratio(2800.0, "clear")


def test_jarvis_adds_warmth_that_clear_does_not():
    assert energy(read(shape(tone(170.0), "jarvis"))[1], 170.0) \
        > energy(read(shape(tone(170.0), "clear"))[1], 170.0) * 1.15


def test_mud_relative_to_definition_doubles_on_noise():
    _, before = read(noise())
    _, after = read(shape(noise(), "jarvis"))
    before_mud, before_air = energy(before, 60.0), energy(before, 2800.0)
    after_mud, after_air = energy(after, 60.0), energy(after, 2800.0)
    assert after_mud < before_mud * 0.7
    assert after_air / after_mud > (before_air / before_mud) * 1.6


# ---------------------------------------------------------------- levels

def test_a_hot_clip_is_brought_under_the_ceiling():
    for style in ("clear", "jarvis"):
        peak = max(abs(value) for value in read(shape(tone(1000.0, amp=32000.0), style))[1])
        assert peak == pytest.approx(CEILING, abs=1e-3)


def test_the_engine_clip_is_left_alone_by_off():
    raw = tone(1000.0, amp=32000.0)
    assert shape(raw, "off") == raw
    assert max(abs(v) for v in read(raw)[1]) > 0.9  # the state normalisation protects against


def test_near_silence_is_not_normalised_up_to_the_ceiling():
    _, out = read(shape(tone(1000.0, amp=180.0), "jarvis"))
    assert max(abs(value) for value in out) < 0.05


# ---------------------------------------------------------------- the contract

def test_sample_rate_channels_and_length_are_preserved():
    for source, channels in ((tone(440.0), 1), (noise(channels=2), 2)):
        before = read(source)[0]
        after = read(shape(source, "jarvis"))[0]
        assert after == before


def test_stereo_input_is_shaped_not_truncated():
    raw = noise(channels=2)
    _, before = read(raw)
    _, after = read(shape(raw, "jarvis"))
    assert len(after) == len(before)
    assert max(abs(v) for v in after) <= CEILING + 1e-3


def test_an_unknown_style_falls_back_to_the_plain_one():
    raw = noise()
    assert shape(raw, "baritone") == shape(raw, "clear")


def test_audio_this_module_cannot_read_is_handed_back():
    assert shape(b"not a wav at all", "jarvis") == b"not a wav at all"
    assert shape(b"", "jarvis") == b""
    assert shape(noise()[:40], "jarvis") == noise()[:40]


def test_an_unsupported_sample_width_is_left_untouched():
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(1)
        handle.setframerate(8000)
        handle.writeframes(bytes([128] * 400))
    assert shape(buffer.getvalue(), "jarvis") == buffer.getvalue()


def test_silence_from_the_engine_stays_silence():
    raw = tone(1000.0, amp=0.0)
    assert shape(raw, "jarvis") == raw


def test_styles_are_offered_to_the_ui_as_they_are_named_in_settings():
    assert set(audio_shape.STYLES) == {"jarvis", "clear", "off"}


# ---------------------------------------------------------------- the route

class _StubRepo:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get_setting(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _StubServices:
    def __init__(self, values: dict[str, object]) -> None:
        self.repo = _StubRepo(values)


def test_the_stored_style_reaches_synthesis():
    from api.voice_routes import _synthesize_kwargs
    from database.repository import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["voice.style"] == "jarvis"
    assert _synthesize_kwargs(_StubServices({}), TTSRequest(text="hi"))["style"] == "jarvis"
    assert _synthesize_kwargs(
        _StubServices({"voice.style": "clear"}), TTSRequest(text="hi"))["style"] == "clear"
    # An empty stored value must not disable shaping by accident.
    assert _synthesize_kwargs(
        _StubServices({"voice.style": ""}), TTSRequest(text="hi"))["style"] == "jarvis"
