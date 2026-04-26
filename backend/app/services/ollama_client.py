"""GitHub Models client — drop-in replacement for OllamaClient."""
from __future__ import annotations

import base64
from typing import Any

from openai import AsyncOpenAI
from app.core.config import settings


class GitHubModelsClient:
    def __init__(self) -> None:
        self.client = AsyncOpenAI(
            base_url="https://models.inference.ai.azure.com",
            api_key=settings.GITHUB_TOKEN,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        model: str | None = None,
        stream: bool = False,
        options: dict | None = None,  # ignored, kept for compatibility
    ) -> dict:
        kwargs: dict[str, Any] = {
            "model": model or settings.GITHUB_MODEL,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)
        msg = response.choices[0].message

        # Normalize to the same dict shape conversation.py already expects
        tool_calls_raw = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls_raw.append({
                    "id": tc.id,
                    "type": "function",          # ← REQUIRED by OpenAI protocol
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,  # JSON string
                    },
                })

        return {
            "message": {
                "content": msg.content or "",
                "tool_calls": tool_calls_raw,
            }
        }

    async def caption_image(self, image_bytes: bytes) -> str:
        """Use GPT-4.1-mini vision to caption an uploaded image."""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        response = await self.client.chat.completions.create(
            model=settings.GITHUB_VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                    {
                        "type": "text",
                        "text": "Describe this image in detail for a creative AI assistant. Focus on subject, mood, lighting, colors, and style. Two sentences.",
                    },
                ],
            }],
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()

    async def complete(self, prompt: str, model: str | None = None) -> str:
        response = await self.client.chat.completions.create(
            model=model or settings.GITHUB_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )
        return response.choices[0].message.content.strip()


ollama = GitHubModelsClient()  # keeps the same import name everywhere