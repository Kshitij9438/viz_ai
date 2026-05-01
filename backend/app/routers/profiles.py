from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import IdentityContext, get_current_or_guest_user
from app.core.db import get_session
from app.memory.memory import get_or_create_taste
from app.models.models import BusinessProfile, User, UserTasteProfile

router = APIRouter(prefix="/api/v1/users", tags=["profiles"])


class TasteUpdate(BaseModel):
    taste_summary: Optional[str] = None
    preferred_styles: Optional[list[str]] = None
    preferred_colors: Optional[list[str]] = None
    disliked_styles: Optional[list[str]] = None


@router.get("/{user_id}/taste-profile")
async def get_taste(
    user_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    if user_id != identity.user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    t = await get_or_create_taste(db, user_id)
    return {
        "user_id": user_id,
        "taste_summary": t.taste_summary,
        "preferred_styles": t.preferred_styles,
        "preferred_colors": t.preferred_colors,
        "disliked_styles": t.disliked_styles,
        "generation_count": t.generation_count,
        "last_updated": t.last_updated.isoformat() if t.last_updated else None,
    }


@router.put("/{user_id}/taste-profile")
async def put_taste(
    user_id: str,
    body: TasteUpdate,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    if user_id != identity.user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    t = await get_or_create_taste(db, user_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(t, k, v)
    await db.commit()
    return {"ok": True}


class BusinessUpsert(BaseModel):
    business_name: str
    business_type: Optional[str] = None
    sub_type: Optional[str] = None
    location: Optional[str] = None
    brand_tone: Optional[str] = None
    brand_colors: Optional[dict] = None
    logo_url: Optional[str] = None
    font_preference: Optional[str] = None
    goals: Optional[list[str]] = None
    disallowed_themes: Optional[list[str]] = None


@router.get("/{user_id}/business-profile")
async def get_business_p(
    user_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    if user_id != identity.user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    res = await db.execute(select(BusinessProfile).where(BusinessProfile.user_id == user_id))
    b = res.scalar_one_or_none()
    if not b:
        raise HTTPException(404, "no business profile")
    return {c.name: getattr(b, c.name) for c in BusinessProfile.__table__.columns}


@router.put("/{user_id}/business-profile")
async def upsert_business(
    user_id: str,
    body: BusinessUpsert,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    if user_id != identity.user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    res = await db.execute(select(BusinessProfile).where(BusinessProfile.user_id == user_id))
    b = res.scalar_one_or_none()
    data = body.model_dump(exclude_none=True)
    if b is None:
        b = BusinessProfile(user_id=user_id, **data)
        db.add(b)
    else:
        for k, v in data.items():
            setattr(b, k, v)
    await db.commit()
    return {"ok": True, "id": b.id}
