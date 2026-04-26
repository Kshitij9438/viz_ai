# Vizzy AI

Conversational creative operating system — full implementation of the Vizzy Chat system design.

**Stack:** FastAPI (Python) + Next.js 14 (App Router) + SQLite (swap to Postgres) + Ollama (LLM with tool calling) + Pollinations.ai (image generation, no API key).

---

## What's implemented

| Spec layer | Status | Where |
|---|---|---|
| L1 Input Processing — image upload + LLaVA captioning | ✅ | `backend/app/routers/uploads.py`, `services/ollama_client.py` |
| L2 Conversation Engine — system prompt assembly + tool calling | ✅ | `backend/app/services/conversation.py` |
| L3 Intent & Output Router | ✅ | `backend/app/pipelines/router.py` (`run_generation`) |
| L4 Prompt Builder — taste/brand injection, negatives, boosters | ✅ | `backend/app/services/prompt_builder.py` |
| L5 Generation Pipeline — image, poster, story_sequence, vision_board, quote_card, before_after, style_transfer, video_loop (placeholder) | ✅ | `backend/app/pipelines/router.py` |
| L6 Output Assembly — AssetBundle JSON, persistent URLs via `/storage` | ✅ | same |
| Section 10 Taste Profile — silent background updater, cold start | ✅ | `backend/app/memory/memory.py` |
| Section 11 Business Profile — onboarding via PUT, brand injection | ✅ | `backend/app/routers/profiles.py`, prompt builder |
| Section 12 Session & Cross-session Memory — recent turns + compression + per-session summary | ✅ | `backend/app/memory/memory.py` |
| Section 13 Data Models — User, Session, Message, Asset, UserTasteProfile, BusinessProfile, GenerationJob, SessionSummary | ✅ | `backend/app/models/models.py` |
| Section 14 API — `/api/v1/chat`, profiles, assets, feedback, end-session | ✅ | `backend/app/routers/*` |
| `generate()` tool (Section 5.2) — exact schema | ✅ | `backend/app/services/generate_tool.py` |
| Frontend chat with multi-image grid + upload + variant selection | ✅ | `frontend/` |

---

## Quick start (without Docker)

### 1. Install Ollama and pull models

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b      # required — primary chat model with tool-calling
ollama pull llava:13b        # optional — for image upload captioning
ollama serve                  # runs on http://localhost:11434
```

### 2. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Health check: <http://localhost:8000/health>
Interactive docs: <http://localhost:8000/docs>

### 3. Frontend

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

Open <http://localhost:3000>.

### 4. End-to-end smoke test

```bash
python scripts/e2e_test.py
```

---

## Quick start (Docker)

> Ollama still needs to run on your host (Docker → host networking via `host.docker.internal`).

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
docker compose up --build
```

---

## generate() function schema

The LLM is given exactly the tool defined in Section 5.2 of the spec:

```json
{
  "output_type": "image|poster|story_sequence|vision_board|video_loop|quote_card|style_transfer|before_after",
  "prompt": "...",
  "style_tags": ["..."],
  "negative_prompt": "...",
  "count": 3,
  "reference_image_url": "...",
  "reference_strength": 0.6,
  "aspect_ratio": "landscape|portrait|square",
  "output_size": "1024x1024",
  "sequence_count": null,
  "poster_text": null,
  "poster_layout": "hero_text_top|hero_text_bottom|split_text_right|minimal_center"
}
```

When the LLM calls it:

1. Args are validated by Pydantic (`GenerateParams`).
2. If the user just uploaded an image and `reference_image_url` is empty, it's auto-attached.
3. `run_generation()` routes to the correct pipeline (image / poster / sequence / vision_board / quote_card / before_after / video_loop).
4. The Prompt Builder enriches the LLM's naturalistic prompt with taste preferences, brand aesthetics, quality boosters, and negatives.
5. Pollinations is called per-variant in parallel via `asyncio.gather`.
6. Bytes are saved to `/storage`, URLs are signed into the database.
7. An `AssetBundle` (Section 9.1 schema) is returned to the chat.

---

## Image backend

Default: **Pollinations.ai** — no API key, free.

To swap to HuggingFace Inference (free with HF token):

```env
IMAGE_BACKEND=huggingface
HF_TOKEN=hf_xxxxxx
```

To swap to local SDXL (GPU machine), implement the `LocalSDXLBackend` class in `app/services/image_backend.py` following the adapter pattern.

---

## Memory updates (background)

After every generation, two background tasks run silently:

1. `update_taste_after_feedback()` — sends a short LLM call to Ollama to merge the new interaction into the user's `taste_summary`. Cumulative; the user never sees it.
2. `maybe_compress_history()` — once a session exceeds 30 turns, older turns are folded into `Session.compressed_history` and the most recent 15 stay verbatim.

When a session is ended (`POST /api/v1/sessions/{id}/end`), a 2-3 sentence per-session summary is generated and stored in `session_summaries`. The most recent 5 are loaded into the system prompt at the start of every new session for that user.

---

## Project layout

```
backend/
  app/
    main.py              # FastAPI app + CORS + StaticFiles
    core/{config,db}.py
    models/models.py     # SQLAlchemy ORM (Section 13 data model)
    services/
      conversation.py    # L2 — Ollama chat with generate() tool
      generate_tool.py   # generate() schema (exact spec)
      prompt_builder.py  # L4 — enrichment + brand/taste injection
      ollama_client.py   # Ollama HTTP wrapper (chat + LLaVA captions)
      image_backend.py   # Pollinations / HF / local SDXL adapter
      storage.py         # Local file storage (Supabase-swappable)
    pipelines/router.py  # L3 + L5 + L6 — routing, generation, AssetBundle
    memory/memory.py     # Section 10 + 12
    routers/
      chat.py            # POST /api/v1/chat, /feedback, /sessions/{id}/end
      uploads.py         # POST /api/v1/uploads (image + LLaVA caption)
      profiles.py        # taste-profile, business-profile CRUD
      assets.py          # asset retrieval + save permanently

frontend/
  app/page.tsx           # chat UI with markdown + image grid + upload
  components/AssetGrid.tsx
  lib/api.ts

scripts/e2e_test.py      # full chat → generate → URL roundtrip test
docker-compose.yml
```

---

## Notes & extension points

- **Postgres / Supabase**: change `DATABASE_URL` to a Postgres async URL (`postgresql+asyncpg://...`), `pip install asyncpg`. Models are unchanged.
- **Asset storage**: swap `LocalStorage` in `app/services/storage.py` for a Supabase Storage adapter using the same `save_bytes` interface.
- **Voice input**: front-end uses no voice yet — drop in the Web Speech API and call `/api/v1/uploads` with the resulting blob if needed.
- **Frame push** (Section 9.3): not wired — add a `routers/frames.py` with a WebSocket endpoint per device.
- **Video loops**: the `video_loop` pipeline currently returns a still as a placeholder. Wire AnimateDiff or similar when GPU is available.
