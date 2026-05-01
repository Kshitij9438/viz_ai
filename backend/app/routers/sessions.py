from __future__ import annotations

import json
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import IdentityContext, get_current_or_guest_user
from app.core.db import get_session
from app.models.models import Asset, Message, Session
from app.routers.assets import serialize_bundle

router = APIRouter(prefix="/api/v1", tags=["sessions"])


def _session_title(session: Session, messages: list[Message]) -> str:
    if session.summary:
        return session.summary[:60]
    if session.last_prompt:
        return session.last_prompt[:60]
    for message in messages:
        if message.role == "user" and message.content:
            return message.content[:60]
    return "New chat"


def _bundle_from_tool_message(message: Message) -> str | None:
    if message.role != "tool" or not message.content:
        return None
    try:
        payload = json.loads(message.content)
    except json.JSONDecodeError:
        return None
    bundle_id = payload.get("bundle_id")
    return bundle_id if isinstance(bundle_id, str) else None


@router.get("/sessions")
async def list_sessions(
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    current_user = identity.user
    sessions = (
        await db.execute(
            select(Session)
            .where(Session.user_id == current_user.id)
            .order_by(Session.started_at.desc())
        )
    ).scalars().all()

    out: list[dict] = []
    for session in sessions:
        messages = (
            await db.execute(
                select(Message)
                .where(Message.session_id == session.id)
                .order_by(Message.sequence.asc(), Message.created_at.asc())
                .limit(8)
            )
        ).scalars().all()
        out.append(
            {
                "id": session.id,
                "title": _session_title(session, list(messages)),
                "preview": next((m.content[:120] for m in messages if m.role == "user" and m.content), ""),
                "message_count": session.message_count,
                "updated_at": messages[-1].created_at.isoformat() if messages else session.started_at.isoformat(),
                "started_at": session.started_at.isoformat() if session.started_at else None,
                "status": session.status,
            }
        )

    return out


@router.get("/users/{user_id}/sessions")
async def list_user_sessions(
    user_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    if user_id != identity.user.id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return await list_sessions(identity=identity, db=db)


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    identity: IdentityContext = Depends(get_current_or_guest_user),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    current_user = identity.user
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    messages = (
        await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.sequence.asc(), Message.created_at.asc())
        )
    ).scalars().all()

    assets = (
        await db.execute(
            select(Asset)
            .where(Asset.session_id == session_id, Asset.bundle_id.is_not(None))
            .order_by(Asset.variant_index.asc(), Asset.created_at.asc())
        )
    ).scalars().all()

    assets_by_bundle: dict[str, list[Asset]] = defaultdict(list)
    for asset in assets:
        if asset.bundle_id:
            assets_by_bundle[asset.bundle_id].append(asset)

    out: list[dict] = []
    ordered = list(messages)
    for index, message in enumerate(ordered):
        if message.role not in {"user", "assistant"}:
            continue

        bundle_id = message.asset_bundle_id
        if message.role == "assistant" and not bundle_id:
            for next_message in ordered[index + 1 : index + 3]:
                bundle_id = _bundle_from_tool_message(next_message)
                if bundle_id:
                    break

        bundle = serialize_bundle(bundle_id, assets_by_bundle[bundle_id]) if bundle_id else None
        creative_output = None
        if message.tool_calls and isinstance(message.tool_calls, dict):
            creative_output = message.tool_calls.get("creative_output")
            if creative_output:
                normalized_items = []
                for item in creative_output.get("outputs", []):
                    if isinstance(item, dict) and item.get("kind") == "asset_bundle":
                        raw_bundle = item.get("bundle") or {}
                        raw_bundle_id = raw_bundle.get("bundle_id")
                        rebuilt = serialize_bundle(raw_bundle_id, assets_by_bundle[raw_bundle_id]) if raw_bundle_id else None
                        normalized_items.append({**item, "bundle": rebuilt or raw_bundle})
                    else:
                        normalized_items.append(item)
                creative_output = {**creative_output, "outputs": normalized_items}
        if not creative_output and bundle:
            creative_output = {
                "type": bundle.get("type") or "image",
                "outputs": [{"kind": "asset_bundle", "bundle": bundle}],
                "metadata": {},
                "actions": bundle.get("actions", []),
            }
        attachments = message.attachments if isinstance(message.attachments, list) else None

        out.append(
            {
                "role": message.role,
                "content": message.content or "",
                "bundle": bundle,
                "creative_output": creative_output,
                "attachments": attachments,
            }
        )

    return out
