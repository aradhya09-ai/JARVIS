"""Play a synthesized reply straight out of the Windows default output device.

Chrome will not let a page make a sound - through an AudioContext or through
speechSynthesis - until that window has received a real click or keypress. An
assistant driven by a wake word is expected to speak first, so relying on the
tab alone means the first reply of every session is dropped in silence. The
backend has no such rule.

Two Windows constraints shape this module:

* ``winsound.PlaySound`` cannot play asynchronously from memory - combining
  SND_MEMORY with SND_ASYNC raises "Cannot play asynchronously from memory" -
  so each clip is written to a private temporary folder and played from there.
  Files are deleted as soon as the sound engine releases them. Raw microphone
  audio is never written here; only synthesized replies.
* SND_PURGE only reaches a sound started by the calling thread, so play and
  stop are funnelled through one worker thread. That is what lets "stop, JARVIS"
  silence the speakers mid-sentence.
"""
from __future__ import annotations

import atexit
import asyncio
import os
import queue
import tempfile
import threading
import wave
from io import BytesIO
from pathlib import Path

try:  # Windows only; the import is the platform test.
    import winsound
except ImportError:  # pragma: no cover - non-Windows hosts
    winsound = None  # type: ignore[assignment]

_JOBS: "queue.Queue[tuple[str, Path | None, threading.Event] | None]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_temp_dir: Path | None = None
_swept = False
_playing: Path | None = None
_stale: list[Path] = []


def available() -> bool:
    return winsound is not None


def availability() -> dict[str, object]:
    if winsound is None:
        return {
            "available": False,
            "engine": "winsound",
            "detail": (
                "Direct speaker output needs Windows. Replies play through the "
                "browser tab instead, which the browser may block until the page "
                "is clicked."
            ),
        }
    return {
        "available": True,
        "engine": "winsound",
        "detail": (
            "Replies are played on the Windows default output device from the "
            "backend. Each clip is written to a temporary folder for the length "
            "of playback and deleted afterwards; microphone audio is never saved."
        ),
    }


def duration_ms(wav: bytes) -> int:
    """Length of a PCM WAV in milliseconds, from its own header."""
    with wave.open(BytesIO(wav)) as handle:
        frames, rate = handle.getnframes(), handle.getframerate()
    if rate <= 0:
        raise ValueError("WAV header reports an invalid sample rate.")
    return round(frames / rate * 1000)


def _directory() -> Path:
    """The private folder the clips live in while they play.

    A backend that is killed outright never runs its exit cleanup, so the first
    call of each start clears whatever the last process left behind. Sweeping
    only once per process also means this can never delete a clip still playing.
    """
    global _temp_dir, _swept
    if _temp_dir is None:
        _temp_dir = Path(tempfile.gettempdir()) / "jarvis-tts"
        _temp_dir.mkdir(exist_ok=True)
    if not _swept:
        _swept = True
        for leftover in _temp_dir.glob("reply-*.wav"):
            try:
                leftover.unlink()
            except OSError:
                pass
    return _temp_dir


def _drop(path: Path | None) -> None:
    """Delete a clip, or keep it for the next attempt if the engine still holds it."""
    if path is None:
        return
    try:
        os.unlink(path)
    except OSError:
        if path not in _stale:
            _stale.append(path)


def _sweep() -> None:
    for path in list(_stale):
        try:
            os.unlink(path)
            _stale.remove(path)
        except OSError:
            pass


def _apply(kind: str, path: Path | None) -> None:
    global _playing
    if winsound is None:
        return
    # Purge first: one clip at a time, and a new reply must cut off the old one.
    winsound.PlaySound(None, winsound.SND_PURGE)
    _drop(_playing)
    _playing = None
    _sweep()
    if kind == "play" and path is not None:
        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
        _playing = path


def _work() -> None:
    while True:
        job = _JOBS.get()
        if job is None:
            break
        kind, path, done = job
        try:
            _apply(kind, path)
        finally:
            done.set()


def _submit(kind: str, path: Path | None) -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="jarvis-speaker", daemon=True)
            _worker.start()
    done = threading.Event()
    _JOBS.put((kind, path, done))
    if not done.wait(2.0):
        raise RuntimeError("the speaker worker is not responding")


def play(wav: bytes) -> int:
    """Start playback on the default device and return the clip length in ms."""
    if winsound is None:
        raise RuntimeError("Direct speaker output is only available on Windows.")
    length = duration_ms(wav)
    handle, name = tempfile.mkstemp(suffix=".wav", prefix="reply-", dir=_directory())
    path = Path(name)
    try:
        with os.fdopen(handle, "wb") as clip:
            clip.write(wav)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    _submit("play", path)
    return length


def stop() -> None:
    if winsound is None:
        return
    _submit("stop", None)


async def play_async(wav: bytes) -> int:
    return await asyncio.to_thread(play, wav)


async def stop_async() -> None:
    await asyncio.to_thread(stop)


def _shutdown() -> None:
    global _playing
    try:
        stop()
    except Exception:  # pragma: no cover - interpreter teardown
        pass
    _drop(_playing)
    _playing = None
    _sweep()


atexit.register(_shutdown)
