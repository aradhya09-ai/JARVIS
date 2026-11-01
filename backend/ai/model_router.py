"""Provider abstraction for local Ollama and OpenAI-compatible cloud models."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from config import Settings
from database.repository import Repository

logger = logging.getLogger("jarvis.ai")

# Long enough for a cold Ollama to answer /api/tags, short enough to stay snappy.
PROBE_TIMEOUT = 8.0
# A failed probe is often transient (model loading), so don't keep saying
# "unavailable" for as long as a healthy one is remembered.
CACHE_OK_SECONDS = 20.0
CACHE_FAIL_SECONDS = 3.0

SYSTEM_PROMPT = (
    "You are JARVIS, a concise, precise desktop assistant running on the user's Windows laptop. "
    "You can use only the tools you are given; you never invent tool results, prices, emails, "
    "calendar events, routes or system readings. If a request cannot be fulfilled with the "
    "available tools, say so plainly. Understand and reply in English, Hindi (Devanagari) or "
    "Hinglish, matching the language the user wrote in. Keep answers short."
)


def clock_context(user_name: str = "") -> str:
    """The part of every prompt that changes from minute to minute.

    Deliberately separate from SYSTEM_PROMPT: a small model has no idea what
    today is, so the date, time and timezone are stated on each request instead
    of being left to training data — but Ollama only reuses its prefill cache
    for a byte-identical prefix. Measured on this laptop, a clock line placed at
    the top made every command re-read the whole tool catalog (82s), while the
    same catalog with the clock moved behind it answered in 9.7s.
    """
    now = datetime.now().astimezone()
    lines = [
        f"Right now it is {now.strftime('%A, %d %B %Y at %H:%M')} "
        f"({now.strftime('%Z') or now.tzname()}). Use this for any question about today, "
        "yesterday, tomorrow or the time."
    ]
    if user_name:
        lines.append(f"The user's name is {user_name}.")
    return "\n".join(lines)


class ModelUnavailable(RuntimeError):
    """Base for model failures. Subclasses carry the real reason."""


class ServerUnreachable(ModelUnavailable):
    """The provider process could not be contacted at all."""


class ModelNotInstalled(ModelUnavailable):
    """The provider is up but the configured model is missing."""


class ModelTimeout(ModelUnavailable):
    """The provider accepted the request but took too long."""


class InvalidModelResponse(ModelUnavailable):
    """The provider replied, but not in a form we could use."""


@dataclass
class ProviderStatus:
    name: str
    kind: str
    available: bool
    detail: str
    model: str | None = None


@dataclass
class ModelRouter:
    settings: Settings
    repo: Repository
    _cache: dict[str, tuple[float, ProviderStatus]] = field(default_factory=dict)
    _last_provider: str | None = None
    # When the model last answered something real. The keep-warm loop uses it to
    # decide whether the user is still talking to JARVIS.
    last_request_at: float = 0.0

    # ------------------------------------------------------------- settings
    def _ollama_model(self) -> str:
        return self.repo.get_setting("ai.ollama_model") or self.settings.ollama_model

    def _preference(self) -> str:
        return (self.repo.get_setting("ai.provider", "auto") or "auto").lower()

    def _temperature(self) -> float:
        try:
            return float(self.repo.get_setting("ai.temperature", self.settings.llm_temperature))
        except (TypeError, ValueError):
            return self.settings.llm_temperature

    # --------------------------------------------------------------- health
    async def _ollama_status(self) -> ProviderStatus:
        model = self._ollama_model()
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
                response = await client.get(f"{self.settings.ollama_url}/api/tags")
            if response.status_code != 200:
                return ProviderStatus("ollama", "local", False,
                                      f"Ollama returned HTTP {response.status_code}.", model)
            names = [m.get("name", "") for m in response.json().get("models", [])]
            matching = [n for n in names if n == model or n.split(":")[0] == model.split(":")[0]]
            if not matching:
                return ProviderStatus(
                    "ollama", "local", False,
                    f"Model '{model}' is not installed. Run: ollama pull {model}. "
                    f"Installed: {', '.join(names) or 'none'}.",
                    model,
                )
            return ProviderStatus("ollama", "local", True, f"Ready ({matching[0]}).", matching[0])
        except httpx.TimeoutException:
            # A cold Ollama can take several seconds to answer /api/tags. Reporting
            # that as "not running" was what made a working model look unavailable.
            return ProviderStatus(
                "ollama", "local", False,
                f"Ollama did not answer within {PROBE_TIMEOUT:.0f}s. It may be starting "
                f"or loading {model} into memory; retry in a moment.", model,
            )
        except httpx.HTTPError:
            return ProviderStatus(
                "ollama", "local", False,
                f"Ollama is not reachable at {self.settings.ollama_url}. Start it with: "
                "ollama serve", model,
            )

    async def _cloud_status(self) -> ProviderStatus:
        if not self.settings.cloud_base_url or not self.settings.cloud_api_key:
            return ProviderStatus("cloud", "cloud", False,
                                  "Cloud provider is not configured (JARVIS_CLOUD_BASE_URL / API key).",
                                  self.settings.cloud_model or None)
        try:
            headers = {"Authorization": f"Bearer {self.settings.cloud_api_key}"}
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.get(f"{self.settings.cloud_base_url}/models", headers=headers)
            if response.status_code in {401, 403}:
                return ProviderStatus("cloud", "cloud", False,
                                      "Cloud provider rejected the API key.", self.settings.cloud_model)
            return ProviderStatus("cloud", "cloud", True,
                                  f"Ready ({self.settings.cloud_model or 'default model'}).",
                                  self.settings.cloud_model or None)
        except httpx.HTTPError as exc:
            return ProviderStatus("cloud", "cloud", False,
                                  f"Cloud provider unreachable ({exc.__class__.__name__}).",
                                  self.settings.cloud_model or None)

    async def status(self, force: bool = False) -> list[ProviderStatus]:
        now = time.time()
        results = []
        for key, loader in (("ollama", self._ollama_status), ("cloud", self._cloud_status)):
            cached = self._cache.get(key)
            ttl = CACHE_OK_SECONDS if cached and cached[1].available else CACHE_FAIL_SECONDS
            if cached and not force and now - cached[0] < ttl:
                results.append(cached[1])
                continue
            status = await loader()
            self._cache[key] = (now, status)
            results.append(status)
        return results

    async def active(self) -> ProviderStatus | None:
        statuses = {s.name: s for s in await self.status()}
        preference = self._preference()
        if preference == "ollama":
            return statuses["ollama"] if statuses["ollama"].available else None
        if preference == "cloud":
            return statuses["cloud"] if statuses["cloud"].available else None
        if statuses["ollama"].available:
            return statuses["ollama"]
        if statuses["cloud"].available:
            return statuses["cloud"]
        return None

    def active_name(self) -> str:
        return self._last_provider or "no model"

    async def warm(self, messages: list[dict[str, str]] | None = None) -> None:
        """Page the model into memory so the first real command is not a minute long.

        With `messages` the ping repeats the prompt a real command will send: the
        model keeps both its weights *and* its prefill cache, so warming never
        throws away the catalog reading the conversation just paid for.
        """
        provider = await self.active()
        if provider is None or provider.kind != "local":
            return
        payload: dict[str, Any] = {
            "model": provider.model,
            "messages": messages or [{"role": "user", "content": "ok"}],
            "stream": False,
            "keep_alive": self.settings.ollama_keep_alive,
            "options": {"num_predict": 4},
        }
        if messages:
            payload["format"] = "json"
        try:
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout) as client:
                await client.post(f"{self.settings.ollama_url}/api/chat", json=payload)
            logger.info("Local model %s warmed up", provider.model)
        except httpx.HTTPError as exc:
            logger.info("Model warm-up skipped (%s)", exc.__class__.__name__)

    # ------------------------------------------------------------- complete
    async def complete(self, messages: list[dict[str, str]], json_mode: bool = False) -> str:
        provider = await self.active()
        if provider is None:
            raise await self._unavailable_error()
        if provider.kind == "local":
            text = await self._ollama_complete(provider, messages, json_mode)
        else:
            text = await self._cloud_complete(messages, json_mode)
        self._last_provider = f"{provider.kind}:{provider.model or 'default'}"
        self.last_request_at = time.time()
        return text

    async def _unavailable_error(self) -> ModelUnavailable:
        """Report the real reason, not a generic "no model" claim.

        Ollama being slow to load a model is different from it not running, and
        the dashboard needs to say which one happened.
        """
        statuses = {s.name: s for s in await self.status()}
        preference = self._preference()
        primary = ("cloud" if preference == "cloud" else "ollama")
        detail = statuses[primary].detail if primary in statuses else "No provider configured."
        lowered = detail.lower()
        if "not installed" in lowered:
            return ModelNotInstalled(detail)
        if "did not answer" in lowered or "timed out" in lowered:
            return ModelTimeout(detail)
        if "not reachable" in lowered or "not running" in lowered or "http " in lowered:
            return ServerUnreachable(detail)
        return ModelUnavailable(detail)

    def _max_tokens(self, json_mode: bool) -> int:
        # Routing only has to emit one small JSON object. Letting a 3B model run
        # to the full answer budget is how one routing call took 77s instead of 4.
        return self.settings.llm_route_max_tokens if json_mode else self.settings.llm_max_tokens

    async def _ollama_complete(self, provider: ProviderStatus, messages: list[dict[str, str]],
                               json_mode: bool) -> str:
        payload: dict[str, Any] = {
            "model": provider.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.settings.ollama_keep_alive,
            "options": {
                "temperature": self._temperature(),
                "num_predict": self._max_tokens(json_mode),
            },
        }
        if json_mode:
            payload["format"] = "json"
        try:
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout) as client:
                response = await client.post(f"{self.settings.ollama_url}/api/chat", json=payload)
                if response.status_code == 404:
                    raise ModelNotInstalled(
                        f"Ollama does not have the model '{provider.model}'. "
                        f"Run: ollama pull {provider.model}")
                response.raise_for_status()
                body = response.json()
        except asyncio.TimeoutError as exc:
            self._cache.pop("ollama", None)
            raise ModelTimeout(
                f"The model {provider.model} took longer than "
                f"{self.settings.ollama_timeout:.0f}s to answer.") from exc
        except httpx.TimeoutException as exc:
            self._cache.pop("ollama", None)
            raise ModelTimeout(
                f"The model {provider.model} took longer than "
                f"{self.settings.ollama_timeout:.0f}s to answer.") from exc
        except httpx.HTTPStatusError as exc:
            self._cache.pop("ollama", None)
            raise ServerUnreachable(
                f"Ollama rejected the request: HTTP {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            self._cache.pop("ollama", None)
            raise ServerUnreachable(
                f"Lost contact with Ollama while asking {provider.model} "
                f"({exc.__class__.__name__}).") from exc
        content = body.get("message", {}).get("content") if isinstance(body, dict) else None
        if not isinstance(content, str):
            raise InvalidModelResponse(
                f"Ollama replied without a message body for {provider.model}.")
        # Which half of a slow reply was reading and which was writing, and
        # whether the tool catalog had to be read again: a cold read costs the
        # user ~30s here, a cached one ~8s. Without this the difference is guesswork.
        logger.info(
            "%s: read %s tokens (%s already cached), wrote %s, load %.1fs, eval %.1fs",
            provider.model,
            body.get("prompt_eval_count", "unknown"),
            body.get("prompt_eval_cached_count", 0),
            body.get("eval_count", "unknown"),
            round((body.get("load_duration") or 0) / 1e9, 1),
            round((body.get("eval_duration") or 0) / 1e9, 1),
        )
        return content

    async def _cloud_complete(self, messages: list[dict[str, str]], json_mode: bool) -> str:
        model = self.settings.cloud_model
        if not model:
            raise ModelUnavailable("JARVIS_CLOUD_MODEL is not set.")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": self._temperature(),
            "max_tokens": self._max_tokens(json_mode),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.settings.cloud_api_key}"}
        url = f"{self.settings.cloud_base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.settings.ollama_timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code == 400 and json_mode:
                    payload.pop("response_format", None)
                    response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
        except httpx.TimeoutException as exc:
            self._cache.pop("cloud", None)
            raise ModelTimeout(
                f"Cloud model {model} took longer than "
                f"{self.settings.ollama_timeout:.0f}s to answer.") from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            self._cache.pop("cloud", None)
            raise ServerUnreachable(f"Cloud model request failed: {exc}") from exc


async def safe_complete(router: ModelRouter, messages: list[dict[str, str]],
                        json_mode: bool = False) -> tuple[str | None, str]:
    try:
        return await router.complete(messages, json_mode=json_mode), router.active_name()
    except ModelUnavailable as exc:
        logger.info("Model unavailable: %s", exc)
        return None, str(exc)
