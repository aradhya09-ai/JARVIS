"""Master volume of the default playback device.

Windows exposes the tray slider as `IAudioEndpointVolume` on the default render
endpoint, so this reads and writes the number the user actually sees. The
alternatives were rejected deliberately: the legacy `waveOutSetVolume` moves a
different mixer layer, and simulated volume keys cannot report a level or reach
a specific one.

`pycaw` (with `comtypes`) is the only dependency this adds. It exists because a
hand-rolled COM binding was tried first and failed: reaching this interface
needs the exact interface identifiers and vtable layout Core Audio publishes,
and a wrong one returns E_NOINTERFACE with no useful message. These two packages
are pure Python, local-only, and verified against this machine's Realtek
endpoint, which makes them the smaller risk than my own binding.
"""
from __future__ import annotations


class VolumeUnavailable(RuntimeError):
    """No reachable master volume: library missing, no endpoint, or driver refusal."""


def _endpoint():
    try:
        from pycaw.pycaw import AudioUtilities
    except ImportError as exc:
        raise VolumeUnavailable(
            "the audio library is not installed (pip install pycaw comtypes)"
        ) from exc
    try:
        device = AudioUtilities.GetSpeakers()
        return device.EndpointVolume
    except Exception as exc:
        raise VolumeUnavailable(f"Windows would not expose the speakers ({exc})") from exc


def _percent(volume) -> int:
    scalar = volume.GetMasterVolumeLevelScalar()
    return int(round(max(0.0, min(1.0, float(scalar))) * 100))


def _muted(volume) -> bool:
    return bool(volume.GetMute())


def read() -> tuple[int, bool]:
    volume = _endpoint()
    return _percent(volume), _muted(volume)


def write(percent: int | None = None, muted: bool | None = None) -> tuple[int, bool]:
    """Apply a level and/or mute state, then report what the endpoint actually is."""
    volume = _endpoint()
    try:
        if percent is not None:
            volume.SetMasterVolumeLevelScalar(min(1.0, max(0.0, percent / 100)), None)
        if muted is not None:
            volume.SetMute(1 if muted else 0, None)
    except Exception as exc:
        raise VolumeUnavailable(f"Windows refused the volume change ({exc})") from exc
    return _percent(volume), _muted(volume)
