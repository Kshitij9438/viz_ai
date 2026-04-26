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
    """
    out: list[dict[str, Any]] = []

    for m in msgs:
        if m.role == "assistant" and m.tool_calls:
            raw_calls = m.tool_calls.get("calls", [])
            normalized = [_normalize_tool_call(c) for c in raw_calls]
            out.append({
                "role": "assistant",
                "content": m.content or "",
                "tool_calls": normalized,
            })

        elif m.role == "tool":
            tool_call_id = (
                m.tool_calls.get("tool_call_id") if m.tool_calls else None
            )
            # Rule 2: drop irrecoverable orphan tool messages
            if not tool_call_id:
                continue
            # Rule 1: drop if the immediately preceding out-entry is not
            # an assistant message that has tool_calls (orphan at boundary)
            if not out or out[-1].get("role") != "assistant" or not out[-1].get("tool_calls"):
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

    return out


# ---------------- MAIN LOOP ---------------- #

async def converse(
    db: AsyncSession,
    *,
    session,
    user_message: str,
    attachments: list[dict] | None = None,
) -> dict[str, Any]:

    # -------- CONTEXT -------- #
    taste = await get_or_create_taste(db, session.user_id)
    business = await get_business(db, session.user_id)
    summaries = await get_recent_session_summaries(db, session.user_id)
    history = await get_recent_messages(db, session.id)

    system_prompt = _assemble_system_prompt(
        taste=taste,
        business=business,
        compressed_history=session.compressed_history,
        session_summaries=summaries,
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(_msgs_to_model(history))

    # -------- USER MESSAGE -------- #
    user_content = user_message
    messages.append({"role": "user", "content": user_content})

    user_seq = await _next_seq(db, session.id)
    db.add(Message(
        session_id=session.id,
        role="user",
        content=user_content,
        sequence=user_seq,
    ))
    session.message_count += 1
    await db.commit()

    # -------- AGENT LOOP -------- #
    MAX_STEPS = 5
    asset_bundle = None
    captured_call = None
    assistant_text = ""

    for _ in range(MAX_STEPS):

        resp = await ollama.chat(
            messages=messages,
            tools=[GENERATE_TOOL_SCHEMA],
        )

        msg = resp.get("message", {})
        assistant_text = (msg.get("content") or "").strip()
        raw_tool_calls: list[dict] = msg.get("tool_calls") or []

        # -------- NORMALIZE TOOL CALLS -------- #
        # Do this once here so both the in-memory entry AND the DB entry
        # use exactly the same normalized form (same ids, type, argument format).
        tool_calls = [_normalize_tool_call(c) for c in raw_tool_calls]

        # -------- BUILD & PERSIST ASSISTANT ENTRY -------- #
        assistant_entry: dict[str, Any] = {
            "role": "assistant",
            "content": assistant_text,
        }
        if tool_calls:
            assistant_entry["tool_calls"] = tool_calls

        messages.append(assistant_entry)

        asst_seq = await _next_seq(db, session.id)
        db.add(Message(
            session_id=session.id,
            role="assistant",
            content=assistant_text,
            tool_calls={"calls": tool_calls} if tool_calls else None,
            sequence=asst_seq,
        ))

        # -------- CRITICAL: commit assistant BEFORE tool execution -------- #
        # If tool execution raises, we still have the assistant message in the
        # DB. Without this commit, a retry would persist the assistant message
        # again, creating a ghost entry with no paired tool message.
        await db.commit()

        # -------- NO TOOL → DONE -------- #
        if not tool_calls:
            break

        # -------- TOOL EXECUTION -------- #
        for call in tool_calls:
            # call is already normalized: id is guaranteed non-null, type is present
            call_id: str = call["id"]
            fn = call.get("function", {})

            if fn.get("name") != "generate":
                # Unknown tool — respond with an error so the LLM can recover
                tool_result = json.dumps({"error": f"Unknown tool: {fn.get('name')}"})
            else:
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}

                try:
                    params = GenerateParams.model_validate(args)
                except Exception as exc:
                    tool_result = json.dumps({"error": f"Invalid arguments: {exc}"})
                else:
                    captured_call = {"name": "generate", "arguments": params.model_dump()}

                    asset_bundle = await run_generation(
                        db,
                        params=params,
                        user_id=session.user_id,
                        session_id=session.id,
                        taste=taste,
                        business=business,
                    )

                    session.last_prompt = asset_bundle["prompt_used"]

                    tool_result = json.dumps({
                        "status": "success",
                        "bundle_id": asset_bundle["bundle_id"],
                    })

            # Tool message — tool_call_id MUST match the assistant's call id
            tool_seq = await _next_seq(db, session.id)
            tool_msg: dict[str, Any] = {
                "role": "tool",
                "tool_call_id": call_id,   # guaranteed non-null (normalized above)
                "content": tool_result,
            }
            messages.append(tool_msg)

            db.add(Message(
                session_id=session.id,
                role="tool",
                content=tool_result,
                tool_calls={"tool_call_id": call_id},
                sequence=tool_seq,
            ))

        await db.commit()

    # -------- FINAL RESPONSE -------- #
    return {
        "reply": assistant_text or "Tell me more.",
        "asset_bundle": asset_bundle,
        "tool_call": captured_call,
    }