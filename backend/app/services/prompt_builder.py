"""Layer 4 — Prompt Builder. Bridges user intent to compact image-model prompts.

User intent stays dominant; taste and brand contribute short style keywords only
(no raw taste paragraphs — avoids repeated generic scenes).
"""
from __future__ import annotations

import logging
import random
import re
from typing import Iterable

from app.models.models import BusinessProfile, UserTasteProfile

logger = logging.getLogger("vizzy.prompt")

# Pollinations encodes the prompt in the URL — keep total length bounded.
MAX_PROMPT_LENGTH = 380

_DEFAULT_INTENT = "creative composition"

# Concrete scene/object nouns — never pass through as "style".
_SCENE_OBJECT_BLOCKLIST: frozenset[str] = frozenset(
    {
        "bench",
        "swing",
        "swings",
        "wooden",
        "porch",
        "deck",
        "fence",
        "park",
        "garden",
        "trail",
        "forest",
        "beach",
        "lake",
        "ocean",
        "river",
        "meadow",
        "tree",
        "trees",
        "flower",
        "flowers",
        "grass",
        "field",
        "mountain",
        "path",
        "bridge",
        "chair",
        "table",
        "rock",
        "rocks",
        "water",
        "outdoor",
        "indoors",
        "room",
        "kitchen",
        "bedroom",
        "street",
        "car",
        "cars",
        "dog",
        "cat",
        "people",
        "person",
        "sitting",
        "walking",
    }
)

# Environment / location leakage ("outdoor calm", "natural setting") — not style-safe.
_RISKY_ENVIRONMENT_WORDS: frozenset[str] = frozenset(
    {
        "environment",
        "environments",
        "setting",
        "settings",
        "outdoors",
        "scenic",
        "wilderness",
        "countryside",
        "backyard",
        "patio",
        "woodsy",
        "rustic",
        "terrain",
        "shoreline",
        "coastline",
        "locale",
        "surroundings",
        "vista",
        "panorama",
        "nature",
    }
)

# "natural" is OK only in lighting/color contexts — not "natural world / setting".
_NATURAL_OK_SUBSTRINGS: tuple[str, ...] = (
    "natural light",
    "natural lighting",
    "natural tones",
    "natural palette",
    "natural colors",
    "natural color",
    "natural shadows",
    "natural gradient",
)

_QUALITY_VARIANTS: tuple[str, ...] = (
    "8k, ultra detailed, cinematic, volumetric lighting",
    "ultra detailed, sharp focus, cinematic color grade",
    "8k, highly detailed render, professional lighting",
    "ultra detailed, cinematic contrast, crisp focus",
    "high fidelity, detailed, volumetric light, film grain",
)

_VARIATION_COMPOSITION: tuple[str, ...] = (
    "wide angle cinematic shot",
    "close-up detailed composition",
    "dramatic perspective",
    "top-down view",
    "minimalist framing",
    "eye-level balanced framing",
)

_VARIATION_LIGHTING: tuple[str, ...] = (
    "soft ambient lighting",
    "high contrast lighting",
    "golden hour lighting",
    "neon glow lighting",
    "rim light accent",
    "diffused studio-style light",
    "cinematic key light",
)


def _contains_blocklisted_scene(text: str) -> bool:
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return any(w in _SCENE_OBJECT_BLOCKLIST for w in words)


def _contains_risky_environment(text: str) -> bool:
    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    if words & _RISKY_ENVIRONMENT_WORDS:
        return True
    low = text.lower()
    if "outdoor" in low or "indoor" in low:
        return True
    return False


def _segment_bad_natural(word: str) -> bool:
    """Reject 'natural' when it implies setting/scene, allow lighting/color phrases."""
    low = word.lower()
    if "natural" not in low:
        return False
    for ok in _NATURAL_OK_SUBSTRINGS:
        if ok in low:
            return False
    return True


def _segment_allowed_for_style(segment: str) -> bool:
    if not segment or len(segment) > 90:
        return False
    if _contains_blocklisted_scene(segment):
        return False
    if _contains_risky_environment(segment):
        return False
    if _segment_bad_natural(segment):
        return False
    return True


def extract_style_keywords(taste: str) -> str:
    """Compress free-form taste prose into short comma-separated style tags (max 5–7).

    Drops scene/environment leakage; keeps mood- and aesthetic-safe phrases.
    """
    if not taste or not str(taste).strip():
        return ""

    segments: list[str] = []
    for raw in re.split(r"[,;\n]+", taste):
        part = " ".join(raw.split()).strip()
        if not part:
            continue
        if not _segment_allowed_for_style(part):
            continue
        segments.append(part.rstrip("."))
        if len(segments) >= 7:
            break

    return ", ".join(segments[:7])


def _sanitize_tokens(tokens: Iterable[str]) -> list[str]:
    out: list[str] = []
    for t in tokens:
        s = str(t).strip()
        if not s or len(s) > 48:
            continue
        if not _segment_allowed_for_style(s):
            continue
        out.append(s)
    return out


def _brand_tone_keywords(brand_tone: str | None) -> str:
    if not brand_tone:
        return ""
    tone = " ".join(brand_tone.split())[:80]
    if not _segment_allowed_for_style(tone):
        return ""
    words = tone.split()
    if len(words) <= 4:
        return tone
    return " ".join(words[:4])


def _random_composition_and_lighting() -> str:
    """Two tokens — composition + lighting — for stronger diversity per request."""
    return f"{random.choice(_VARIATION_COMPOSITION)}, {random.choice(_VARIATION_LIGHTING)}"


def _truncate_style_only(style: str, max_len: int) -> str:
    if len(style) <= max_len:
        return style.rstrip(", ")
    truncated = style[:max_len]
    last_comma = truncated.rfind(",")
    if last_comma > max_len * 0.4:
        truncated = truncated[:last_comma]
    return truncated.rstrip(", ")


def build_image_prompt(
    base_prompt: str,
    *,
    style_tags: list[str] | None = None,
    taste: UserTasteProfile | None = None,
    business: BusinessProfile | None = None,
) -> str:
    """Build prompt: user intent first; taste/brand as short Style: keywords only.

    Total length capped at MAX_PROMPT_LENGTH by trimming the style segment only,
    never the user intent.
    """
    intent = (base_prompt or "").strip().rstrip(".")
    if not intent:
        intent = _DEFAULT_INTENT

    style_pieces: list[str] = []
    if style_tags:
        style_pieces.extend(_sanitize_tokens(style_tags))

    if taste:
        style_pieces.extend(_sanitize_tokens(taste.preferred_styles or []))
        if taste.preferred_colors:
            colors = _sanitize_tokens([f"{c} tones" for c in taste.preferred_colors[:4]])
            style_pieces.extend(colors)
        summary_kw = extract_style_keywords(taste.taste_summary or "")
        if summary_kw:
            for chunk in summary_kw.split(", "):
                chunk = chunk.strip()
                if chunk:
                    style_pieces.append(chunk)

    brand_kw = _brand_tone_keywords(business.brand_tone if business else None)
    if brand_kw:
        style_pieces.append(brand_kw)

    seen: set[str] = set()
    uniq: list[str] = []
    for p in style_pieces:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(p)

    uniq.append(_random_composition_and_lighting())

    style_core = ", ".join(uniq[:12])

    quality = random.choice(_QUALITY_VARIANTS)

    fixed_overhead = len(". Style: . ") + len(quality) + 8
    budget = MAX_PROMPT_LENGTH - len(intent) - fixed_overhead
    if budget < 24:
        budget = 24

    style_trimmed = _truncate_style_only(style_core, budget)
    if not style_trimmed:
        style_trimmed = "refined, contemporary"

    final = f"{intent}. Style: {style_trimmed}. {quality}"

    if len(final) > MAX_PROMPT_LENGTH:
        tighter_budget = max(16, budget - (len(final) - MAX_PROMPT_LENGTH) - 2)
        style_trimmed = _truncate_style_only(style_core, tighter_budget)
        final = f"{intent}. Style: {style_trimmed}. {quality}"

    if len(final) > MAX_PROMPT_LENGTH:
        quality = "8k, ultra detailed, sharp focus."
        final = f"{intent}. Style: {style_trimmed}. {quality}"

    if not final.startswith(intent):
        logger.warning(
            "prompt_intent_mismatch",
            extra={"event": "prompt_intent_mismatch", "intent": intent[:120], "prompt": final[:200]},
        )
        final = f"{intent}. Style: {style_trimmed}. {quality}"[:MAX_PROMPT_LENGTH]

    # Audit: intent must always prefix the prompt (also catches truncation bugs)
    assert final.startswith(intent), (
        f"intent-first invariant: intent={intent[:80]!r} final={final[:120]!r}"
    )

    logger.info(
        "final_image_prompt",
        extra={
            "event": "final_image_prompt",
            "prompt": final,
            "prompt_length": len(final),
        },
    )

    return final


def build_negative_prompt(
    user_negative: str | None = None, taste: UserTasteProfile | None = None
) -> str:
    parts = [
        "blurry, low quality, oversaturated, watermark, text, logo, "
        "extra fingers, deformed, jpeg artifacts, lowres"
    ]
    if user_negative:
        parts.append(user_negative)
    if taste and taste.disliked_styles:
        parts.append(", ".join(taste.disliked_styles))
    return ", ".join(parts)


def resolve_size(aspect: str | None, output_size: str | None) -> tuple[int, int]:
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
