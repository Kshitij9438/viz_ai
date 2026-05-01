from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.models.models import BusinessProfile, Message, UserTasteProfile
from app.services.ollama_client import ollama

IntentName = Literal["image", "story", "video", "moodboard", "campaign", "edit", "chat"]


@dataclass
class IntentResult:
    intent: IntentName
    pipeline: str
    steps: list[str]
    confidence: float = 0.7
    execute: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "pipeline": self.pipeline,
            "steps": self.steps,
            "confidence": self.confidence,
            "execute": self.execute,
            "parameters": self.parameters,
        }


PIPELINE_STEPS: dict[str, list[str]] = {
    "image_pipeline": ["interpret prompt", "personalize visual direction", "generate image set"],
    "image_edit_pipeline": ["read reference context", "apply requested transformation", "generate edited image"],
    "story_pipeline": ["write story", "split into scenes", "generate scene visuals", "package sequence"],
    "video_pipeline": ["plan motion concept", "generate key visual", "return video placeholder"],
    "moodboard_pipeline": ["extract aesthetic anchors", "generate visual board", "package palette direction"],
    "campaign_pipeline": ["build campaign strategy", "write copy", "generate campaign visuals", "package deliverables"],
    "chat_pipeline": ["respond conversationally"],
}


def _fallback_intent(message: str, attachments: list[dict] | None) -> IntentResult:
    text = message.lower()
    has_image = bool(attachments)

    if re.search(r"\b(refine|make it|change|darker|lighter|more premium|improve|iterate)\b", text):
        return IntentResult("edit", "image_edit_pipeline", PIPELINE_STEPS["image_edit_pipeline"])
    if has_image and re.search(r"\b(turn|edit|transform|restyle|into|remove|replace)\b", text):
        return IntentResult("edit", "image_edit_pipeline", PIPELINE_STEPS["image_edit_pipeline"])
    if re.search(r"\b(story|scene by scene|comic|sequence|narrative|storyboard)\b", text):
        return IntentResult("story", "story_pipeline", PIPELINE_STEPS["story_pipeline"])
    if re.search(r"\b(campaign|ad campaign|poster|signage|launch|product visual|brand|marketing)\b", text):
        return IntentResult("campaign", "campaign_pipeline", PIPELINE_STEPS["campaign_pipeline"])
    if re.search(r"\b(moodboard|mood board|vision board|aesthetic board|palette)\b", text):
        return IntentResult("moodboard", "moodboard_pipeline", PIPELINE_STEPS["moodboard_pipeline"])
    if re.search(r"\b(video|loop|motion|animate|reel)\b", text):
        return IntentResult("video", "video_pipeline", PIPELINE_STEPS["video_pipeline"])
    if re.search(r"\b(image|visual|generate|create|design|paint|painting|photo|illustration)\b", text):
        return IntentResult("image", "image_pipeline", PIPELINE_STEPS["image_pipeline"])

    return IntentResult("chat", "chat_pipeline", PIPELINE_STEPS["chat_pipeline"], execute=False)


def _json_from_text(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def classify_intent(
    *,
    message: str,
    attachments: list[dict] | None,
    recent_messages: list[Message],
    taste: UserTasteProfile | None,
    business: BusinessProfile | None,
) -> IntentResult:
    fallback = _fallback_intent(message, attachments)
    recent = "\n".join(f"{m.role}: {m.content[:180]}" for m in recent_messages[-6:])
    profile = {
        "taste": taste.taste_summary if taste else "",
        "styles": taste.preferred_styles if taste else [],
        "colors": taste.preferred_colors if taste else [],
        "business": business.business_name if business else "",
        "business_type": business.business_type if business else "",
    }
    prompt = f"""
Classify the user's creative intent for Vizzy, a creative operating system.

Return ONLY JSON with:
intent: one of ["image","story","video","moodboard","campaign","edit","chat"]
pipeline: one of ["image_pipeline","image_edit_pipeline","story_pipeline","video_pipeline","moodboard_pipeline","campaign_pipeline","chat_pipeline"]
steps: concise ordered strings
confidence: number 0-1
execute: boolean, true when the user is asking Vizzy to produce or modify creative output now
parameters: object with useful hints, including aspect_ratio, count, style_tags, poster_text when obvious

User message: {message}
Has attachments: {bool(attachments)}
Recent conversation:
{recent or "(none)"}
Profile:
{json.dumps(profile)}
"""
    try:
        raw = await ollama.complete(prompt)
        data = _json_from_text(raw) or {}
        pipeline = data.get("pipeline") or fallback.pipeline
        intent = data.get("intent") or fallback.intent
        if pipeline not in PIPELINE_STEPS:
            pipeline = fallback.pipeline
        if intent not in {"image", "story", "video", "moodboard", "campaign", "edit", "chat"}:
            intent = fallback.intent
        return IntentResult(
            intent=intent,
            pipeline=pipeline,
            steps=data.get("steps") or PIPELINE_STEPS[pipeline],
            confidence=float(data.get("confidence", fallback.confidence)),
            execute=bool(data.get("execute", fallback.execute)),
            parameters=data.get("parameters") or fallback.parameters,
        )
    except Exception:
        return fallback
