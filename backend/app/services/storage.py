from __future__ import annotations

import mimetypes
import logging
import uuid
from pathlib import Path
from typing import Tuple
from urllib.parse import urlparse

from supabase import create_client

from app.core.config import settings


BUCKET = "vizzy-assets"
logger = logging.getLogger("vizzy.storage")


def _clean_path(value: str) -> str:
    value = value.strip()
    if not value:
        return value

    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        if settings.SUPABASE_URL and parsed.netloc == urlparse(settings.SUPABASE_URL).netloc:
            return value
        marker = f"/storage/v1/object/public/{BUCKET}/"
        if marker in parsed.path:
            return parsed.path.split(marker, 1)[1].lstrip("/")
        if "/storage/" in parsed.path:
            return parsed.path.split("/storage/", 1)[1].lstrip("/")
        return value

    return value.removeprefix("/storage/").lstrip("/")


class SupabaseStorage:
    def __init__(self) -> None:
        self.client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        self.bucket = BUCKET

    def public_url(self, path_or_url: str) -> str:
        path = _clean_path(path_or_url)
        if not path:
            return path
        if path.startswith(("http://", "https://")):
            return path

        public = self.client.storage.from_(self.bucket).get_public_url(path)
        if isinstance(public, dict):
            return public.get("publicUrl") or public.get("public_url") or ""
        return str(public)

    def save_bytes(
        self,
        data: bytes,
        suffix: str = ".jpg",
        subdir: str = "generated",
        content_type: str = "image/jpeg",
    ) -> Tuple[str, str]:
        file_id = f"{uuid.uuid4().hex}{suffix}"
        path = f"{subdir.strip('/')}/{file_id}"

        res = self.client.storage.from_(self.bucket).upload(
            path,
            data,
            file_options={"content-type": content_type, "upsert": "false"},
        )
        if isinstance(res, dict) and res.get("error"):
            raise RuntimeError(res["error"])

        return path, self.public_url(path)

    def save_upload(self, data: bytes, filename: str) -> Tuple[str, str]:
        suffix = Path(filename).suffix or ".bin"
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return self.save_bytes(data, suffix=suffix, subdir="uploads", content_type=content_type)


class LocalStorage:
    """Development fallback only.

    Production must set Supabase credentials so API responses are canonical
    Supabase public URLs. The backend still returns absolute URLs in local mode
    to keep manual development usable.
    """

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.STORAGE_DIR).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def public_url(self, path_or_url: str) -> str:
        value = _clean_path(path_or_url)
        if value.startswith(("http://", "https://", "data:")):
            return value
        base = settings.PUBLIC_BASE_URL.rstrip("/")
        if not base:
            raise RuntimeError("PUBLIC_BASE_URL is required when Supabase storage is not configured")
        return f"{base}/storage/{value.lstrip('/')}"

    def save_bytes(
        self,
        data: bytes,
        suffix: str = ".jpg",
        subdir: str = "generated",
        content_type: str = "image/jpeg",
    ) -> Tuple[str, str]:
        out_dir = self.base_dir / subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{suffix}"
        path = out_dir / name
        path.write_bytes(data)
        public_path = f"{subdir.strip('/')}/{name}"
        return public_path, self.public_url(public_path)

    def save_upload(self, data: bytes, filename: str) -> Tuple[str, str]:
        suffix = Path(filename).suffix or ".bin"
        return self.save_bytes(data, suffix=suffix, subdir="uploads")


if settings.SUPABASE_URL and settings.SUPABASE_KEY:
    logger.info("storage_backend_selected", extra={"event": "storage_backend_selected", "backend": "supabase"})
    storage = SupabaseStorage()
else:
    logger.info("storage_backend_selected", extra={"event": "storage_backend_selected", "backend": "local"})
    storage = LocalStorage()


def public_asset_url(path_or_url: str | None) -> str:
    if not path_or_url:
        return ""
    return storage.public_url(path_or_url)
