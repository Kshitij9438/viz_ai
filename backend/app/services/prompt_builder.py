"""Layer 4 — Prompt Builder. Bridges naturalistic LLM prompts to structured image-model prompts.

Injects taste profile + business brand aesthetics + quality boosters + negative prompts.
"""
from __future__ import annotations

from app.models.models import BusinessProfile, UserTasteProfile

QUALITY_BOOST = "high quality, detailed, masterful composition"
DEFAULT_NEGATIVE = (
    "blurry, low quality, oversaturated, watermark, text, logo, "
    "extra fingers, deformed, jpeg artifacts, lowres"
)

# Maximum prompt length sent to image generation APIs.
# Pollinations encodes the prompt in the URL — excessively long prompts
# cause 429s, URL truncation, and wasted tokens.
MAX_PROMPT_LENGTH = 400


def _truncate_prompt(prompt: str, max_len: int = MAX_PROMPT_LENGTH) -> str:
    """Intelligently truncate a prompt to max_len characters.

    Preserves complete comma-separated clauses rather than cutting mid-word.
    """
    if len(prompt) <= max_len:
        return prompt

    # Try to cut at a comma boundary
    truncated = prompt[:max_len]
    last_comma = truncated.rfind(",")
    if last_comma > max_len * 0.5:  # only if we keep at least half
        truncated = truncated[:last_comma]

    return truncated.rstrip(", ")


def _aspect_to_size(aspect: str | None, output_size: str | None) -> tuple[int, int]:
    if output_size:
        try:
            w, h = output_size.lower().split("x")
            return int(w), int(h)
        except Exception:  # noqa: BLE001
            pass
    if aspect == "landscape":
        return 1280, 768
    if aspect == "portrait":
        return 768, 1280
    return 1024, 1024


def build_image_prompt(
    base_prompt: str,
    *,
    style_tags: list[str] | None = None,
    taste: UserTasteProfile | None = None,
    business: BusinessProfile | None = None,
) -> str:
    """Build an enriched image prompt, truncated to MAX_PROMPT_LENGTH.

    Priority order (most important first):
    1. User's base prompt
    2. Style tags
    3. Quality boost
    4. Taste preferences (only if space allows)
    5. Business brand (only if space allows)
    """
    # Start with the core prompt
    parts: list[str] = [base_prompt.strip().rstrip(".")]

    if style_tags:
        parts.append(", ".join(style_tags[:5]))  # cap style tags

    parts.append(QUALITY_BOOST)

    # Build core prompt first
    core = ", ".join(parts)

    # Only add taste/business if we have room
    extras: list[str] = []

    if taste:
        if taste.preferred_styles:
            extras.append(", ".join(taste.preferred_styles[:3]))
        if taste.preferred_colors:
            extras.append(", ".join(taste.preferred_colors[:3]) + " palette")

    if business and business.brand_tone:
        extras.append(business.brand_tone[:60])

    if extras:
        full = core + ", " + ", ".join(extras)
    else:
        full = core

    return _truncate_prompt(full)


def build_negative_prompt(
    user_negative: str | None = None, taste: UserTasteProfile | None = None
) -> str:
    parts = [DEFAULT_NEGATIVE]
    if user_negative:
        parts.append(user_negative)
    if taste and taste.disliked_styles:
        parts.append(", ".join(taste.disliked_styles))
    return ", ".join(parts)


def resolve_size(aspect: str | None, output_size: str | None) -> tuple[int, int]:
    return _aspect_to_size(aspect, output_size)
