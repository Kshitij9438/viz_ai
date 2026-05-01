import os
import asyncio
from sqlalchemy import select

from supabase import create_client

from app.core.config import settings
from app.core.db import get_session
from app.models.models import Asset


SUPABASE_URL = settings.SUPABASE_URL
SUPABASE_KEY = settings.SUPABASE_KEY
BUCKET = settings.SUPABASE_BUCKET

client = create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================
# 🔥 PRELOAD EXISTING FILES (FAST)
# =========================
existing_files = set()


def preload():
    global existing_files
    try:
        files = client.storage.from_(BUCKET).list("generated")
        existing_files = set(f["name"] for f in files)
        print(f"📦 Found {len(existing_files)} files already in Supabase")
    except Exception as e:
        print("⚠️ Failed to preload:", e)


def file_exists(filename: str) -> bool:
    return filename in existing_files


# =========================
# 🚀 MIGRATION
# =========================
async def migrate():
    preload()

    async for db in get_session():
        result = await db.execute(select(Asset))
        assets = result.scalars().all()

        for a in assets:
            if not a.url:
                continue

            # ✅ skip already migrated
            if a.url.startswith("http") or a.url.startswith("generated/"):
                continue

            # only old local files
            if not a.url.startswith("/storage/"):
                continue

            filename = a.url.split("/")[-1]
            local_path = os.path.join("storage", "generated", filename)

            if not os.path.exists(local_path):
                print(f"❌ Missing local file: {filename}")
                continue

            # =========================
            # UPLOAD IF NEEDED
            # =========================
            if file_exists(filename):
                print(f"⏭️ Already exists: {filename}")
            else:
                print(f"⬆️ Uploading: {filename}")

                with open(local_path, "rb") as f:
                    try:
                        client.storage.from_(BUCKET).upload(
                            f"generated/{filename}",
                            f,
                            {
                                "content-type": "image/jpeg",
                                "upsert": True,  # 🔥 avoids duplicate crash
                            },
                        )
                    except Exception as e:
                        print(f"⚠️ Upload failed {filename}: {e}")
                        continue

            # =========================
            # UPDATE DB
            # =========================
            a.url = f"generated/{filename}"

        await db.commit()
        print("✅ Migration committed")


# =========================
# RUN
# =========================
if __name__ == "__main__":
    asyncio.run(migrate())