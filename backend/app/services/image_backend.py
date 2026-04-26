"""Image backend adapter — supports Pollinations (default), HuggingFace Inference API, and a stub for local SDXL.

Switch via IMAGE_BACKEND env var. All backends expose the same async interface:
    await backend.generate(prompt, *, negative_prompt, width, height, seed, reference_image_url, reference_strength) -> bytes
"""
from __future__ import annotations

import asyncio
import random
from urllib.parse import quote

import httpx

from app.core.config import settings


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
        seed = seed if seed is not None else random.randint(1, 2**31 - 1)
        url = (
            f"https://image.pollinations.ai/prompt/{quote(prompt)}"
            f"?width={width}&height={height}&model=flux&nologo=true&seed={seed}"
        )
        if reference_image_url:
            url += f"&image={quote(reference_image_url)}"
            if reference_strength is not None:
                url += f"&strength={reference_strength}"
        # Pollinations is free but rate-limits aggressively; retry with backoff
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                r = await self.client.get(url)
                r.raise_for_status()
                if r.headers.get("content-type", "").startswith("image"):
                    return r.content
                raise RuntimeError(f"Unexpected content-type: {r.headers.get('content-type')}")
            except Exception as e:  # noqa: BLE001
                last_exc = e
                await asyncio.sleep(3.0 * (attempt + 1))
        raise RuntimeError(f"Pollinations failed after 5 retries: {last_exc}")


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


def get_backend():
    backend = settings.IMAGE_BACKEND.lower()
    if backend == "pollinations":
        return PollinationsBackend()
    if backend == "huggingface":
        return HuggingFaceBackend()
    # Local SDXL stub — implement when GPU available
    raise NotImplementedError(f"Image backend '{backend}' not implemented; use 'pollinations'")


image_backend = get_backend()
