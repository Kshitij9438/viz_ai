"""Memory systems — Layer 7.

Implements:
  * Session memory (last N turns + compressed older turns)
  * Cross-session memory (per-session summaries)
  * Taste profile background updater
  * History compression
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    BusinessProfile,
    Message,
    Session as SessionModel,
    SessionSummary,
    UserTasteProfile,
)
from app.services.ollama_client import ollama

RECENT_TURNS = 15
COMPRESS_AFTER = 30  # turns before older history is folded into a summary


async def get_or_create_taste(db: AsyncSession, user_id: str) -> UserTasteProfile:
    res = await db.execute(select(UserTasteProfile).where(UserTasteProfile.user_id == user_id))
    t = res.scalar_one_or_none()
    if t is None:
        t = UserTasteProfile(user_id=user_id, taste_summary="", preferred_styles=[],
                             preferred_colors=[], disliked_styles=[], past_selections=[])
        db.add(t); await db.commit(); await db.refresh(t)
    return t


async def get_business(db: AsyncSession, user_id: str) -> BusinessProfile | None:
    res = await db.execute(select(BusinessProfile).where(BusinessProfile.user_id == user_id))
    return res.scalar_one_or_none()


async def get_recent_messages(db: AsyncSession, session_id: str, limit: int = RECENT_TURNS) -> list[Message]:
    res = await db.execute(
        select(Message).where(Message.session_id == session_id)
        .order_by(desc(Message.sequence)).limit(limit)
    )
    return list(reversed(res.scalars().all()))


async def get_recent_session_summaries(db: AsyncSession, user_id: str, limit: int = 5) -> list[str]:
    res = await db.execute(
        select(SessionSummary).where(SessionSummary.user_id == user_id)
        .order_by(desc(SessionSummary.created_at)).limit(limit)
    )
    return [s.summary for s in res.scalars().all()]


async def maybe_compress_history(db: AsyncSession, session: SessionModel) -> None:
    """When in-session messages exceed COMPRESS_AFTER, fold older ones into a paragraph summary."""
    res = await db.execute(
        select(Message).where(Message.session_id == session.id).order_by(Message.created_at)
    )
    msgs = res.scalars().all()
    if len(msgs) <= COMPRESS_AFTER:
        return
    older = msgs[: len(msgs) - RECENT_TURNS]
    transcript = "\n".join(f"{m.role}: {m.content[:300]}" for m in older)
    prompt = (
        "Compress the following conversation excerpt into one short paragraph that "
        "preserves creative direction, decisions made, assets referenced, and user preferences:\n\n"
        + transcript
    )
    try:
        summary = await ollama.complete(prompt)
    except Exception:  # noqa: BLE001
        summary = (session.compressed_history or "") + "\n[older turns]"
    session.compressed_history = summary
    await db.commit()


async def update_taste_after_feedback(
    db: AsyncSession,
    user_id: str,
    *,
    prompt_summary: str,
    chosen_variant: int | None,
    feedback: str | None,
) -> None:
    """Background task — silently update the user's taste summary after a generation cycle."""
    taste = await get_or_create_taste(db, user_id)
    base = (
        "Given the following interaction, update the user's taste summary in 2 sentences. "
        "Keep it cumulative — don't discard old preferences unless contradicted.\n\n"
        f"Current taste summary: {taste.taste_summary or '(empty)'}\n"
        f"What was generated: {prompt_summary}\n"
        f"What the user selected: variant {chosen_variant if chosen_variant else 'none'}\n"
        f"What they said after: {feedback or '(no further feedback)'}\n\n"
        "Updated summary:"
    )
    try:
        new_summary = await ollama.complete(base)
    except Exception:  # noqa: BLE001
        new_summary = taste.taste_summary
    taste.taste_summary = new_summary.strip()[:1000]
    taste.generation_count = (taste.generation_count or 0) + 1
    taste.past_selections = (taste.past_selections or []) + [{
        "prompt_summary": prompt_summary[:280],
        "chose_variant": chosen_variant,
        "feedback": feedback,
    }]
    taste.last_updated = datetime.now(timezone.utc)
    await db.commit()


async def end_session_summary(db: AsyncSession, session: SessionModel) -> None:
    res = await db.execute(
        select(Message).where(Message.session_id == session.id).order_by(Message.created_at)
    )
    msgs = res.scalars().all()
    transcript = "\n".join(f"{m.role}: {m.content[:200]}" for m in msgs)
    try:
        summary = await ollama.complete(
            "Summarize this creative session in 2-3 sentences focusing on what the user "
            "made, their style preferences, and the final selected piece:\n\n" + transcript
        )
    except Exception:  # noqa: BLE001
        summary = "Creative session."
    session.summary = summary
    session.ended_at = datetime.now(timezone.utc)
    session.status = "ended"
    db.add(SessionSummary(user_id=session.user_id, session_id=session.id, summary=summary))
    await db.commit()
