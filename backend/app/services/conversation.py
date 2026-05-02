from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.memory import (
    get_business,
    get_or_create_taste,
    get_recent_messages,
    get_recent_session_summaries,
)
from app.models.models import BusinessProfile, Message, UserTasteProfile
from app.pipelines.router import run_generation
from app.services.intent_engine import IntentResult, PIPELINE_STEPS, classify_intent
from app.services.pipeline_engine import PipelineContext, execute_pipeline
from app.services.generate_tool import GENERATE_TOOL_SCHEMA, GenerateParams
from app.services.ollama_client import ollama


BASE_PERSONA = (
    "You are Vizzy, a warm creative assistant. You help users shape and generate visual content.\n"
    "- Ask one short question per turn.\n"
    "- After confirming direction, call generate().\n"
    "- Keep responses natural and short."
)


# ---------------- PROMPT ---------------- #

def _assemble_system_prompt(
    *,
    taste: UserTasteProfile | None,
    business: BusinessProfile | None,
    compressed_history: str | None,
    session_summaries: list[str],
) -> str:
    parts = [BASE_PERSONA]

    if business:
        parts.append(f"Business: {business.business_name}")

    if taste and taste.taste_summary:
        parts.append(f"Taste: {taste.taste_summary}")

    if session_summaries:
        parts.append(f"Past: {' | '.join(session_summaries)}")

    if compressed_history:
        parts.append(f"Earlier: {compressed_history}")

    return "\n\n".join(parts)


# ---------------- SEQUENCE COUNTER ---------------- #

async def _next_seq(db: AsyncSession, session_id: str) -> int:
    """Return the next monotonic sequence number for a session's messages.

    Uses MAX(sequence)+1 so it is correct even if rows were inserted
    with gaps or out of order (e.g. after a failed transaction retry).
    """
    result = await db.execute(
        select(func.max(Message.sequence)).where(Message.session_id == session_id)
    )
    current_max = result.scalar()  # None if no rows yet
    return (current_max or 0) + 1


# ---------------- HISTORY FORMAT ---------------- #

def _normalize_tool_call(c: dict) -> dict:
    """Ensure a tool_call dict is fully OpenAI-protocol-compliant.

    Guarantees:
      - "type": "function" is present
      - "id" is a non-null string (generates a fallback if missing)
      - "function.arguments" is a JSON string (not a dict)
    """
    call_id = c.get("id") or f"call_{uuid.uuid4().hex[:12]}"
    args = c.get("function", {}).get("arguments", {})
    if isinstance(args, dict):
        args = json.dumps(args)
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": c.get("function", {}).get("name"),
            "arguments": args,
        },
    }


def _msgs_to_model(msgs: list[Message]) -> list[dict[str, Any]]:
    """Convert ORM Message rows into the list of dicts the LLM API expects.

    Defensive rules applied:
      1. Leading tool messages (orphans from a history-window boundary) are
         dropped — the assistant message that triggered them is outside the
         window, so replaying the tool message alone causes "tool must follow
         tool_calls" from the OpenAI API.
      2. Any tool message whose tool_call_id resolves to None is also dropped
         for the same reason.
      3. Every tool_call in assistant messages is passed through
         _normalize_tool_call() to guarantee type + id fields.
      4. After assembly, any assistant message with tool_calls that is NOT
         followed by tool responses for ALL of its call IDs is stripped down
         to a plain assistant message (tool_calls removed).  This prevents the
         OpenAI "tool_call_id did not have response" 400 error.
    """
    out: list[dict[str, Any]] = []

    # Collect the set of valid tool_call_ids from all assistant messages
    # so tool messages can verify their parent exists in the window.
    valid_call_ids: set[str] = set()

    for m in msgs:
        if m.role == "assistant" and m.tool_calls:
            raw_calls = m.tool_calls.get("calls", [])
            normalized = [_normalize_tool_call(c) for c in raw_calls]
            out.append({
                "role": "assistant",
                "content": m.content or "",
                "tool_calls": normalized,
            })
            for c in normalized:
                valid_call_ids.add(c["id"])

        elif m.role == "tool":
            tool_call_id = (
                m.tool_calls.get("tool_call_id") if m.tool_calls else None
            )
            # Rule 2: drop irrecoverable orphan tool messages
            if not tool_call_id:
                continue
            # Rule 1: drop if the parent assistant tool_call is not in our window
            if tool_call_id not in valid_call_ids:
                continue
            out.append({
                "role": "tool",
                "content": m.content or "",
                "tool_call_id": tool_call_id,
            })

        else:
            out.append({
                "role": m.role,
                "content": m.content or "",
            })

    # ---- Post-pass: ensure every assistant tool_calls block is fully answered ----
    # Collect all tool_call_ids that actually have a tool response in `out`.
    answered_ids: set[str] = set()
    for entry in out:
        if entry.get("role") == "tool" and entry.get("tool_call_id"):
            answered_ids.add(entry["tool_call_id"])

    # Walk through and fix / remove incomplete assistant+tool_calls groups.
    cleaned: list[dict[str, Any]] = []
    i = 0
    while i < len(out):
        entry = out[i]
        if entry.get("role") == "assistant" and entry.get("tool_calls"):
            calls = entry["tool_calls"]
            expected_ids = {c["id"] for c in calls}
            if expected_ids <= answered_ids:
                # All tool responses present — keep assistant and its tool messages
                cleaned.append(entry)
            else:
                # Missing at least one tool response — strip tool_calls and
                # drop any orphan tool messages that reference these calls.
                cleaned.append({
                    "role": "assistant",
                    "content": entry.get("content") or "",
                })
                # Skip the following tool messages that belong to this group
                while i + 1 < len(out) and out[i + 1].get("role") == "tool" and out[i + 1].get("tool_call_id") in expected_ids:
                    i += 1
        else:
            cleaned.append(entry)
        i += 1

    return cleaned


# ---------------- MAIN LOOP ---------------- #

async def converse(
    db: AsyncSession,
    *,
    session,
    user_message: str,
    attachments: list[dict] | None = None,
    force_chat_pipeline: bool = False,
    refinement_mode: bool = False,
    awaiting_confirmation: bool = False,
    design_context: dict[str, Any] | None = None,
) -> dict[str, Any]:

    # -------- CONTEXT -------- #
    taste = await get_or_create_taste(db, session.user_id)
    business = await get_business(db, session.user_id)
    summaries = await get_recent_session_summaries(db, session.user_id)

    # 🔥 STRONG RECENT MEMORY (guaranteed continuity)
    result = await db.execute(
        select(Message)
        .where(Message.session_id == session.id)
        .order_by(Message.sequence.desc())
        .limit(12)  # ⚖️ balanced window
    )
    recent_msgs = list(reversed(result.scalars().all()))

    # Fallback (in case DB is empty / edge case)
    if not recent_msgs:
        recent_msgs = await get_recent_messages(db, session.id)

    system_prompt = _assemble_system_prompt(
        taste=taste,
        business=business,
        compressed_history=session.compressed_history,
        session_summaries=summaries,
    )

    # 🔥 subtle but important: enforce conversational continuity
    system_prompt += (
        "\n\nYou are in an ongoing conversation. "
        "Always refer to previous messages when relevant."
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt}
    ]

    messages.extend(_msgs_to_model(recent_msgs))

    # -------- USER MESSAGE -------- #
    user_content = user_message
    messages.append({"role": "user", "content": user_content})

    # -------- STORE USER -------- #
    user_seq = await _next_seq(db, session.id)
    db.add(Message(
        session_id=session.id,
        role="user",
        content=user_content,
        sequence=user_seq,
        attachments=attachments,
    ))
    session.message_count += 1
    await db.commit()

    # -------- CREATIVE OS INTENT + PIPELINE -------- #
    if force_chat_pipeline:
        intent = IntentResult(
            intent="chat",
            pipeline="chat_pipeline",
            steps=PIPELINE_STEPS["chat_pipeline"],
            confidence=1.0,
            execute=False,
            parameters={},
        )
    else:
        intent = await classify_intent(
            message=user_message,
            attachments=attachments,
            recent_messages=recent_msgs,
            taste=taste,
            business=business,
        )

    dc = design_context if design_context is not None else getattr(session, "design_context", None)

    result = await execute_pipeline(
        PipelineContext(
            db=db,
            user_id=session.user_id,
            session_id=session.id,
            message=user_message,
            attachments=attachments,
            recent_messages=recent_msgs,
            taste=taste,
            business=business,
            session_last_prompt=session.last_prompt,
            refinement_mode=refinement_mode,
            awaiting_confirmation=awaiting_confirmation,
            design_context=dc if isinstance(dc, dict) else None,
        ),
        intent,
    )

    if result.primary_bundle:
        session.last_prompt = result.primary_bundle.get("prompt_used") or result.memory_signal

    asst_seq = await _next_seq(db, session.id)
    assistant_record = Message(
        session_id=session.id,
        role="assistant",
        content=result.reply,
        tool_calls={
            "creative_output": result.creative_output,
            "intent": intent.model_dump(),
            "bundle_ids": result.bundle_ids,
        },
        asset_bundle_id=result.primary_bundle.get("bundle_id") if result.primary_bundle else None,
        sequence=asst_seq,
    )
    db.add(assistant_record)
    await db.commit()

    return {
        "reply": result.reply,
        "asset_bundle": result.primary_bundle,
        "creative_output": result.creative_output,
        "tool_call": result.tool_call,
        "intent": intent.model_dump(),
    }
