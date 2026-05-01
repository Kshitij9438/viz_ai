from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Tuple

from supabase import create_client

from app.core.config import settings


# ---------------------------
# ☁️ SUPABASE STORAGE
# ---------------------------

class SupabaseStorage:
    def __init__(self):
        self.client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_KEY,
        )
        self.bucket = "vizzy-assets"

    def save_bytes(
        self,
        data: bytes,
        suffix: str = ".jpg",
        subdir: str = "generated",
    ) -> Tuple[str, str]:

        file_id = f"{uuid.uuid4().hex}{suffix}"
        path = f"{subdir}/{file_id}"

        res = self.client.storage.from_(self.bucket).upload(
            path,
            data,
            file_options={"content-type": "image/jpeg"},
        )

        if isinstance(res, dict) and res.get("error"):
            raise RuntimeError(res["error"])

        public = self.client.storage.from_(self.bucket).get_public_url(path)

        if isinstance(public, dict):
            url = public.get("publicUrl")
        else:
            url = public

        return path, url


# ---------------------------
# 📁 LOCAL STORAGE (FALLBACK)
# ---------------------------

class LocalStorage:
    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or settings.STORAGE_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_bytes(
        self,
        data: bytes,
        suffix: str = ".jpg",
        subdir: str = "generated",
    ) -> Tuple[str, str]:

        out_dir = self.base_dir / subdir
        out_dir.mkdir(parents=True, exist_ok=True)

        name = f"{uuid.uuid4().hex}{suffix}"
        path = out_dir / name

        with open(path, "wb") as f:
            f.write(data)

        public_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/storage/{subdir}/{name}"
        return str(path), public_url


# ---------------------------
# 🔁 AUTO SWITCH
# ---------------------------

if settings.SUPABASE_URL and settings.SUPABASE_KEY:
    print("✅ Using Supabase Storage")
    storage = SupabaseStorage()
else:
    print("⚠️ Using Local Storage (fallback)")
    storage = LocalStorage()