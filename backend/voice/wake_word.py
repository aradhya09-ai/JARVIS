"""Wake-word capability reporting.

Custom "Hey JARVIS" models are not bundled. The UI implements wake-word
matching with the browser recogniser; openWakeWord is optional for offline use.
"""
from __future__ import annotations


def availability() -> dict[str, object]:
    try:
        import openwakeword  # noqa: F401
    except ImportError:
        return {
            "available": False,
            "engine": "openWakeWord",
            "detail": (
                "openWakeWord is not installed. Wake word detection runs in the browser "
                "with the Web Speech API and matches the configured phrase, which may "
                "require an internet-backed recogniser and is not always-on."
            ),
            "browser_fallback": True,
        }
    return {
        "available": True,
        "engine": "openWakeWord",
        "detail": (
            "openWakeWord is installed but JARVIS does not bundle a custom 'Hey JARVIS' "
            "model. Use the configurable wake phrase; a custom model must be supplied."
        ),
        "browser_fallback": True,
    }
