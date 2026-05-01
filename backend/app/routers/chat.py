from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import IdentityContext, get_current_or_guest_user
from app.core.db import AsyncSessionLocal, get_session
from app.memory.memory import (
    end_session_summary,
    maybe_compress_history,
    update_taste_after_feedback,
)
from app.models.models import Session as SessionModel, User
from app.services.conversation import converse
from app.core.limiter import limiter
from fastapi import Request
router = APIRouter(prefix="/api/v1", tags=["chat"])


class Attachment(BaseModel):
    type: str
    url: str
    caption: Optional[str] = None


class ChatRequest(BaseModel):
    user_id: Optional[str] = None  # deprecated, ignored
    session_id: Optional[str] = None
    message: str
    attachments: list[Attachment] = []


class ChatResponse(BaseModel):
    reply: str
    asset_bundle: Optional[dict[str, Any]] = None
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

@router.post("/chat", response_model=ChatResponse)
@limiter.limit("20/minute")
async def chat(
    request: Request,
    req: ChatRequest,
    bg: BackgroundTasks,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> ChatResponse:
    user = identity.user
    session = await _get_or_create_session(db, req.session_id, user.id)

    result = await converse(
        db, session=session, user_message=req.message,
        attachments=[a.model_dump() for a in req.attachments] if req.attachments else None,
    )

    # background: compress history & update taste if a generation just happened
    bg.add_task(_bg_compress, session.id)
    if result.get("asset_bundle"):
        prompt_summary = result["asset_bundle"]["prompt_used"][:280]
        bg.add_task(_bg_taste_update, user.id, prompt_summary, req.message)

    return ChatResponse(
        reply=result["reply"],
        asset_bundle=result.get("asset_bundle"),
        tool_call=result.get("tool_call"),
        session_id=session.id,
        user_id=user.id,
        guest_token=identity.guest_token,
    )


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
    bg: BackgroundTasks,
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

    bg.add_task(
        _bg_taste_update_with_variant,
        current_user.id, req.bundle_id, req.chosen_variant, req.feedback,
    )
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

from fastapi import Request
from app.core.limiter import limiter

@router.get("/rate-test")
@limiter.limit("3/minute")  # small for testing
async def rate_test(request: Request):
    return {"ok": True}