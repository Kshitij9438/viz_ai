#from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile, Request

from app.core.auth import IdentityContext, get_current_or_guest_user
from app.core.limiter import limiter
from app.services.ollama_client import ollama
from app.services.storage import storage

router = APIRouter(prefix="/api/v1", tags=["uploads"])


@router.post("/uploads", response_model=dict)  # 🔥 CRITICAL FIX
@limiter.limit("10/minute")
async def upload(
    request: Request,  # 🔥 REQUIRED for slowapi
    file: UploadFile = File(...),
    identity: IdentityContext = Depends(get_current_or_guest_user),
) -> dict:
    """
    Upload file → store in cloud → optionally caption images
    """

    # Ensure identity is resolved (sets request.state.user for limiter)
    _ = identity

    data = await file.read()

    # Save to storage (Supabase or fallback)
    _, url = storage.save_upload(data, file.filename or "upload.bin")

    caption = ""
    is_image = (file.content_type or "").startswith("image/")

    # Auto-caption images (non-blocking failure)
    if is_image:
        try:
            caption = await ollama.caption_image(data)
        except Exception:
            caption = "user-shared image"

    return {
        "url": url,
        "caption": caption,
        "type": "image" if is_image else "file",
    }