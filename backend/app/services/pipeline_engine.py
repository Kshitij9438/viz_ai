from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import BusinessProfile, Message, UserTasteProfile
from app.pipelines.router import run_generation
from app.services.generate_tool import GenerateParams
from app.services.intent_engine import IntentResult
from app.services.ollama_client import ollama


@dataclass
class PipelineContext:
    db: AsyncSession
    user_id: str
    session_id: str
    message: str
    attachments: list[dict] | None
    recent_messages: list[Message]
    taste: UserTasteProfile | None
    business: BusinessProfile | None
    session_last_prompt: str | None = None
    refinement_mode: bool = False
    awaiting_confirmation: bool = False
    design_context: dict[str, Any] | None = None
    #: When True, ``execute_pipeline`` always runs ``ChatPipeline`` (no generation routers).
    force_chat_pipeline: bool = False


@dataclass
class PipelineResult:
    reply: str
    primary_bundle: dict[str, Any] | None = None
    creative_output: dict[str, Any] | None = None
    tool_call: dict[str, Any] | None = None
    memory_signal: str | None = None
    bundle_ids: list[str] = field(default_factory=list)


class BasePipeline:
    name = "base"
    output_type = "image"

    async def run(self, ctx: PipelineContext, intent: IntentResult) -> PipelineResult:
        raise NotImplementedError

    def _reference_url(self, ctx: PipelineContext) -> str | None:
        if not ctx.attachments:
            return None
        for attachment in ctx.attachments:
            if attachment.get("type") == "image" and attachment.get("url"):
                return attachment["url"]
        return ctx.attachments[0].get("url")

    def _style_tags(self, ctx: PipelineContext, intent: IntentResult) -> list[str]:
        tags = intent.parameters.get("style_tags") or []
        if ctx.taste and ctx.taste.preferred_styles:
            tags = [*tags, *ctx.taste.preferred_styles[:4]]
        return list(dict.fromkeys(str(tag) for tag in tags if tag))

    def _aspect_ratio(self, intent: IntentResult) -> str:
        ratio = intent.parameters.get("aspect_ratio")
        return ratio if ratio in {"square", "landscape", "portrait"} else "square"

    def _personalized_prompt(self, ctx: PipelineContext, prompt: str) -> str:
        """Core user/refinement intent only — aesthetics come from ``build_image_prompt``."""
        cleaned = prompt.strip()
        cleaned = re.sub(r"^style:\s*", "", cleaned, flags=re.IGNORECASE).strip()
        return cleaned


class ImagePipeline(BasePipeline):
    name = "image_pipeline"

    async def run(self, ctx: PipelineContext, intent: IntentResult) -> PipelineResult:
        prompt = self._personalized_prompt(ctx, ctx.message)
        raw_count = int(intent.parameters.get("count") or 3)
        count = max(raw_count, 3)
        params = GenerateParams(
            output_type="image",
            prompt=prompt,
            count=count,
            style_tags=self._style_tags(ctx, intent),
            aspect_ratio=self._aspect_ratio(intent),
        )
        bundle = await run_generation(
            ctx.db,
            params=params,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            taste=ctx.taste,
            business=ctx.business,
        )
        return _visual_result("image", "I made a visual direction for you.", bundle, intent)


class ImageEditPipeline(BasePipeline):
    name = "image_edit_pipeline"

    async def run(self, ctx: PipelineContext, intent: IntentResult) -> PipelineResult:
        reference = self._reference_url(ctx)
        previous = ctx.session_last_prompt or _last_visual_prompt(ctx.recent_messages)
        prompt = ctx.message
        if re.search(r"\b(refine|last output|last image)\b", ctx.message.lower()) and previous:
            prompt = f"{previous}. Refinement request: {ctx.message}"
        prompt = self._personalized_prompt(ctx, prompt)

        params = GenerateParams(
            output_type="style_transfer" if reference else "image",
            prompt=prompt,
            count=int(intent.parameters.get("count") or 2),
            reference_image_url=reference,
            reference_strength=0.65 if reference else None,
            style_tags=self._style_tags(ctx, intent),
            aspect_ratio=self._aspect_ratio(intent),
        )
        bundle = await run_generation(
            ctx.db,
            params=params,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            taste=ctx.taste,
            business=ctx.business,
        )
        return _visual_result("image", "I refined the visual direction.", bundle, intent)


class StoryPipeline(BasePipeline):
    name = "story_pipeline"

    async def run(self, ctx: PipelineContext, intent: IntentResult) -> PipelineResult:
        story = await _complete_json(
            f"""
Write a compact visual story concept for this request:
{ctx.message}

Return JSON:
title: string
logline: string
scenes: array of 3-6 objects with title, description, visual_prompt
""",
            fallback={
                "title": "Visual Story",
                "logline": ctx.message,
                "scenes": [
                    {"title": "Opening", "description": ctx.message, "visual_prompt": ctx.message},
                    {"title": "Development", "description": ctx.message, "visual_prompt": ctx.message},
                    {"title": "Finale", "description": ctx.message, "visual_prompt": ctx.message},
                ],
            },
        )
        scenes = story.get("scenes") or []
        sequence_prompt = self._personalized_prompt(
            ctx,
            "Create a consistent scene-by-scene visual sequence. "
            + " ".join(f"Scene {i + 1}: {s.get('visual_prompt') or s.get('description')}" for i, s in enumerate(scenes)),
        )
        params = GenerateParams(
            output_type="story_sequence",
            prompt=sequence_prompt,
            sequence_count=max(3, min(len(scenes) or 3, 6)),
            style_tags=self._style_tags(ctx, intent),
            aspect_ratio="landscape",
        )
        bundle = await run_generation(
            ctx.db,
            params=params,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            taste=ctx.taste,
            business=ctx.business,
        )
        output = _creative_output("story", intent, bundle)
        output["outputs"].insert(0, {"kind": "story", "title": story.get("title"), "logline": story.get("logline"), "scenes": scenes})
        return PipelineResult(
            reply=f"{story.get('title', 'Story concept')} is ready as a scene sequence.",
            primary_bundle=bundle,
            creative_output=output,
            tool_call={"name": "pipeline", "arguments": intent.model_dump()},
            memory_signal=story.get("logline") or ctx.message,
            bundle_ids=[bundle["bundle_id"]],
        )


class MoodboardPipeline(BasePipeline):
    name = "moodboard_pipeline"

    async def run(self, ctx: PipelineContext, intent: IntentResult) -> PipelineResult:
        prompt = self._personalized_prompt(ctx, f"Moodboard and visual identity board for: {ctx.message}")
        params = GenerateParams(
            output_type="vision_board",
            prompt=prompt,
            count=6,
            style_tags=self._style_tags(ctx, intent),
            aspect_ratio="square",
        )
        bundle = await run_generation(
            ctx.db,
            params=params,
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            taste=ctx.taste,
            business=ctx.business,
        )
        return _visual_result("moodboard", "I built a moodboard direction.", bundle, intent)


class CampaignPipeline(BasePipeline):
    name = "campaign_pipeline"

    async def run(self, ctx: PipelineContext, intent: IntentResult) -> PipelineResult:
        campaign = await _complete_json(
            f"""
Create a practical campaign brief for:
{ctx.message}

Business profile:
{_business_text(ctx.business)}

Return JSON:
campaign_name: string
positioning: string
headlines: array of 3 strings
captions: array of 3 strings
visual_direction: string
poster_text: string
""",
            fallback={
                "campaign_name": "Campaign Concept",
                "positioning": ctx.message,
                "headlines": [ctx.message],
                "captions": [ctx.message],
                "visual_direction": ctx.message,
                "poster_text": ctx.business.business_name if ctx.business else "New Campaign",
            },
        )
        prompt = self._personalized_prompt(ctx, campaign.get("visual_direction") or ctx.message)
        poster = await run_generation(
            ctx.db,
            params=GenerateParams(
                output_type="poster",
                prompt=prompt,
                poster_text=campaign.get("poster_text"),
                poster_layout="hero_text_bottom",
                style_tags=self._style_tags(ctx, intent),
                aspect_ratio="portrait",
            ),
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            taste=ctx.taste,
            business=ctx.business,
        )
        board = await run_generation(
            ctx.db,
            params=GenerateParams(
                output_type="vision_board",
                prompt=f"Campaign visual system: {prompt}",
                count=4,
                style_tags=self._style_tags(ctx, intent),
            ),
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            taste=ctx.taste,
            business=ctx.business,
        )
        output = _creative_output("campaign", intent, poster)
        output["outputs"].insert(0, {"kind": "campaign_brief", **campaign})
        output["outputs"].append({"kind": "asset_bundle", "bundle": board})
        return PipelineResult(
            reply=f"{campaign.get('campaign_name', 'Campaign')} is ready with copy and visuals.",
            primary_bundle=poster,
            creative_output=output,
            tool_call={"name": "pipeline", "arguments": intent.model_dump()},
            memory_signal=campaign.get("positioning") or ctx.message,
            bundle_ids=[poster["bundle_id"], board["bundle_id"]],
        )


class VideoPipeline(BasePipeline):
    name = "video_pipeline"

    async def run(self, ctx: PipelineContext, intent: IntentResult) -> PipelineResult:
        prompt = self._personalized_prompt(ctx, f"Keyframe for a short animated loop: {ctx.message}")
        bundle = await run_generation(
            ctx.db,
            params=GenerateParams(
                output_type="video_loop",
                prompt=prompt,
                style_tags=self._style_tags(ctx, intent),
                aspect_ratio="landscape",
            ),
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            taste=ctx.taste,
            business=ctx.business,
        )
        return _visual_result("video", "I made a video-loop key visual placeholder.", bundle, intent)


_CHAT_GATE_SYSTEM = (
    "You are a conversational assistant helping refine a user's idea.\n"
    "- Ask ONE focused question.\n"
    "- Do NOT generate images.\n"
    "- Do NOT assume completion.\n"
    "- Keep responses short and natural.\n"
    "- Never use phrases like: \"Here is your design\", \"I created this\", "
    "\"Generated image\", or claim you produced visuals.\n\n"
)


class ChatPipeline(BasePipeline):
    name = "chat_pipeline"

    async def run(self, ctx: PipelineContext, intent: IntentResult) -> PipelineResult:
        from app.services.design_context import format_confirmation, is_ready, readiness_state

        if ctx.force_chat_pipeline:
            if ctx.awaiting_confirmation:
                dc = ctx.design_context or {}
                if is_ready(dc):
                    body = format_confirmation(dc)
                else:
                    body = await ollama.complete(
                        _CHAT_GATE_SYSTEM
                        + "The brief is not complete. Ask ONE short question about subject, "
                        "style, colors, or mood.\n\n"
                        f"User: {ctx.message}"
                    )
            elif ctx.refinement_mode:
                hint = ""
                if ctx.design_context and any(
                    ctx.design_context.get(k) for k in ("subject", "style", "colors", "mood")
                ):
                    hint = f"\nDesign cues so far: {ctx.design_context}\n"
                body = await ollama.complete(
                    _CHAT_GATE_SYSTEM
                    + "The user is refining direction only. Ask ONE clarifying question. "
                    "Do not imply you are generating or showing images.\n"
                    f"{hint}\nUser: {ctx.message}"
                )
            else:
                body = await ollama.complete(
                    _CHAT_GATE_SYSTEM + f"User: {ctx.message}"
                )
            return PipelineResult(
                reply=body or "What would you like to focus on next?",
                creative_output={"type": "chat", "outputs": [], "metadata": {"intent": intent.model_dump()}},
                tool_call={"name": "pipeline", "arguments": intent.model_dump()},
            )

        if ctx.awaiting_confirmation:
            dc = ctx.design_context or {}
            if is_ready(dc):
                response = format_confirmation(dc)
            else:
                response = await ollama.complete(
                    "Reply as Vizzy. The user's direction is still a bit open. "
                    "Ask ONE short, concrete question about what they want made (subject, product, or scene) "
                    "or the deliverable type (logo, poster, social graphic, etc.). "
                    "Do not say you are generating images yet.\n\n"
                    f"User: {ctx.message}"
                )
        elif ctx.refinement_mode:
            hint = ""
            if ctx.design_context and any(ctx.design_context.get(k) for k in ("subject", "style", "colors", "mood")):
                hint = f"\nDesign cues so far: {ctx.design_context}\n"
            rs = readiness_state(ctx.design_context or {})
            subj = (ctx.design_context or {}).get("subject") if ctx.design_context else None
            focus = subj.strip() if isinstance(subj, str) and subj.strip() else None
            if not rs.get("has_subject"):
                gap = (
                    "Ask what they want made or shown (subject, product, scene). "
                    "Keep it to ONE question."
                )
            elif not rs.get("has_visual_cue"):
                fk = focus or "this"
                gap = (
                    f"When asking about palette or mood, prefer wording like "
                    f"'For {fk}, any color preference?' or similar if it fits. "
                    "Keep ONE question about style, colors, or mood."
                )
            else:
                gap = (
                    "Ask ONE precise question to tighten direction before generating "
                    "(format, audience, or one visual detail)."
                )
            response = await ollama.complete(
                "Reply as Vizzy. The user is steering creative direction but has not asked you to "
                "generate images yet. "
                + gap
                + " Do not say you are generating, rendering, or creating images right now."
                f"{hint}\nUser: {ctx.message}"
            )
        else:
            response = await ollama.complete(
                "Reply as Vizzy, a concise creative co-pilot. Ask at most one useful question.\n\n"
                f"User: {ctx.message}"
            )
        return PipelineResult(
            reply=response or "Tell me what you want to create, and I can shape it into a visual direction.",
            creative_output={"type": "chat", "outputs": [], "metadata": {"intent": intent.model_dump()}},
            tool_call={"name": "pipeline", "arguments": intent.model_dump()},
        )


PIPELINE_REGISTRY: dict[str, BasePipeline] = {
    pipeline.name: pipeline
    for pipeline in [
        ImagePipeline(),
        ImageEditPipeline(),
        StoryPipeline(),
        MoodboardPipeline(),
        CampaignPipeline(),
        VideoPipeline(),
        ChatPipeline(),
    ]
}


async def execute_pipeline(ctx: PipelineContext, intent: IntentResult) -> PipelineResult:
    if ctx.force_chat_pipeline:
        chat = PIPELINE_REGISTRY["chat_pipeline"]
        return await chat.run(ctx, intent)
    pipeline = PIPELINE_REGISTRY.get(intent.pipeline) or PIPELINE_REGISTRY["image_pipeline"]
    return await pipeline.run(ctx, intent)


def _visual_result(kind: str, reply: str, bundle: dict[str, Any], intent: IntentResult) -> PipelineResult:
    return PipelineResult(
        reply=reply,
        primary_bundle=bundle,
        creative_output=_creative_output(kind, intent, bundle),
        tool_call={"name": "pipeline", "arguments": intent.model_dump()},
        memory_signal=bundle.get("prompt_used"),
        bundle_ids=[bundle["bundle_id"]],
    )


def _creative_output(kind: str, intent: IntentResult, bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": kind,
        "outputs": [{"kind": "asset_bundle", "bundle": bundle}],
        "metadata": {
            "intent": intent.model_dump(),
            "pipeline": intent.pipeline,
            "steps": intent.steps,
        },
        "actions": ["refine", "regenerate", "download_all", "save", "share"],
    }


def _last_visual_prompt(messages: list[Message]) -> str | None:
    for message in reversed(messages):
        if message.role == "assistant" and message.asset_bundle_id:
            return message.content
    return None


def _business_text(business: BusinessProfile | None) -> str:
    if not business:
        return "No business profile."
    return json.dumps(
        {
            "name": business.business_name,
            "type": business.business_type,
            "tone": business.brand_tone,
            "colors": business.brand_colors,
        }
    )


async def _complete_json(prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = await ollama.complete(prompt)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw, flags=re.S)
            if match:
                return json.loads(match.group(0))
    except Exception:
        pass
    return fallback
