from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import IdentityContext, get_current_or_guest_user
from app.core.db import get_session
from app.core.config import settings

from app.models.models import Asset

router = APIRouter(prefix="/api/v1", tags=["assets"])


# =========================
# 🔥 URL RESOLVER (FINAL FIX)
# =========================
def resolve_asset_url(path: str) -> str:
    if not path:
        return ""

    # ✅ Already correct (Supabase or external)
    if path.startswith("http"):
        return path

    # 🔥 Normalize ALL legacy formats
    path = path.strip()

    # remove any local prefixes
    if path.startswith("/storage/"):
        path = path[len("/storage/"):]
    elif path.startswith("storage/"):
        path = path[len("storage/"):]

    # 🔥 ensure correct folder structure
    if not path.startswith("generated/"):
        filename = path.split("/")[-1]
        path = f"generated/{filename}"

    # =========================
    # 🚀 SUPABASE MODE
    # =========================
    if settings.SUPABASE_URL and settings.SUPABASE_BUCKET:
        return (
            f"{settings.SUPABASE_URL}/storage/v1/object/public/"
            f"{settings.SUPABASE_BUCKET}/{path}"
        )

    # =========================
    # 🧪 LOCAL DEV FALLBACK
    # =========================
    return f"http://localhost:8000/storage/{path}"


# =========================
# 🔄 SERIALIZER
# =========================
def _serialize(a: Asset) -> dict:
    return {
        "id": a.id,
        "url": resolve_asset_url(a.url),  # ✅ CRITICAL FIX
        "type": a.type,
        "prompt": a.prompt,
        "selected": a.selected,
        "saved_permanently": a.saved_permanently,
        "variant_index": a.variant_index,
        "bundle_id": a.bundle_id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# =========================
# 📦 GET SINGLE ASSET
# =========================
@router.get("/assets/{asset_id}")
async def get_asset(
    asset_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    current_user = identity.user

    a = (
        await db.execute(select(Asset).where(Asset.id == asset_id))
    ).scalar_one_or_none()

    if not a:
        raise HTTPException(404)

    if a.user_id != current_user.id:
        raise HTTPException(403)

    return _serialize(a)


# =========================
# 💾 SAVE ASSET
# =========================
@router.post("/assets/{asset_id}/save")
async def save_asset(
    asset_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    current_user = identity.user

    a = (
        await db.execute(select(Asset).where(Asset.id == asset_id))
    ).scalar_one_or_none()

    if not a:
        raise HTTPException(404)

    if a.user_id != current_user.id:
        raise HTTPException(403)

    a.saved_permanently = True
    await db.commit()

    return {"ok": True}


# =========================
# 🖼️ LIST USER ASSETS
# =========================
@router.get("/users/{user_id}/assets")
async def list_user_assets(
    user_id: str,
    saved: bool = False,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    current_user = identity.user

    if user_id != current_user.id:
        raise HTTPException(403)

    q = (
        select(Asset)
        .where(Asset.user_id == user_id)
        .order_by(Asset.created_at.desc())
    )

    if saved:
        q = q.where(Asset.saved_permanently == True)  # noqa

    rows = (await db.execute(q)).scalars().all()

    return [_serialize(a) for a in rows]