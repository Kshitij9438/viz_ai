"""Gate image/edit jobs: explicit generate vs confirm vs refine vs chat.

Enqueue only on ``generate`` or ``confirm`` + readiness (see chat router).
Attachments default to refine unless user explicitly generates or confirms.
"""
from __future__ import annotations

import re
from typing import Literal

GenerationMode = Literal["generate", "refine", "chat", "confirm"]

# Implicit confirmation (Phase 6)
_PURE_CONFIRM_RE = re.compile(
    r"^\s*("
    r"yes(\s+please)?|yeah|yep|sure|ok|okay|please|go\s+ahead|do\s+it|"
    r"sounds\s+good|that\s+works|looks\s+good|perfect|"
    r"proceed|run\s+it|go\s+for\s+it|"
    r"create\s+it|generate\s+it|make\s+it\s+now"
    r")\s*[!.]*\s*$",
    re.IGNORECASE,
)

_GENERATE_RE = re.compile(
    r"\b("
    r"create|generate|render(\s+me)?|design(\s+me)?\s+a|make(\s+me)?\s+a|"
    r"give\s+me\s+(an?\s+)?(image|picture|photo|illustration|logo|poster|banner|visual)|"
    r"draw|paint|illustrate|show\s+me\s+(an?\s+)?(image|picture|photo)|"
    r"i\s+need\s+(an?\s+)?(image|picture|logo|poster|design)|"
    r"want\s+(an?\s+)?(image|picture|logo|poster|design)|"
    r"looking\s+for\s+(an?\s+)?(logo|poster|design|image)|"
    r"build\s+(me\s+)?(an?\s+)?(image|logo|poster)"
    r")\b",
    re.IGNORECASE,
)

_CHAT_RE = re.compile(
    r"^\s*("
    r"hi\b|hello\b|hey\b|thanks?\b|thank\s+you|thx|"
    r"how\s+(does|do|can|is)|what\s+is|who\s+are|good\s+(morning|afternoon|evening)"
    r")",
    re.IGNORECASE,
)

_REFINE_RE = re.compile(
    r"\b("
    r"more\s+\w+|less\s+\w+|"
    r"minimal|minimalistic|minimalism|modern|cleaner|simpler|"
    r"darker|lighter|softer|bolder|warmer|cooler|brighter|"
    r"refine|tweak|adjust|iterate|"
    r"something\s+(more|less)|"
    r"make\s+it\s+(more|less|the\s+same)|"
    r"change\s+the\s+(style|look|mood|feel|palette|colors?)|"
    r"can\s+we\s+(try|make)|"
    r"i\s+want\s+something\s+(more|less)"
    r")\b",
    re.IGNORECASE,
)


def classify_generation_mode(message: str, *, has_attachments: bool = False) -> GenerationMode:
    """Classify user turn for image/edit gating.

    - generate: explicit “create/render/…” now
    - confirm: short affirmative (yes, looks good, perfect, …)
    - refine: gather direction (includes attachments-only turns unless explicit generate)
    - chat: greetings / meta
    """
    text = (message or "").strip()
    if not text:
        return "chat"

    lower = text.lower()

    # Explicit creation beats everything except pure confirm lines
    if _GENERATE_RE.search(lower):
        return "generate"

    if len(text) <= 72 and _PURE_CONFIRM_RE.match(text):
        return "confirm"

    if has_attachments:
        return "refine"

    if _CHAT_RE.search(lower) and len(text) < 120:
        return "chat"

    if _REFINE_RE.search(lower) and not _GENERATE_RE.search(lower):
        return "refine"

    # Rich descriptions → guided refinement / confirmation, not instant jobs
    if len(text) >= 56 and ("," in text or text.count(" ") >= 8):
        return "refine"

    if len(text) < 100:
        return "refine"

    return "refine"


def classify_intent(message: str, *, has_attachments: bool = False) -> str:
    """Returns generate | refine | chat | confirm."""
    return classify_generation_mode(message, has_attachments=has_attachments)
