"""Layer 4 — Prompt Builder. Bridges naturalistic LLM prompts to structured image-model prompts.

Injects taste profile + business brand aesthetics + quality boosters + negative prompts.
"""
from __future__ import annotations

from app.models.models import BusinessProfile, UserTasteProfile

QUALITY_BOOST = "high quality, detailed, 8k resolution, masterful composition"
DEFAULT_NEGATIVE = (
    "blurry, low quality, oversaturated, watermark, text, logo, "
    "extra fingers, deformed, jpeg artifacts, lowres"
)


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
    parts: list[str] = [base_prompt.strip().rstrip(".")]

    if style_tags:
        parts.append(", ".join(style_tags))

    # Silently inject taste preferences
    if taste:
        if taste.preferred_styles:
            parts.append(", ".join(taste.preferred_styles))
        if taste.preferred_colors:
            parts.append(", ".join(taste.preferred_colors) + " palette")

    # Brand injection for business users
    if business:
        if business.brand_tone:
            parts.append(business.brand_tone)
        colors = (business.brand_colors or {})
        accent = ", ".join(v for v in colors.values() if isinstance(v, str))
        if accent:
            parts.append(f"brand color accents {accent}")

    parts.append(QUALITY_BOOST)
    return ", ".join(parts)


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
