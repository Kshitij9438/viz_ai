"""Structured design cues accumulated per session (subject, style, colors, mood)."""
from __future__ import annotations

import logging
import random
import re
from typing import Any, TypedDict

logger = logging.getLogger("vizzy.design_context")


class DesignContext(TypedDict, total=False):
    subject: str | None
    style: str | None
    colors: str | None
    mood: str | None


def empty_design_context() -> DesignContext:
    return {"subject": None, "style": None, "colors": None, "mood": None}


def _norm(s: str | None, max_len: int = 240) -> str | None:
    if not s:
        return None
    t = " ".join(s.split()).strip()
    return t[:max_len] if t else None


def _merge_incremental(existing: str | None, new_fragment: str | None, max_len: int = 120) -> str | None:
    """Merge style/colors/mood tokens without piling conflicting duplicates."""
    new_fragment = _norm(new_fragment, max_len=max_len)
    if not new_fragment:
        return _norm(existing, max_len=max_len)
    if not existing:
        return new_fragment
    if new_fragment.lower() in existing.lower():
        return existing
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    seen = {p.lower() for p in parts}
    if new_fragment.lower() not in seen:
        parts.append(new_fragment)
    return ", ".join(parts)[:max_len]


_STYLE_WORDS = re.compile(
    r"\b(minimal|minimalistic|minimalism|modern|rustic|vintage|retro|futuristic|"
    r"elegant|playful|bold|subtle|clean|organic|industrial|luxury|whimsical|"
    r"professional|casual|scandinavian|art\s+deco|bauhaus|brutalist)\b",
    re.IGNORECASE,
)

_MOOD_WORDS = re.compile(
    r"\b(cozy|dramatic|serene|energetic|moody|uplifting|melancholic|epic|intimate|"
    r"calm|tense|dreamy|nostalgic|hopeful|dark|lighthearted)\b",
    re.IGNORECASE,
)

_COLOR_WORDS = re.compile(
    r"\b(navy|burgundy|teal|cyan|magenta|lavender|crimson|emerald|gold|silver|bronze|"
    r"black|white|grey|gray|red|blue|green|yellow|orange|purple|pink|brown|beige|"
    r"monochrome|pastel|neon)\b(?:\s+and\s+\w+)?",
    re.IGNORECASE,
)

_SUBJECT_PATTERNS = [
    re.compile(
        r"(?:logo|poster|banner|flyer|thumbnail|mockup|image|picture|photo|illustration|"
        r"graphic|visual|design)\s+(?:for|of|about|showing)\s+([^.,;?\n]{3,120})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:create|make|need|want)\s+(?:a|an|the)?\s*([^.,;?\n]{6,120}?)\s+(?:logo|poster|banner|image)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:about|depicting|featuring)\s+([^.,;?\n]{3,120})",
        re.IGNORECASE,
    ),
]


def _extract_subject_from_patterns(text: str) -> str | None:
    for pat in _SUBJECT_PATTERNS:
        m = pat.search(text)
        if m:
            subj = _norm(m.group(1).strip(" .'\" "))
            if subj and len(subj) >= 3:
                return subj
    return None


def merge_design_context(existing: dict[str, Any] | None, message: str) -> DesignContext:
    """Merge signals from the latest user message. New subject replaces old; style/colors/mood merge."""
    base: dict[str, Any] = {}
    if isinstance(existing, dict):
        for k in ("subject", "style", "colors", "mood"):
            v = existing.get(k)
            if isinstance(v, str) and v.strip():
                base[k] = v.strip()[:240]

    text = (message or "").strip()
    if not text:
        out = empty_design_context()
        for k in ("subject", "style", "colors", "mood"):
            if base.get(k):
                out[k] = base[k]  # type: ignore[literal-required]
        return out  # type: ignore[return-value]

    pattern_subject = _extract_subject_from_patterns(text)
    if pattern_subject:
        base["subject"] = pattern_subject
    elif base.get("subject") is None and len(text) >= 40 and "," in text:
        first = text.split(",")[0].strip()
        if 8 <= len(first) <= 160:
            base["subject"] = first

    sm = _STYLE_WORDS.search(text)
    if sm:
        base["style"] = _merge_incremental(base.get("style"), sm.group(0))

    cm = _COLOR_WORDS.search(text)
    if cm:
        base["colors"] = _merge_incremental(base.get("colors"), cm.group(0))

    mm = _MOOD_WORDS.search(text)
    if mm:
        base["mood"] = _merge_incremental(base.get("mood"), mm.group(0))

    result = empty_design_context()
    result.update(base)
    return result  # type: ignore[return-value]


def readiness_state(context: DesignContext | dict[str, Any] | None) -> dict[str, Any]:
    """Structured readiness for logging and UI hints."""
    if not context:
        return {
            "ready": False,
            "has_subject": False,
            "has_style": False,
            "has_colors": False,
            "has_mood": False,
            "has_visual_cue": False,
        }
    subj = context.get("subject")
    sty = context.get("style")
    col = context.get("colors")
    md = context.get("mood")
    has_subject = isinstance(subj, str) and len(subj.strip()) >= 2
    has_style = isinstance(sty, str) and len(sty.strip()) >= 1
    has_colors = isinstance(col, str) and len(col.strip()) >= 1
    has_mood = isinstance(md, str) and len(md.strip()) >= 1
    has_visual_cue = has_style or has_colors or has_mood
    ready = bool(has_subject and has_visual_cue)
    return {
        "ready": ready,
        "has_subject": has_subject,
        "has_style": has_style,
        "has_colors": has_colors,
        "has_mood": has_mood,
        "has_visual_cue": has_visual_cue,
    }


def is_ready(context: DesignContext | dict[str, Any] | None) -> bool:
    """Ready for confirmation: subject plus at least one of style, colors, or mood."""
    return bool(readiness_state(context).get("ready"))


def soft_escalate_to_confirmation(context: DesignContext | dict[str, Any] | None) -> bool:
    """Rich brief in one shot (subject + style + colors) — skip extra refinement turns."""
    if not context:
        return False
    subj = context.get("subject")
    sty = context.get("style")
    col = context.get("colors")
    return (
        isinstance(subj, str)
        and len(subj.strip()) >= 2
        and isinstance(sty, str)
        and len(sty.strip()) >= 1
        and isinstance(col, str)
        and len(col.strip()) >= 1
    )


_CONFIRMATION_TEMPLATES: tuple[str, ...] = (
    "So you're going for a {style_p} {colors_p} {subject}.\n\nWant me to generate this?",
    "Got it — a {style_p} {subject} with {colors_p}.\n\nShould I create it?",
    "This sounds like a {mood_p} {subject}.\n\nReady for me to generate it?",
    "Here's the direction: {style_p}, {colors_p}, focused on {subject}.\n\nGo ahead and generate?",
)

_CONFIRM_HINT = '\n\nYou can say "generate it", "go ahead", or "yes" when you\'re ready.'


def format_confirmation(context: DesignContext | dict[str, Any]) -> str:
    """Dynamic confirmation copy with random template + short hint."""
    style = (context.get("style") or "").strip()
    colors = (context.get("colors") or "").strip()
    subject = (context.get("subject") or "your idea").strip()
    mood = (context.get("mood") or "").strip()

    style_p = style or "distinctive"
    colors_p = colors or "your chosen palette"
    mood_p = mood or "focused"

    body = random.choice(_CONFIRMATION_TEMPLATES).format(
        style_p=style_p,
        colors_p=colors_p,
        subject=subject,
        mood_p=mood_p,
    )
    body = body + _CONFIRM_HINT

    logger.info(
        "confirmation_triggered",
        extra={
            "event": "confirmation_triggered",
            "subject": subject[:120],
            "has_style": bool(style),
            "has_colors": bool(colors),
            "has_mood": bool(mood),
        },
    )
    return body


def build_resolved_user_message(raw_message: str, context: DesignContext | dict[str, Any]) -> str:
    """Compose a single prompt line for the worker from session cues + latest user text."""
    parts: list[str] = []
    if context.get("subject"):
        parts.append(f"Subject: {context['subject']}")
    if context.get("style"):
        parts.append(f"Style: {context['style']}")
    if context.get("colors"):
        parts.append(f"Colors: {context['colors']}")
    if context.get("mood"):
        parts.append(f"Mood: {context['mood']}")
    if parts:
        return " ".join(parts)
    return raw_message.strip()
