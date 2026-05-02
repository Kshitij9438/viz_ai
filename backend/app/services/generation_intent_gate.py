from __future__ import annotations

import re
from typing import Literal

GenerationMode = Literal["generate", "refine", "chat", "confirm"]


def _is_descriptive_generation(text: str) -> bool:
    text = text.lower()
    words = set(re.findall(r"[a-z]+", text))
    keyword_words = {
        "create", "generate", "paint", "draw", "make",
        "scene", "imagine", "visualize", "show",
        "mood", "atmosphere", "poster", "logo", "image", "art", "design",
    }
    phrase_keywords = {"feels like"}
    return len(text) > 20 and (
        bool(words & keyword_words) or any(phrase in text for phrase in phrase_keywords)
    )


def classify_generation_mode(message: str, *, has_attachments: bool = False) -> str:
    text = (message or "").strip().lower()

    if not text:
        return "chat"

    if has_attachments:
        return "generate"

    if any(word in text for word in ["generate", "create", "make", "draw", "paint", "design"]):
        return "generate"

    if _is_descriptive_generation(text):
        return "generate"

    if any(word in text for word in ["yes", "go ahead", "looks good", "perfect"]):
        return "confirm"

    if len(text.split()) <= 3:
        return "chat"

    return "refine"


def classify_intent(message: str, *, has_attachments: bool = False) -> str:
    """Returns generate | refine | chat | confirm."""
    return classify_generation_mode(message, has_attachments=has_attachments)
