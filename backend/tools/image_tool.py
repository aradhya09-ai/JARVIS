"""Image generation through an explicitly configured provider.

There is no bundled local image model. If no provider is configured this tool
fails with setup instructions instead of inventing images.
"""
from __future__ import annotations

import asyncio
import base64
import re
from datetime import datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

import paths
from security.permissions import Risk
from tools.base import ToolContext, ToolDefinition, ToolError, ToolResult

IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")


class ImageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=3, max_length=800)
    filename: str | None = Field(default=None, max_length=80)
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=576, ge=256, le=2048)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip()).strip("-").lower()
    return cleaned[:48] or "image"


async def _provider_status(settings) -> dict:
    provider = settings.image_provider
    if provider == "none":
        return {"available": False, "provider": "none",
                "reason": "No image provider configured (JARVIS_IMAGE_PROVIDER=none)."}
    if provider == "local":
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.get(f"{settings.image_base_url}/sdapi/v1/options")
            available = response.status_code == 200
            return {"available": available, "provider": "local",
                    "endpoint": settings.image_base_url,
                    "reason": None if available else f"SD WebUI returned HTTP {response.status_code}."}
        except httpx.HTTPError as exc:
            return {"available": False, "provider": "local", "endpoint": settings.image_base_url,
                    "reason": f"SD WebUI is not reachable ({exc.__class__.__name__})."}
    if provider == "openai":
        if not settings.image_api_key:
            return {"available": False, "provider": "openai",
                    "reason": "JARVIS_IMAGE_API_KEY is not set."}
        return {"available": True, "provider": "openai", "endpoint": settings.image_base_url,
                "reason": None, "billing": "This provider may charge per generated image."}
    return {"available": False, "provider": provider, "reason": f"Unknown provider '{provider}'."}


async def _generate_local(settings, prompt: str, width: int, height: int) -> bytes:
    payload = {
        "prompt": prompt,
        "width": width,
        "height": height,
        "steps": 24,
        "cfg_scale": 6.5,
        "sampler_name": "DPM++ 2M Karras",
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(f"{settings.image_base_url}/sdapi/v1/txt2img", json=payload)
    if response.status_code != 200:
        raise ToolError(f"SD WebUI returned HTTP {response.status_code}: {response.text[:200]}")
    data = response.json()
    images = data.get("images") or []
    if not images:
        raise ToolError("SD WebUI returned no images.")
    return base64.b64decode(images[0].split(",")[-1])


async def _generate_openai(settings, prompt: str, width: int, height: int) -> bytes:
    payload = {
        "model": settings.image_model or "gpt-image-1",
        "prompt": prompt,
        "size": f"{width}x{height}",
        "n": 1,
    }
    headers = {"Authorization": f"Bearer {settings.image_api_key}"}
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            f"{settings.image_base_url}/images/generations", json=payload, headers=headers
        )
    if response.status_code != 200:
        raise ToolError(f"Image service returned HTTP {response.status_code}: {response.text[:200]}")
    data = response.json().get("data") or []
    if not data:
        raise ToolError("Image service returned no images.")
    item = data[0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        async with httpx.AsyncClient(timeout=60.0) as client:
            download = await client.get(item["url"])
        if download.status_code != 200:
            raise ToolError("Downloading the generated image failed.")
        return download.content
    raise ToolError("Image service response did not contain image data.")


async def generate_handler(args: ImageArgs, ctx: ToolContext) -> ToolResult:
    status = await _provider_status(ctx.settings)
    if not status["available"]:
        return ToolResult.failure(
            "Image generation is unavailable. " + str(status.get("reason")),
            provider_status=status,
        )
    ctx.note(f"Generating image with {status['provider']} provider")
    provider = status["provider"]
    if provider == "local":
        payload = await _generate_local(ctx.settings, args.prompt, args.width, args.height)
    else:
        payload = await _generate_openai(ctx.settings, args.prompt, args.width, args.height)

    if not payload.startswith(IMAGE_MAGIC):
        raise ToolError("The provider response was not a recognised image file.")

    target_dir = ctx.settings.output_dir / "images"
    target_dir.mkdir(parents=True, exist_ok=True)
    base = _slug(args.filename or args.prompt)
    destination = target_dir / f"{base}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    await asyncio.to_thread(destination.write_bytes, payload)
    ctx.audit.record(ctx.actor, "image.generate", Risk.LOW.value,
                     {"provider": provider, "path": str(destination)}, "ok")
    # Only the file this handler just wrote is opened, never a path from the model.
    opened, _ = await asyncio.to_thread(paths.open_path, destination)
    summary = "Executed." if opened else "Generated, but the viewer did not open."
    if status.get("billing"):
        summary += " " + status["billing"]
    return ToolResult.success(summary, path=str(destination), provider=provider,
                              opened=opened, bytes=len(payload))


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def status_handler(_: EmptyArgs, ctx: ToolContext) -> ToolResult:
    status = await _provider_status(ctx.settings)
    if status["available"]:
        summary = f"Image provider '{status['provider']}' is available."
    else:
        summary = f"Image generation is unavailable: {status.get('reason')}"
    return ToolResult.success(summary, provider_status=status)


def build() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="image.generate",
            description="Generate an image through the configured local or cloud provider.",
            args_model=ImageArgs,
            risk=Risk.MODERATE,
            handler=generate_handler,
            # Some providers charge per picture, and the user asked to be asked
            # before anything spends money. A local or unset provider never does.
            confirm_when=lambda args, settings: settings.image_provider == "openai",
            category="images",
        ),
        ToolDefinition(
            name="image.status",
            description="Check whether an image provider is configured and reachable.",
            args_model=EmptyArgs,
            risk=Risk.LOW,
            handler=status_handler,
            category="images",
        ),
    ]
