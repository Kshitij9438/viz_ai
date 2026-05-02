"""Job status polling endpoint.

GET /api/v1/jobs/{job_id} — returns job status and result.
PostgreSQL is the sole source of truth.  No in-memory cache (Railway
containers are stateless; per-instance caches cause inconsistency).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import IdentityContext
from app.core.config import settings
from app.core.db import get_session
from app.core.limiter import limiter
from app.models.models import Job
from app.routers.chat import resolve_user
from app.services.storage import public_asset_url

router = APIRouter(prefix="/api/v1", tags=["jobs"])


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    retry_after: Optional[int] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


def _unwrap_stored_result(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Worker stores a flat dict; tolerate accidental ``{"result": {...}}`` nesting."""
    if not raw or not isinstance(raw, dict):
        return raw
    inner = raw.get("result")
    if (
        isinstance(inner, dict)
        and ("asset_bundle" in inner or "reply" in inner)
        and "asset_bundle" not in raw
        and "reply" not in raw
    ):
        return inner
    return raw


def _normalize_job_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize asset URLs in the stored job result (always under API ``result`` key)."""
    result = _unwrap_stored_result(result)
    if not result:
        return None
    normalized = dict(result)
    bundle = normalized.get("asset_bundle")
    if bundle and isinstance(bundle, dict):
        bundle = dict(bundle)
        bundle["assets"] = [
            {**a, "url": public_asset_url(a.get("url"))}
            for a in bundle.get("assets", [])
            if a.get("url")
        ]
        normalized["asset_bundle"] = bundle
    return normalized


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
@limiter.limit("60/minute")
async def get_job_status(
    request: Request,
    job_id: str,
    identity: IdentityContext = Depends(resolve_user),
    db: AsyncSession = Depends(get_session),
) -> JobStatusResponse:
    """Poll for job result.

    DB is the sole source of truth.  Response includes a ``retry_after``
    hint so the frontend knows when to poll again:

    - pending  → retry_after: 2  (seconds)
    - running  → retry_after: 3
    - done     → retry_after: null
    - failed   → retry_after: null
    """
    job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Auth: only the job creator can view it
    if job.user_id != identity.user.id:
        if not settings.ALLOW_GUEST_CHAT:
            raise HTTPException(status_code=403, detail="Forbidden")

    # Determine retry_after hint
    retry_after = None
    if job.status == "pending":
        retry_after = 2
    elif job.status == "running":
        retry_after = 3

    payload = _normalize_job_result(job.result) if job.status == "done" else None

    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        result=payload,
        error=job.error if job.status == "failed" else None,
        retry_after=retry_after,
        created_at=job.created_at.isoformat() if job.created_at else None,
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )
