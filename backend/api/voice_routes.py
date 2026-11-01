from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.deps import get_services
from api.schemas import TTSRequest
from services import Services
from voice import audio_output, wake_word
from voice.text_to_speech import _is_silent

router = APIRouter(prefix="/api/voice", tags=["voice"])

SUFFIXES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
}

MAX_AUDIO_BYTES = 25 * 1024 * 1024


@router.get("/status")
async def status(services: Services = Depends(get_services)) -> dict:
    return {
        "backend_stt": services.stt.availability(),
        "backend_tts": services.tts.availability(),
        "speaker_output": audio_output.availability(),
        "wake_word": wake_word.availability(),
        "browser_note": (
            "The desktop UI uses the browser's SpeechRecognition and speechSynthesis APIs. "
            "Browser recognition may use an online service, so mic use is always visible "
            "and can be disabled instantly."
        ),
    }


@router.post("/transcribe")
async def transcribe(request: Request, language: str | None = None,
                     services: Services = Depends(get_services)) -> dict:
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty audio body.")
    if len(body) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio exceeds 25 MB.")
    suffix = SUFFIXES.get((request.headers.get("content-type") or "").split(";")[0], ".webm")
    try:
        return await services.stt.transcribe(body, suffix=suffix, language=language)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/voices")
async def voices(services: Services = Depends(get_services)) -> dict:
    """Which SAPI voices actually exist on this machine.

    Requirement: never claim Hindi audio works without checking. The UI uses
    this to explain the limitation instead of silently producing silence.
    """
    installed = await services.tts.list_voices()
    hindi = [v for v in installed if str(v.get("culture", "")).lower().startswith("hi")]
    return {
        "engine": "Windows SAPI (System.Speech)",
        "voices": installed,
        "hindi_available": bool(hindi),
        "hindi_voices": [v["name"] for v in hindi],
        "note": (
            f"{len(installed)} Windows voice(s) installed: "
            + (", ".join(f"{v['name']} ({v['culture']})" for v in installed) or "none")
            + ". "
            + (
                f"Hindi voices found: {', '.join(v['name'] for v in hindi)}."
                if hindi else
                "No Hindi voice is installed, so Devanagari text is transliterated into "
                "Latin phonetics and read by an English voice. Install Hindi under "
                "Settings > Time & language > Language & region > Add a language, then "
                "enable its Speech option, for true Hindi speech."
            )
        ),
    }


def _synthesize_kwargs(services: Services, payload: TTSRequest) -> dict:
    """An explicit field in the request wins; otherwise use the stored setting."""
    return {
        "language": payload.language or str(
            services.repo.get_setting("voice.language", "") or "") or None,
        "voice_name": payload.voice if payload.voice is not None else str(
            services.repo.get_setting("voice.sapi_voice", "") or "") or None,
        "rate": payload.rate if payload.rate is not None else int(
            services.repo.get_setting("voice.rate", 0) or 0),
        "pitch": payload.pitch if payload.pitch is not None else int(
            services.repo.get_setting("voice.pitch", 0) or 0),
    }


@router.post("/tts")
async def tts(payload: TTSRequest, services: Services = Depends(get_services)) -> Response:
    try:
        audio, info = await services.tts.synthesize(payload.text, **_synthesize_kwargs(services, payload))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    services.audit.record("user", "voice.tts", "low", {"chars": len(payload.text)}, "ok")
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={
            "Content-Disposition": "inline; filename=jarvis.wav",
            "Cache-Control": "no-store",
            # The UI reports which voice actually spoke and whether Hindi had to
            # be transliterated. Guessing from the text alone would keep claiming
            # "transliterated" after a real Hindi voice is installed.
            "X-Jarvis-TTS-Info": json.dumps(info, ensure_ascii=True),
        },
    )


@router.post("/tts/speak")
async def tts_speak(payload: TTSRequest, services: Services = Depends(get_services)) -> dict:
    """Synthesize a reply and play it on the Windows default output device.

    A browser tab cannot make a sound before it has been clicked, which breaks a
    wake-word assistant that has to answer first. The length is returned so the
    UI ends the speaking state when the audio really finishes, and a clip with no
    frames - what a SAPI voice returns for a script it cannot speak - is reported
    as a failure instead of playing silence.
    """
    if not audio_output.available():
        raise HTTPException(
            status_code=503, detail=str(audio_output.availability()["detail"]))
    try:
        audio, info = await services.tts.synthesize(
            payload.text, **_synthesize_kwargs(services, payload))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        length = audio_output.duration_ms(audio)
    except Exception as exc:  # unreadable header: refuse rather than guess
        raise HTTPException(
            status_code=503, detail=f"Speech audio was unreadable: {exc}") from exc
    if length <= 0 or _is_silent(audio):
        raise HTTPException(
            status_code=503,
            detail="The installed Windows voice returned no audio for this text.",
        )
    try:
        await audio_output.play_async(audio)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail=f"Speaker output failed: {exc}") from exc
    services.audit.record(
        "user", "voice.tts_speak", "low",
        {"chars": len(payload.text), "duration_ms": length}, "ok")
    return {"duration_ms": length, "engine": "winsound", **info}


@router.post("/tts/speak/stop")
async def tts_speak_stop(services: Services = Depends(get_services)) -> dict:
    """Silence the speakers now - an interrupt has to actually stop the audio."""
    await audio_output.stop_async()
    services.audit.record("user", "voice.tts_stop", "low", {}, "ok")
    return {"stopped": True}
