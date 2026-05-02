"""Layer 5 — Generation Pipelines + Layer 3 routing + Layer 6 AssetBundle assembly.

Chat-only turns bypass this module: ``app.services.pipeline_engine.execute_pipeline``
honors ``PipelineContext.force_chat_pipeline`` and runs ``ChatPipeline`` without
calling ``run_generation``.
"""
from __future__ import annotations

import hashlib
import io
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Asset, BusinessProfile, GenerationJob, UserTasteProfile
from app.services.generate_tool import GenerateParams
from app.services.image_backend import image_backend
from app.services.prompt_builder import build_image_prompt, build_negative_prompt, resolve_size
from app.services.storage import storage


def _generation_base_seed(user_id: str, prompt: str) -> int:
    """Per-request seed for diversity + reproducibility if inputs replay (includes time)."""
    payload = f"{user_id}\0{prompt}\0{time.time_ns()}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % (2**31 - 2) + 1


def _seed_variant(base: int, salt: int) -> int:
    return ((base + salt * 100_003) % (2**31 - 2)) + 1


# ---------- low-level generation ----------

async def _gen_one(
    prompt: str,
    *,
    negative_prompt: str | None,
    width: int,
    height: int,
    reference_image_url: str | None = None,
    reference_strength: float | None = None,
    seed: int | None = None,
) -> bytes:
    """Generate a single image via the rate-limited backend."""
    return await image_backend.generate_safe(
        prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        reference_image_url=reference_image_url,
        reference_strength=reference_strength,
        seed=seed,
    )


async def _gen_many(prompt: str, n: int, *, seed: int | None = None, **kw: Any) -> list[bytes]:
    """Generate n images sequentially so a first 429 stops additional seeds."""
    async def one(i: int) -> bytes:
        s = None if seed is None else _seed_variant(seed, i)
        return await _gen_one(prompt, seed=s, **kw)

    images: list[bytes] = []
    for i in range(n):
        images.append(await one(i))
    return images


# ---------- compositing helpers ----------

def _grid(images: list[bytes], cols: int | None = None) -> bytes:
    pil = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
    n = len(pil)
    cols = cols or (3 if n >= 6 else 2 if n >= 2 else 1)
    rows = (n + cols - 1) // cols
    w = max(im.width for im in pil)
    h = max(im.height for im in pil)
    pad = 12
    canvas = Image.new("RGB", (cols * w + (cols + 1) * pad, rows * h + (rows + 1) * pad), "white")
    for i, im in enumerate(pil):
        r, c = divmod(i, cols)
        canvas.paste(im.resize((w, h)), (pad + c * (w + pad), pad + r * (h + pad)))
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=88)
    return out.getvalue()


def _composite_poster(bg: bytes, text: str | None, layout: str | None) -> bytes:
    img = Image.open(io.BytesIO(bg)).convert("RGB")
    if not text:
        out = io.BytesIO(); img.save(out, format="JPEG", quality=90); return out.getvalue()
    draw = ImageDraw.Draw(img, "RGBA")
    W, H = img.size
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=int(H / 14))
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 24
    if layout == "hero_text_bottom":
        x, y = (W - tw) // 2, H - th - pad * 3
    elif layout == "split_text_right":
        x, y = W - tw - pad * 2, (H - th) // 2
    elif layout == "minimal_center":
        x, y = (W - tw) // 2, (H - th) // 2
    else:  # hero_text_top default
        x, y = (W - tw) // 2, pad * 2
    draw.rectangle((x - pad, y - pad // 2, x + tw + pad, y + th + pad), fill=(0, 0, 0, 140))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
    out = io.BytesIO(); img.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _quote_card(text: str, width: int = 1024, height: int = 1024) -> bytes:
    img = Image.new("RGB", (width, height), (245, 236, 215))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf", size=int(height / 18))
    except Exception:  # noqa: BLE001
        font = ImageFont.load_default()
    # naive wrapping
    words = text.split()
    lines, cur = [], ""
    max_w = width - 120
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    line_h = int(height / 14)
    total_h = len(lines) * line_h
    y = (height - total_h) // 2
    for line in lines:
        tw = draw.textlength(line, font=font)
        draw.text(((width - tw) / 2, y), line, font=font, fill=(50, 30, 20))
        y += line_h
    out = io.BytesIO(); img.save(out, format="JPEG", quality=92)
    return out.getvalue()


# ---------- main entry: route + run ----------

async def run_generation(
    db: AsyncSession,
    *,
    params: GenerateParams,
    user_id: str,
    session_id: str,
    taste: UserTasteProfile | None,
    business: BusinessProfile | None,
) -> dict[str, Any]:
    """Routes generate() params to the correct pipeline and returns an AssetBundle dict."""
    bundle_id = f"bnd_{uuid.uuid4().hex[:12]}"
    width, height = resolve_size(params.aspect_ratio, params.output_size)

    job = GenerationJob(
        session_id=session_id,
        user_id=user_id,
        output_type=params.output_type,
        prompt=params.prompt,
        negative_prompt=params.negative_prompt,
        count=params.count,
        reference_image_url=params.reference_image_url,
        reference_strength=params.reference_strength,
        bundle_id=bundle_id,
        status="running",
    )
    db.add(job)
    await db.commit()

    enriched = build_image_prompt(
        params.prompt, style_tags=params.style_tags, taste=taste, business=business
    )
    negative = build_negative_prompt(params.negative_prompt, taste=taste)
    base_seed = _generation_base_seed(user_id, params.prompt)

    asset_records: list[Asset] = []
    bundle_type = "image_grid"
    expires_at = datetime.utcnow() + timedelta(days=30)

    # ---- routing ----
    if params.output_type in ("image", "style_transfer"):
        n = max(1, min(params.count or 3, 9))
        imgs = await _gen_many(
            enriched,
            n,
            seed=base_seed,
            negative_prompt=negative,
            width=width,
            height=height,
            reference_image_url=params.reference_image_url,
            reference_strength=params.reference_strength,
        )
        for i, b in enumerate(imgs, start=1):
            _, url = storage.save_bytes(b, ".jpg")
            asset_records.append(Asset(
                user_id=user_id, session_id=session_id, bundle_id=bundle_id,
                url=url, type="image", prompt=enriched, style_tags=params.style_tags,
                variant_index=i, expires_at=expires_at,
            ))

    elif params.output_type == "poster":
        bg = await _gen_one(
            enriched + ", clean composition with empty space for text overlay",
            negative_prompt=negative,
            width=width,
            height=height,
            seed=base_seed,
        )
        composed = _composite_poster(bg, params.poster_text, params.poster_layout)
        _, url = storage.save_bytes(composed, ".jpg")
        asset_records.append(Asset(
            user_id=user_id, session_id=session_id, bundle_id=bundle_id,
            url=url, type="poster", prompt=enriched, style_tags=params.style_tags,
            expires_at=expires_at,
        ))
        bundle_type = "poster"

    elif params.output_type == "story_sequence":
        n = max(3, min(params.sequence_count or 6, 8))
        prev: bytes | None = None
        panels: list[bytes] = []
        for i in range(n):
            panel_prompt = f"{enriched}, panel {i + 1} of {n}, consistent art style and color palette"
            ref_url = params.reference_image_url
            ref_strength = params.reference_strength
            if prev is not None:
                # save previous as a temp public URL for img2img continuity
                _, ref_url = storage.save_bytes(prev, ".jpg", subdir="refs")
                ref_strength = 0.3
            b = await _gen_one(
                panel_prompt,
                negative_prompt=negative,
                width=width,
                height=height,
                reference_image_url=ref_url,
                reference_strength=ref_strength,
                seed=_seed_variant(base_seed, i),
            )
            panels.append(b); prev = b
        for i, b in enumerate(panels, start=1):
            _, url = storage.save_bytes(b, ".jpg")
            asset_records.append(Asset(
                user_id=user_id, session_id=session_id, bundle_id=bundle_id,
                url=url, type="sequence", prompt=enriched, style_tags=params.style_tags,
                variant_index=i, expires_at=expires_at,
            ))
        bundle_type = "story_sequence"

    elif params.output_type == "vision_board":
        n = params.count if params.count in (4, 6, 9) else 6
        imgs = await _gen_many(
            enriched,
            n,
            seed=base_seed,
            negative_prompt=negative,
            width=width,
            height=height,
        )
        composed = _grid(imgs, cols=3 if n == 9 else (3 if n == 6 else 2))
        _, board_url = storage.save_bytes(composed, ".jpg")
        asset_records.append(Asset(
            user_id=user_id, session_id=session_id, bundle_id=bundle_id,
            url=board_url, type="vision_board", prompt=enriched, style_tags=params.style_tags,
            expires_at=expires_at,
        ))
        bundle_type = "vision_board"

    elif params.output_type == "quote_card":
        text = params.poster_text or params.prompt
        composed = _quote_card(text, width, height)
        _, url = storage.save_bytes(composed, ".jpg")
        asset_records.append(Asset(
            user_id=user_id, session_id=session_id, bundle_id=bundle_id,
            url=url, type="quote_card", prompt=text, style_tags=params.style_tags,
            expires_at=expires_at,
        ))
        bundle_type = "quote_card"

    elif params.output_type == "before_after":
        if not params.reference_image_url:
            raise ValueError("before_after requires reference_image_url")
        after = await _gen_one(
            enriched,
            negative_prompt=negative,
            width=width,
            height=height,
            reference_image_url=params.reference_image_url,
            reference_strength=params.reference_strength or 0.65,
            seed=base_seed,
        )
        # fetch original
        import httpx as _h
        async with _h.AsyncClient(timeout=60.0) as c:
            r = await c.get(params.reference_image_url)
            r.raise_for_status()
            before = r.content
        composed = _grid([before, after], cols=2)
        _, url = storage.save_bytes(composed, ".jpg")
        asset_records.append(Asset(
            user_id=user_id, session_id=session_id, bundle_id=bundle_id,
            url=url, type="before_after", prompt=enriched, expires_at=expires_at,
        ))
        bundle_type = "before_after"

    elif params.output_type == "video_loop":
        # Placeholder — generate a single still and return as a "video poster" until AnimateDiff is wired.
        b = await _gen_one(
            enriched,
            negative_prompt=negative,
            width=width,
            height=height,
            seed=base_seed,
        )
        _, url = storage.save_bytes(b, ".jpg")
        asset_records.append(Asset(
            user_id=user_id, session_id=session_id, bundle_id=bundle_id,
            url=url, type="video", prompt=enriched, expires_at=expires_at,
        ))
        bundle_type = "video_loop_placeholder"

    else:
        raise ValueError(f"Unknown output_type: {params.output_type}")

    db.add_all(asset_records)
    job.status = "complete"
    job.completed_at = datetime.utcnow()
    job.asset_ids = [a.id for a in asset_records]
    await db.commit()

    return {
        "bundle_id": bundle_id,
        "type": bundle_type,
        "assets": [
            {"id": a.id, "url": a.url, "index": a.variant_index, "type": a.type}
            for a in asset_records
        ],
        "prompt_used": enriched,
        "negative_prompt_used": negative,
        "actions": ["select", "download_all", "refine", "send_to_frame", "share", "save"],
    }
