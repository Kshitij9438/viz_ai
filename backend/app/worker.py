from __future__ import annotations

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.core.config import settings
from app.core.queue import dead_letter_job, redis_settings_from_url
from app.memory.memory import maybe_compress_history, update_taste_after_feedback
from app.models.models import Asset, Session as SessionModel


async def compress_history(ctx, session_id: str) -> None:
    try:
        async with AsyncSessionLocal() as db:
            session = (
                await db.execute(select(SessionModel).where(SessionModel.id == session_id))
            ).scalar_one_or_none()
            if session:
                await maybe_compress_history(db, session)
    except Exception as exc:
        await dead_letter_job("compress_history", (session_id,), str(exc))
        raise


async def update_taste_profile(
    ctx,
    user_id: str,
    prompt_summary: str,
    chosen_variant: int | None = None,
    feedback: str | None = None,
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            await update_taste_after_feedback(
                db,
                user_id,
                prompt_summary=prompt_summary,
                chosen_variant=chosen_variant,
                feedback=feedback,
            )
    except Exception as exc:
        await dead_letter_job("update_taste_profile", (user_id, prompt_summary, chosen_variant, feedback), str(exc))
        raise


async def update_taste_profile_for_bundle(
    ctx,
    user_id: str,
    bundle_id: str,
    chosen_variant: int | None = None,
    feedback: str | None = None,
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            asset = (
                await db.execute(select(Asset).where(Asset.bundle_id == bundle_id).limit(1))
            ).scalar_one_or_none()
            prompt_summary = asset.prompt[:240] if asset else bundle_id
            await update_taste_after_feedback(
                db,
                user_id,
                prompt_summary=prompt_summary,
                chosen_variant=chosen_variant,
                feedback=feedback,
            )
    except Exception as exc:
        await dead_letter_job("update_taste_profile_for_bundle", (user_id, bundle_id, chosen_variant, feedback), str(exc))
        raise


class WorkerSettings:
    functions = [compress_history, update_taste_profile, update_taste_profile_for_bundle]
    redis_settings = redis_settings_from_url()
    max_tries = settings.QUEUE_MAX_TRIES
    job_timeout = settings.QUEUE_JOB_TIMEOUT_SECONDS
