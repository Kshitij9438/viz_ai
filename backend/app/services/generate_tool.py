"""generate() function schema — exact match to Vizzy spec Section 5.2.

Exposed to the LLM via Ollama's tool-use API (OpenAI-compatible shape).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

OutputType = Literal[
    "image",
    "poster",
    "story_sequence",
    "vision_board",
    "video_loop",
    "quote_card",
    "style_transfer",
    "before_after",
]


class GenerateParams(BaseModel):
    output_type: OutputType
    prompt: str
    style_tags: list[str] = Field(default_factory=list)
    negative_prompt: Optional[str] = None
    count: int = 3
    reference_image_url: Optional[str] = None
    reference_strength: Optional[float] = None
    aspect_ratio: Optional[Literal["square", "landscape", "portrait"]] = "square"
    output_size: Optional[str] = "1024x1024"
    sequence_count: Optional[int] = None
    poster_text: Optional[str] = None
    poster_layout: Optional[
        Literal["hero_text_top", "hero_text_bottom", "split_text_right", "minimal_center"]
    ] = None


GENERATE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "generate",
        "description": (
            "Create visual content for the user. Call ONLY after you have confirmed "
            "the creative direction with the user. Do not call on the very first turn "
            "unless the user's intent is unambiguous."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "output_type": {
                    "type": "string",
                    "enum": [
                        "image", "poster", "story_sequence", "vision_board",
                        "video_loop", "quote_card", "style_transfer", "before_after",
                    ],
                    "description": "Kind of asset to produce.",
                },
                "prompt": {"type": "string", "description": "Naturalistic creative prompt."},
                "style_tags": {"type": "array", "items": {"type": "string"}},
                "negative_prompt": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 9, "default": 3},
                "reference_image_url": {"type": "string"},
                "reference_strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "aspect_ratio": {"type": "string", "enum": ["square", "landscape", "portrait"]},
                "output_size": {"type": "string", "description": "WxH e.g. 1024x1024"},
                "sequence_count": {"type": "integer", "minimum": 3, "maximum": 8},
                "poster_text": {"type": "string"},
                "poster_layout": {
                    "type": "string",
                    "enum": ["hero_text_top", "hero_text_bottom", "split_text_right", "minimal_center"],
                },
            },
            "required": ["output_type", "prompt"],
        },
    },
}
