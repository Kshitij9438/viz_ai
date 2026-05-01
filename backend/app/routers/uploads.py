from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile

from app.core.auth import IdentityContext, get_current_or_guest_user
from app.services.ollama_client import ollama
from app.services.storage import storage

router = APIRouter(prefix="/api/v1", tags=["uploads"])


@router.post("/uploads")
async def upload(file: UploadFile = File(...), identity: IdentityContext = Depends(get_current_or_guest_user)) -> dict:
    _ = identity
    data = await file.read()
    _, url = storage.save_upload(data, file.filename or "upload.bin")
    caption = ""
    if (file.content_type or "").startswith("image/"):
        try:
            caption = await ollama.caption_image(data)
        except Exception:  # noqa: BLE001
            caption = "user-shared image"
    return {"url": url, "caption": caption, "type": "image" if (file.content_type or "").startswith("image/") else "file"}
