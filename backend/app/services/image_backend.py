"""Image backend adapter — supports Pollinations (default), HuggingFace Inference API, and a stub for local SDXL.

Switch via IMAGE_BACKEND env var. All backends expose the same async interface:
    await backend.generate(prompt, *, negative_prompt, width, height, seed, reference_image_url, reference_strength) -> bytes
"""
from __future__ import annotations

import asyncio
import io
import logging
import random
from urllib.parse import quote

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.core.config import settings

logger = logging.getLogger("vizzy.image_backend")


def _placeholder_image(width: int = 512, height: int = 512, message: str = "Generation unavailable") -> bytes:
    """Return a gradient placeholder image with an error message.

    Used as a fallback when the image backend is completely unreachable,
    preventing cascade failure in the pipeline.
    """
    img = Image.new("RGB", (width, height))
    for y in range(height):
        r = int(40 + (y / height) * 60)
        g = int(20 + (y / height) * 40)
        b = int(80 + (y / height) * 80)
        for x in range(width):
            img.putpixel((x, y), (r, g, b))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=max(16, height // 30))
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), message, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = (height - th) // 2
    draw.text((x, y), message, font=font, fill=(200, 200, 200))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=80)
    return out.getvalue()


class PollinationsBackend:
    name = "pollinations"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=120.0)

    async def generate(
        self,
        prompt: str,
        *,
        negative_prompt: str | None = None,
        width: int = 1024,
        height: int = 1024,
        seed: int | None = None,
        reference_image_url: str | None = None,
        reference_strength: float | None = None,
    ) -> bytes:
        """Generate a single image via Pollinations.

        Rate limiting and retry are handled by the caller via
        ``rate_limited_image_call`` — this method does ONE attempt.
        """
        seed = seed if seed is not None else random.randint(1, 2**31 - 1)
        url = (
            f"https://image.pollinations.ai/prompt/{quote(prompt)}"
            f"?width={width}&height={height}&model=flux&nologo=true&seed={seed}"
        )
        if reference_image_url:
            url += f"&image={quote(reference_image_url)}"
            if reference_strength is not None:
                url += f"&strength={reference_strength}"

        r = await self.client.get(url)

        # Specific 429 handling — raise so retry_with_backoff can detect it
        if r.status_code == 429:
            raise httpx.HTTPStatusError(
                "Rate limited by Pollinations",
                request=r.request,
                response=r,
            )

        r.raise_for_status()

        if r.headers.get("content-type", "").startswith("image"):
            return r.content

        raise RuntimeError(f"Unexpected content-type: {r.headers.get('content-type')}")

    async def generate_safe(
        self,
        prompt: str,
        **kwargs,
    ) -> bytes:
        """Generate with full rate limiting, retry, and placeholder fallback.

        This is the method pipelines should call.
        """
        from app.core.rate_control import rate_limited_image_call

        try:
            return await rate_limited_image_call(self.generate, prompt, **kwargs)
        except Exception as exc:
            logger.error(
                "image_generation_failed_using_placeholder",
                extra={
                    "event": "image_generation_failed",
                    "error": str(exc)[:300],
                    "prompt": prompt[:100],
                },
            )
            return _placeholder_image(
                width=kwargs.get("width", 512),
                height=kwargs.get("height", 512),
                message="Image generation temporarily unavailable",
            )


class HuggingFaceBackend:
    name = "huggingface"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=180.0)

    async def generate(self, prompt: str, **kw) -> bytes:
        if not settings.HF_TOKEN:
            raise RuntimeError("HF_TOKEN not set")
        api = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
        r = await self.client.post(
            api,
            headers={"Authorization": f"Bearer {settings.HF_TOKEN}"},
            json={"inputs": prompt},
        )
        r.raise_for_status()
        return r.content

    async def generate_safe(self, prompt: str, **kwargs) -> bytes:
        from app.core.rate_control import rate_limited_image_call

        try:
            return await rate_limited_image_call(self.generate, prompt, **kwargs)
        except Exception as exc:
            logger.error("hf_generation_failed", extra={"error": str(exc)[:300]})
            return _placeholder_image(message="Image generation temporarily unavailable")


def get_backend():
    backend = settings.IMAGE_BACKEND.lower()
    if backend == "pollinations":
        return PollinationsBackend()
    if backend == "huggingface":
        return HuggingFaceBackend()
    # Local SDXL stub — implement when GPU available
    raise NotImplementedError(f"Image backend '{backend}' not implemented; use 'pollinations'")


image_backend = get_backend()
