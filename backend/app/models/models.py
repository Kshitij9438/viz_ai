"""SQLAlchemy ORM models implementing the Vizzy data model spec (Section 13)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _uid("usr"))
    email: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    account_type: Mapped[str] = mapped_column(String, default="home")  # home | business
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_active: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _uid("sess"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active")
    # Compressed history of older turns
    compressed_history: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Tracking the active asset (most recently selected variant)
    active_asset_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _uid("msg"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    role: Mapped[str] = mapped_column(String)  # user | assistant | tool | system
    content: Mapped[str] = mapped_column(Text)
    # Monotonic per-session counter — guarantees stable replay ordering even when
    # multiple messages are committed in the same db.commit() call (same created_at).
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    input_type: Mapped[str] = mapped_column(String, default="text")
    attachments: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    tool_calls: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    asset_bundle_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _uid("ast"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    bundle_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    url: Mapped[str] = mapped_column(String)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    type: Mapped[str] = mapped_column(String)  # image | poster | sequence | vision_board | video | quote_card
    prompt: Mapped[str] = mapped_column(Text)
    style_tags: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    saved_permanently: Mapped[bool] = mapped_column(Boolean, default=False)
    variant_index: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class UserTasteProfile(Base):
    __tablename__ = "user_taste_profiles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _uid("taste"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    taste_summary: Mapped[str] = mapped_column(Text, default="")
    preferred_styles: Mapped[list] = mapped_column(JSON, default=list)
    preferred_colors: Mapped[list] = mapped_column(JSON, default=list)
    disliked_styles: Mapped[list] = mapped_column(JSON, default=list)
    past_selections: Mapped[list] = mapped_column(JSON, default=list)
    generation_count: Mapped[int] = mapped_column(Integer, default=0)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=_now)


class BusinessProfile(Base):
    __tablename__ = "business_profiles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _uid("biz"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    business_name: Mapped[str] = mapped_column(String)
    business_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sub_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    brand_tone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    brand_colors: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    font_preference: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    past_campaigns: Mapped[list] = mapped_column(JSON, default=list)
    goals: Mapped[list] = mapped_column(JSON, default=list)
    disallowed_themes: Mapped[list] = mapped_column(JSON, default=list)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=_now)


class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _uid("job"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    output_type: Mapped[str] = mapped_column(String)
    prompt: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    reference_image_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reference_strength: Mapped[Optional[float]] = mapped_column(default=None, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    asset_ids: Mapped[list] = mapped_column(JSON, default=list)
    bundle_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SessionSummary(Base):
    __tablename__ = "session_summaries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _uid("sm"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class UserCredential(Base):
    __tablename__ = "user_credentials"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: _uid("cred"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    hashed_password: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
