#from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import IdentityContext, get_current_or_guest_user
from app.core.db import AsyncSessionLocal, get_session
from app.core.limiter import limiter
from app.core.queue import check_dedup, enqueue_job, set_dedup
from app.memory.memory import (
    end_session_summary,
    maybe_compress_history,
    update_taste_after_feedback,
)
from app.models.models import Job, Session as SessionModel, User
from app.services.storage import public_asset_url
from app.services.conversation import converse
from app.services.intent_engine import classify_intent

router = APIRouter(prefix="/api/v1", tags=["chat"])


class Attachment(BaseModel):
    type: str = Field(min_length=1, max_length=30)
    url: str = Field(min_length=1, max_length=2048)
    caption: Optional[str] = Field(default=None, max_length=500)


class ChatRequest(BaseModel):
    user_id: Optional[str] = None  # deprecated, ignored
    session_id: Optional[str] = None
    message: str = Field(min_length=1, max_length=8000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=8)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message cannot be empty")
        return value


class ChatResponse(BaseModel):
    reply: str
    job_id: Optional[str] = None
    job_status: Optional[str] = None
    asset_bundle: Optional[dict[str, Any]] = None
    creative_output: Optional[dict[str, Any]] = None
    intent: Optional[dict[str, Any]] = None
    tool_call: Optional[dict[str, Any]] = None
    session_id: str
    user_id: str
    guest_token: Optional[str] = None


async def _get_or_create_user(db: AsyncSession, user_id: str | None) -> User:
    # Kept for backward compat — no longer called by chat()
    if user_id:
        u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if u:
            return u
    u = User(account_type="home")
    db.add(u); await db.commit(); await db.refresh(u)
    return u


async def _get_or_create_session(db: AsyncSession, session_id: str | None, user_id: str) -> SessionModel:
    if session_id:
        s = (await db.execute(select(SessionModel).where(SessionModel.id == session_id))).scalar_one_or_none()
        if s:
            if s.user_id != user_id:
                raise HTTPException(status_code=403, detail="Session does not belong to authenticated identity")
            return s
    s = SessionModel(user_id=user_id)
    db.add(s); await db.commit(); await db.refresh(s)
    return s


async def _bg_taste_update(user_id: str, prompt_summary: str, feedback: str | None) -> None:
    async with AsyncSessionLocal() as db:
        await update_taste_after_feedback(
            db, user_id, prompt_summary=prompt_summary, chosen_variant=None, feedback=feedback,
        )


def _normalize_bundle(bundle: dict[str, Any] | None) -> dict[str, Any] | None:
    if not bundle:
        return None
    normalized = dict(bundle)
    normalized["assets"] = [
        {
            **asset,
            "url": public_asset_url(asset.get("url")),
        }
        for asset in bundle.get("assets", [])
        if asset.get("url")
    ]
    return normalized


def _normalize_creative_output(output: dict[str, Any] | None) -> dict[str, Any] | None:
    if not output:
        return None
    normalized = dict(output)
    normalized_outputs = []
    for item in output.get("outputs", []):
        if isinstance(item, dict) and item.get("kind") == "asset_bundle" and item.get("bundle"):
            normalized_outputs.append({**item, "bundle": _normalize_bundle(item["bundle"])})
        else:
            normalized_outputs.append(item)
    normalized["outputs"] = normalized_outputs
    return normalized


# ---------------------------------------------------------------------------
# Intent type mapping
# ---------------------------------------------------------------------------

_GENERATION_INTENTS = {"image", "story", "video", "moodboard", "campaign", "edit"}


# ---------------------------------------------------------------------------
# /chat endpoint — hybrid sync/async
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse)
@limiter.limit("20/minute")
async def chat(
    request: Request,
    req: ChatRequest,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> ChatResponse:
    user = identity.user
    session = await _get_or_create_session(db, req.session_id, user.id)

    # ---- Step 1: Classify intent (1 LLM call, ~1-3s, acceptable) ----
    from app.memory.memory import get_or_create_taste, get_business, get_recent_messages
    taste = await get_or_create_taste(db, session.user_id)
    business = await get_business(db, session.user_id)
    recent_msgs = await get_recent_messages(db, session.id)

    try:
        intent = await classify_intent(
            message=req.message,
            attachments=[a.model_dump() for a in req.attachments] if req.attachments else None,
            recent_messages=recent_msgs,
            taste=taste,
            business=business,
        )
    except Exception:
        # Fallback to regex-based classification (never crash)
        from app.services.intent_engine import _fallback_intent
        intent = _fallback_intent(req.message, [a.model_dump() for a in req.attachments] if req.attachments else None)

    # ---- Step 2: Chat-only (no generation) → sync fast path ----
    if intent.intent not in _GENERATION_INTENTS:
        try:
            result = await converse(
                db, session=session, user_message=req.message,
                attachments=[a.model_dump() for a in req.attachments] if req.attachments else None,
            )
        except Exception:
            result = {
                "reply": "I hit a temporary issue. Your session is safe. Please try again in a moment.",
                "asset_bundle": None,
                "creative_output": None,
                "intent": None,
                "tool_call": None,
            }

        return ChatResponse(
            reply=str(result.get("reply") or "Tell me more."),
            job_id=None,
            job_status=None,
            asset_bundle=_normalize_bundle(result.get("asset_bundle")),
            creative_output=_normalize_creative_output(result.get("creative_output")),
            intent=result.get("intent"),
            tool_call=result.get("tool_call"),
            session_id=session.id,
            user_id=user.id,
            guest_token=identity.guest_token,
        )

    # ---- Step 3: Generation intent → enqueue job, return fast ----

    # Dedup check
    existing_job_id = await check_dedup(user.id, session.id, req.message)
    if existing_job_id:
        import logging
        logging.getLogger("vizzy.chat").info(
            "dedup_hit",
            extra={"event": "dedup_hit", "job_id": existing_job_id, "user_id": user.id},
        )
        return ChatResponse(
            reply=f"I'm already working on that! You can check progress.",
            job_id=existing_job_id,
            job_status="pending",
            session_id=session.id,
            user_id=user.id,
            guest_token=identity.guest_token,
        )

    # Create Job in DB (single source of truth)
    job = Job(
        user_id=user.id,
        session_id=session.id,
        type="generation",
        message=req.message,
        attachments_json=[a.model_dump() for a in req.attachments] if req.attachments else None,
        intent_data=intent.model_dump(),
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # Enqueue to Redis (transport only)
    enqueued = await enqueue_job(job.id)

    if not enqueued:
        # Redis unavailable — return degraded response, do NOT run pipeline sync
        return ChatResponse(
            reply="I understood your request, but image generation is temporarily unavailable. Please try again in a moment.",
            job_id=None,
            job_status=None,
            session_id=session.id,
            user_id=user.id,
            guest_token=identity.guest_token,
            intent=intent.model_dump(),
        )

    # Set dedup marker
    await set_dedup(user.id, session.id, req.message, job.id)

    # Reply type mapping for friendly messages
    _type_names = {
        "image": "your images",
        "story": "your story",
        "video": "your video",
        "moodboard": "your moodboard",
        "campaign": "your campaign",
        "edit": "the edit",
    }
    type_name = _type_names.get(intent.intent, "your creation")

    return ChatResponse(
        reply=f"I'm working on {type_name}. This usually takes 10–30 seconds.",
        job_id=job.id,
        job_status="pending",
        session_id=session.id,
        user_id=user.id,
        guest_token=identity.guest_token,
        intent=intent.model_dump(),
    )


# ---------------------------------------------------------------------------
# /feedback
# ---------------------------------------------------------------------------

async def _bg_compress(session_id: str) -> None:
    async with AsyncSessionLocal() as db:
        s = (await db.execute(select(SessionModel).where(SessionModel.id == session_id))).scalar_one_or_none()
        if s:
            await maybe_compress_history(db, s)


class FeedbackRequest(BaseModel):
    user_id: Optional[str] = None  # deprecated, ignored
    session_id: str
    bundle_id: str
    chosen_variant: Optional[int] = None
    feedback: Optional[str] = None


@router.post("/feedback")
async def feedback(
    req: FeedbackRequest,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    from app.models.models import Asset

    current_user = identity.user
    s = (await db.execute(select(SessionModel).where(SessionModel.id == req.session_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    bundle_asset = (await db.execute(select(Asset).where(Asset.bundle_id == req.bundle_id).limit(1))).scalar_one_or_none()
    if bundle_asset and bundle_asset.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    if req.chosen_variant:
        res = await db.execute(
            select(Asset).where(Asset.bundle_id == req.bundle_id, Asset.variant_index == req.chosen_variant)
        )
        a = res.scalar_one_or_none()
        if a:
            if a.user_id != current_user.id:
                raise HTTPException(status_code=403, detail="Forbidden")
            a.selected = True
            s.active_asset_id = a.id
            await db.commit()

    # Taste update — best-effort in-process (non-blocking background)
    try:
        await _bg_taste_update_with_variant(current_user.id, req.bundle_id, req.chosen_variant, req.feedback)
    except Exception:
        pass  # taste update is non-critical

    return {"ok": True}


async def _bg_taste_update_with_variant(user_id: str, bundle_id: str, variant: int | None, feedback: str | None) -> None:
    async with AsyncSessionLocal() as db:
        from app.models.models import Asset
        a = (await db.execute(select(Asset).where(Asset.bundle_id == bundle_id).limit(1))).scalar_one_or_none()
        prompt_summary = (a.prompt[:240] if a else bundle_id)
        await update_taste_after_feedback(
            db, user_id, prompt_summary=prompt_summary,
            chosen_variant=variant, feedback=feedback,
        )


@router.post("/sessions/{session_id}/end")
async def end_session(
    session_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    current_user = identity.user
    s = (await db.execute(select(SessionModel).where(SessionModel.id == session_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if s.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    await end_session_summary(db, s)
    return {"ok": True, "summary": s.summary}


@router.get("/rate-test")
@limiter.limit("3/minute")  # small for testing
async def rate_test(request: Request):
    return {"ok": True}
