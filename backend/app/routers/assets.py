from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import IdentityContext, get_current_or_guest_user
from app.core.db import get_session
from app.models.models import Asset
from app.services.storage import public_asset_url

router = APIRouter(prefix="/api/v1", tags=["assets"])


def serialize_asset(asset: Asset) -> dict:
    return {
        "id": asset.id,
        "session_id": asset.session_id,
        "url": public_asset_url(asset.url),
        "type": asset.type,
        "prompt": asset.prompt,
        "selected": asset.selected,
        "saved_permanently": asset.saved_permanently,
        "variant_index": asset.variant_index,
        "index": asset.variant_index,
        "bundle_id": asset.bundle_id,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
    }


def serialize_bundle(bundle_id: str, assets: list[Asset]) -> dict | None:
    ordered = sorted(assets, key=lambda item: item.variant_index or 0)
    if not ordered:
        return None

    first = ordered[0]
    return {
        "bundle_id": bundle_id,
        "type": first.type if len(ordered) == 1 else "image_grid",
        "assets": [
            {
                "id": asset.id,
                "url": public_asset_url(asset.url),
                "index": asset.variant_index,
                "type": asset.type,
            }
            for asset in ordered
        ],
        "prompt_used": first.prompt,
        "actions": ["select", "download_all", "refine", "send_to_frame", "share", "save"],
    }


@router.get("/assets/{asset_id}")
async def get_asset(
    asset_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    current_user = identity.user
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return serialize_asset(asset)


@router.post("/assets/{asset_id}/save")
async def save_asset(
    asset_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    current_user = identity.user
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    if asset.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    asset.saved_permanently = True
    await db.commit()
    return {"ok": True}


@router.get("/users/{user_id}/assets")
async def list_user_assets(
    user_id: str,
    saved: bool = False,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    current_user = identity.user
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    query = select(Asset).where(Asset.user_id == user_id).order_by(Asset.created_at.desc())
    if saved:
        query = query.where(Asset.saved_permanently == True)  # noqa: E712

    rows = (await db.execute(query)).scalars().all()
    return [serialize_asset(asset) for asset in rows]
