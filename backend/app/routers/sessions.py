from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import IdentityContext, get_current_or_guest_user
from app.core.db import get_session
from app.core.config import settings

from app.models.models import (
    Session as SessionModel,
    Message,
    Asset,
)

router = APIRouter(prefix="/api/v1", tags=["sessions"])


# =========================
# 🔧 HELPER: FIX ASSET URL
# =========================
def resolve_asset_url(path: str) -> str:
    if not path:
        return ""

    # already full URL → return as-is
    if path.startswith("http"):
        return path

    # convert local path → Supabase public URL
    if settings.SUPABASE_URL and settings.SUPABASE_BUCKET:
        return (
            f"{settings.SUPABASE_URL}/storage/v1/object/public/"
            f"{settings.SUPABASE_BUCKET}/{path}"
        )

    return path


# =========================
# 📚 LIST SESSIONS
# =========================
@router.get("/users/{user_id}/sessions")
async def list_sessions(
    user_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
):
    current_user = identity.user

    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    result = await db.execute(
        select(SessionModel)
        .where(SessionModel.user_id == user_id)
        .order_by(SessionModel.started_at.desc())
    )

    sessions = result.scalars().all()
    output = []

    for s in sessions:
        msg_result = await db.execute(
            select(Message)
            .where(Message.session_id == s.id, Message.role == "user")
            .order_by(Message.sequence.asc())
            .limit(1)
        )
        first_msg = msg_result.scalar_one_or_none()

        preview = (
            first_msg.content[:100]
            if first_msg
            else (s.summary[:100] if s.summary else "New session")
        )

        output.append(
            {
                "session_id": s.id,
                "created_at": s.started_at.isoformat()
                if s.started_at
                else None,
                "preview": preview,
                "status": s.status,
            }
        )

    return output


# =========================
# 💬 GET SESSION MESSAGES (FINAL FIX)
# =========================
@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
):
    current_user = identity.user

    # 🔒 Validate session ownership
    session_result = await db.execute(
        select(SessionModel).where(SessionModel.id == session_id)
    )
    session = session_result.scalar_one_or_none()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    # 📥 Fetch messages
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.sequence.asc())
    )

    messages = result.scalars().all()
    output = []

    for m in messages:
        bundle = None

        # 🔥 Resolve assets
        if m.asset_bundle_id:
            assets_result = await db.execute(
                select(Asset).where(Asset.bundle_id == m.asset_bundle_id)
            )
            assets = assets_result.scalars().all()

            if assets:
                bundle = {
                    "bundle_id": m.asset_bundle_id,
                    "type": assets[0].type if assets else "image",
                    "prompt_used": "",
                    "actions": [],
                    "assets": [
                        {
                            "id": a.id,
                            "url": resolve_asset_url(a.url),  # ✅ FIXED
                            "index": a.index,
                            "type": a.type,
                        }
                        for a in assets
                    ],
                }

        output.append(
            {
                "role": m.role,
                "content": m.content,
                "bundle": bundle,
            }
        )

    return output