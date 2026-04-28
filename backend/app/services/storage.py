"""Local filesystem asset storage. Public URLs served by FastAPI StaticFiles at /storage."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from app.core.config import settings


class LocalStorage:
    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or settings.STORAGE_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_bytes(self, data: bytes, suffix: str = ".jpg", subdir: str = "generated") -> tuple[str, str]:
        """Returns (file_path, public_url)."""
        print("🔥 PUBLIC_BASE_URL:", settings.PUBLIC_BASE_URL)
        out_dir = self.base_dir / subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{suffix}"
        path = out_dir / name
        with open(path, "wb") as f:
            f.write(data)
        public_url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/storage/{subdir}/{name}"
        return str(path), public_url

    def save_upload(self, data: bytes, filename: str) -> tuple[str, str]:
        ext = os.path.splitext(filename)[1] or ".bin"
        return self.save_bytes(data, suffix=ext, subdir="uploads")


storage = LocalStorage()
