"""Local text-to-speech through the Windows SAPI engine (no extra packages).

User text is passed through a temporary UTF-8 file and read with
[IO.File]::ReadAllText, never interpolated into the PowerShell command.

Windows ships only Latin-script voices on most installs (typically David and
Zira, both en-US).  A SAPI voice asked to speak Devanagari returns a valid but
zero-frame, completely silent WAV *without* raising an error, so callers cannot
tell silence from success.  This module therefore transliteres Devanagari into
Latin phonetics when no Hindi voice is installed and reports which route was
used, so the UI can describe the audio honestly.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import struct
import tempfile
import wave
from io import BytesIO
from pathlib import Path

from voice.audio_shape import STYLES, shape

# $args[0] = wav out, $args[1] = text file, $args[2] = preferred voice name
# ("" for the engine default), $args[3] = listing file (voice enumeration mode),
# $args[4] = rate (-10..10), $args[5] = pitch (-10..10).  Listing mode is how
# /api/voice/voices answers "is a Hindi voice actually installed on this machine?".
SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($args.Length -ge 4 -and $args[3]) {
    $synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object {
        "{0}|{1}" -f $_.VoiceInfo.Name, $_.VoiceInfo.Culture.Name
    } | Set-Content -Path $args[3] -Encoding UTF8
    exit 0
}
$wanted = if ($args.Length -ge 3) { $args[2] } else { "" }
$picked = $null
if ($wanted) {
    $picked = $synth.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Name -eq $wanted
    } | Select-Object -First 1
}
if (-not $picked) {
    $picked = $synth.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Culture.Name -like "en-*"
    } | Select-Object -First 1
}
if ($picked) { $synth.SelectVoice($picked.VoiceInfo.Name) }
if ($args.Length -ge 5 -and $args[4]) {
    $rate = [int]$args[4]
    if ($rate -ge -10 -and $rate -le 10) { $synth.Rate = $rate }
}
if ($args.Length -ge 6 -and $args[5]) {
    $pitch = [int]$args[5]
    if ($pitch -ge -10 -and $pitch -le 10) { $synth.Pitch = $pitch }
}
$synth.SetOutputToWaveFile($args[0])
$text = [System.IO.File]::ReadAllText($args[1], [System.Text.Encoding]::UTF8)
$synth.Speak($text)
$synth.Dispose()
"""

CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l", "ऱ": "r",
}

# Vowel signs replace the inherent "a" of the preceding consonant.
MATRAS = {
    "ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॅ": "e", "ॉ": "o",
}

SIGNS = {"ं": "n", "ँ": "n", "ः": "h", "ऽ": "", "्": "", "ा": "aa"}

VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o",
}

DIGITS = {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4",
          "५": "5", "६": "6", "७": "7", "८": "8", "९": "9"}

DEVANAGARI = set(CONSONANTS) | set(MATRAS) | set(VOWELS) | set(DIGITS) | {"ं", "ँ", "ः", "्", "ऽ"}


def has_devanagari(text: str) -> bool:
    return any(ch in DEVANAGARI for ch in text)


def romanize(text: str) -> str:
    """Approximate Devanagari -> Latin transliteration for a Latin-only voice.

    Only the word-final schwa is dropped; everything else keeps its inherent "a"
    so an English voice lands on recognisable syllables. Not a scholarly
    transliteration.
    """
    chars = list(text)
    out: list[str] = []
    i = 0
    while i < len(chars):
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if ch in DIGITS:
            out.append(DIGITS[ch])
            i += 1
            continue
        if ch in VOWELS:
            out.append(VOWELS[ch])
            i += 1
            continue
        if ch in MATRAS:
            out.append(MATRAS[ch])
            i += 1
            continue
        if ch in SIGNS:
            out.append(SIGNS[ch])
            i += 1
            continue
        if ch in CONSONANTS:
            base = CONSONANTS[ch]
            if nxt == "्":
                out.append(base)
                i += 2
                continue
            if nxt in MATRAS or nxt == "ऽ":
                out.append(base)
                i += 1
                continue
            if nxt in SIGNS:
                # "कंप" is "kam-p", not "k-p": the nasal sounds on its own and the
                # consonant in front of it keeps its inherent vowel.
                out.append(base + "a")
                i += 1
                continue
            word_final = nxt == "" or nxt.isspace() or nxt in "।,.!?;:"
            if word_final:
                out.append(base)
            else:
                # The inherent schwa is kept before a conjunct: an English voice
                # reading "namste" loses the word, while "namaste" is recognisable.
                out.append(base + "a")
            i += 1
            continue
        if ch == "।":
            out.append(".")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out).strip()


def _bounded(value: object) -> int:
    """Clamp a spoken-rate or pitch setting into the range SAPI accepts."""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return max(-10, min(10, number))


def _is_silent(wav: bytes) -> bool:
    """True when the engine produced a header with no audible samples."""
    if len(wav) <= 44:
        return True
    try:
        reader = wave.open(BytesIO(wav), "rb")
    except Exception:  # noqa: BLE001 - unparseable audio is treated as a failure
        return False
    try:
        width = reader.getsampwidth()
        frames = reader.readframes(reader.getnframes())
    finally:
        reader.close()
    if not frames:
        return True
    width = reader.getsampwidth()
    if width == 0:
        return True
    usable = len(frames) - (len(frames) % width)
    if usable == 0:
        return True
    fmt = {1: "b", 2: "h", 4: "i"}.get(width)
    if fmt is None:
        return False
    samples = struct.unpack(f"<{usable // width}{fmt}", frames[:usable])
    return max((abs(s) for s in samples), default=0) == 0


class TextToSpeech:
    def __init__(self) -> None:
        self.powershell = shutil.which("powershell") or shutil.which("pwsh")

    def availability(self) -> dict[str, object]:
        available = os.name == "nt" and self.powershell is not None
        return {
            "available": available,
            "engine": "Windows SAPI (System.Speech)",
            "detail": "Local, offline speech synthesis via PowerShell."
            if available else
            "Windows SAPI TTS is unavailable; use the browser's speech synthesis instead.",
        }

    async def _powershell(self, script_path: Path, args: list[str]) -> tuple[int, str]:
        assert self.powershell
        process = await asyncio.create_subprocess_exec(
            str(self.powershell), "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(script_path), *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=45)
        return process.returncode or 0, stderr.decode("utf-8", errors="replace")[:300]

    async def list_voices(self) -> list[dict[str, str]]:
        """Installed SAPI voices, so the UI can say whether Hindi is available."""
        if not self.availability()["available"]:
            return []
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            script_path = temp_dir / "voices.ps1"
            listing = temp_dir / "voices.txt"
            script_path.write_text(SCRIPT, encoding="utf-8")
            code, _ = await self._powershell(script_path, ["", "", "", str(listing)])
            if code != 0 or not listing.exists():
                return []
            voices = []
            for line in listing.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip().lstrip("\ufeff")
                if "|" not in line:
                    continue
                name, _, culture = line.partition("|")
                if name.strip():
                    voices.append({"name": name.strip(), "culture": culture.strip() or "unknown"})
            return voices

    async def synthesize(self, text: str, language: str | None = None,
                         voice_name: str | None = None, *,
                         rate: int = 0, pitch: int = 0,
                         style: str = "jarvis") -> tuple[bytes, dict[str, object]]:
        """Return (wav_bytes, info). info records the route actually taken."""
        status = self.availability()
        if not status["available"]:
            raise RuntimeError(str(status["detail"]))
        cleaned = " ".join(text.split())[:800]
        if not cleaned:
            raise ValueError("Text is empty.")

        wants_hindi = bool(language and language.lower().startswith("hi"))
        info: dict[str, object] = {
            "requested_language": language or "default",
            "devanagari": has_devanagari(cleaned),
            "romanized": False,
            "voice": voice_name or "auto",
            "rate": _bounded(rate),
            "pitch": _bounded(pitch),
            "style": style if style in STYLES else "clear",
        }

        spoken = cleaned
        if info["devanagari"]:
            # A Latin-only voice cannot render Devanagari; romanize so the audio
            # is at least audible. A genuine hi-IN voice is used untouched below.
            hi_voices = [v for v in await self.list_voices()
                         if str(v.get("culture", "")).lower().startswith("hi")]
            if not hi_voices and not (voice_name and voice_name.lower().startswith(("hi", "kalpana", "heera", "madhur"))):
                spoken = romanize(cleaned)
                info["romanized"] = True

        with tempfile.TemporaryDirectory() as temp:
            temp_dir = Path(temp)
            script_path = temp_dir / "speak.ps1"
            text_path = temp_dir / "speech.txt"
            wav_path = temp_dir / "speech.wav"
            script_path.write_text(SCRIPT, encoding="utf-8")
            text_path.write_text(spoken, encoding="utf-8")
            code, stderr = await self._powershell(
                script_path,
                [str(wav_path), str(text_path), voice_name or "", "",
                 str(_bounded(rate)), str(_bounded(pitch))],
            )
            if code != 0 or not wav_path.exists():
                raise RuntimeError("Windows TTS failed: " + stderr)
            audio = wav_path.read_bytes()

        if _is_silent(audio):
            if wants_hindi or info["devanagari"]:
                raise RuntimeError(
                    "No Hindi speech voice is installed on Windows. Add one in "
                    "Settings > Time & language > Language > Add a language > Hindi, "
                    "then enable its speech option."
                )
            raise RuntimeError("Windows TTS produced silent audio for this text.")

        # Shaping happens after the silence check: a silent clip must still
        # raise, and EQ cannot rescue an engine failure.
        audio = shape(audio, str(info["style"]))

        if wants_hindi and info["romanized"]:
            info["note"] = (
                "Spoken with an English voice using Latin phonetics; "
                "no Hindi SAPI voice is installed."
            )
        elif info["romanized"]:
            info["note"] = "Devanagari was transliterated for the Latin-only voice."
        return audio, info
