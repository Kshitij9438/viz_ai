# Vizzy AI

**Conversational creative operating system** — chat with an AI to generate images, posters, story sequences, vision boards, quote cards, and more. Vizzy learns your taste over time and builds a persistent creative profile across sessions.

---

## True Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| **LLM** | GitHub Models API (`gpt-4.1-mini`) | Azure OpenAI-compatible endpoint; requires a GitHub PAT |
| **Vision** | GitHub Models API (`gpt-4.1-mini`) | Same model handles image captioning on upload |
| **Image Generation** | [Pollinations.ai](https://pollinations.ai) (FLUX) | Free, no API key; HuggingFace Inference is the fallback |
| **Backend** | FastAPI (Python 3.11) | Async, tool-calling agent loop |
| **Database** | SQLite via `aiosqlite` | Dev only — must swap for production (see below) |
| **File Storage** | Local filesystem (`./storage`) | Dev only — must swap for production |
| **Frontend** | Next.js 14 (App Router) | React, Tailwind CSS, react-markdown |
| **Deploy targets** | Railway (backend) + Vercel (frontend) | See deployment section |

> **Important clarification:** The file `backend/app/services/ollama_client.py` is named for historical reasons but contains `GitHubModelsClient`, which calls the GitHub Models Azure-OpenAI-compatible API. Ollama is **not used** anywhere in this codebase.

---

## What's Actually Built

### Conversation Engine (`services/conversation.py`)
- Stateful agent loop (up to 5 steps per turn)
- System prompt assembled fresh each turn from: base persona + business profile + taste summary + compressed older history + recent session summaries
- Tool-calling via OpenAI function-calling protocol (`generate()` schema)
- Defensive message normalization: orphan `tool` messages are dropped at history-window boundaries, `tool_calls` ids are always non-null, `arguments` always serialized to JSON string before hitting the API

### Memory System (`memory/memory.py`)
Three layers operating silently in the background:

| Layer | Mechanism |
|---|---|
| **In-session** | Last 15 turns verbatim + older turns folded into `Session.compressed_history` via LLM summarization (triggered at 30 turns) |
| **Cross-session** | After `POST /sessions/{id}/end`, a 2-3 sentence summary is written to `session_summaries`; the 5 most recent are injected into every new session's system prompt |
| **Taste profile** | After every generation, a background task calls the LLM to merge the new interaction into `UserTasteProfile.taste_summary` (cumulative, 2-sentence rolling update) |

### Generation Pipelines (`pipelines/router.py`)

| `output_type` | What it does |
|---|---|
| `image` | 1–9 parallel variants via Pollinations FLUX |
| `style_transfer` | Same as `image`, with `reference_image_url` + `reference_strength` |
| `poster` | 1 background image + PIL text compositing (4 layout options) |
| `story_sequence` | 3–8 sequential panels; each uses the previous panel as img2img reference for visual continuity |
| `vision_board` | 4/6/9 images composited into a grid (PIL) |
| `quote_card` | Pure PIL text rendering on a warm paper background, no image model involved |
| `before_after` | Fetches original + generates transformed; composites side-by-side grid |
| `video_loop` | **Placeholder** — returns a single still image until AnimateDiff/video backend is wired |

### Prompt Builder (`services/prompt_builder.py`)
Injects into every image prompt:
- User's `preferred_styles` and `preferred_colors` from taste profile
- Business `brand_tone` and `brand_colors` (business accounts)
- Quality booster: `high quality, detailed, 8k resolution, masterful composition`
- Default negative prompt + user-specified negatives + `disliked_styles` from taste profile

### Data Models (`models/models.py`)
`User` → `Session` → `Message` (monotonic `sequence` counter) → `Asset` (bundled by `bundle_id`) → `GenerationJob`

Plus: `UserTasteProfile`, `BusinessProfile`, `SessionSummary`

### API Surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/chat` | Main conversation endpoint; returns `reply` + optional `asset_bundle` |
| `POST` | `/api/v1/feedback` | Record variant selection; marks `Asset.selected`, updates taste profile |
| `POST` | `/api/v1/sessions/{id}/end` | Triggers end-of-session LLM summary |
| `POST` | `/api/v1/uploads` | Image upload; captions it with GPT-4.1-mini vision |
| `GET/PUT` | `/api/v1/users/{id}/taste-profile` | Read/write taste profile |
| `GET/PUT` | `/api/v1/users/{id}/business-profile` | Read/write business profile |
| `GET` | `/api/v1/assets/{id}` | Single asset |
| `POST` | `/api/v1/assets/{id}/save` | Mark asset permanently saved |
| `GET` | `/api/v1/users/{id}/assets` | List all or only saved assets |
| `GET` | `/health` | Returns `ok`, `image_backend`, `llm_model` |
| `GET` | `/storage/**` | Static file serving for generated images |

---

## Deployment Readiness Assessment

### ✅ Ready
- GitHub Models integration is fully wired and working
- Pollinations image backend requires no API key and is production-callable
- CORS is configurable via `FRONTEND_ORIGIN` (comma-separated for multiple origins)
- Background tasks (taste update, history compression) run without blocking the response
- Message sequence counter prevents ordering bugs on concurrent commits
- Frontend stores `user_id`/`session_id` in `localStorage` for session continuity
- Docker + `docker-compose.yml` present and functional
- Railway (`railway.json`, `Procfile`) and Vercel configs scaffolded

### 🚨 Must Fix Before Real Production

**1. Replace SQLite with PostgreSQL**

SQLite has no concurrent write safety and its file will be lost on Railway's ephemeral filesystem on every redeploy.

```bash
pip install asyncpg
```

```env
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/vizzy
```

No model changes needed — SQLAlchemy handles both.

**2. Replace local file storage**

Generated images at `./storage` are wiped on every Railway deploy. Use Supabase Storage, AWS S3, Cloudflare R2, or any compatible object store. Swap the `LocalStorage` class in `services/storage.py` — the interface is `save_bytes(data, suffix, subdir) -> (path, url)`.

**3. Set required environment variables**

```env
GITHUB_TOKEN=ghp_xxxxxxxxxxxx   # GitHub PAT with Models access
PUBLIC_BASE_URL=https://your-backend.up.railway.app
FRONTEND_ORIGIN=https://your-frontend.vercel.app
```

### ⚠️ Known Limitations (Not Blockers)

| Issue | Impact | Fix |
|---|---|---|
| No authentication | Any user can call any `user_id` endpoint | Add JWT/session auth layer |
| No rate limiting on `/chat` | API abuse possible | Add `slowapi` or Railway's built-in limits |
| `video_loop` is a still image | Users get a JPEG, not a video | Wire AnimateDiff or Replicate video model |
| Taste updates use extra LLM calls | Adds latency + GitHub Models quota | Make it truly background (separate worker) |
| `sequence` migration script needed | Existing DBs (before the column was added) need `scripts/migrate_add_sequence.py` | Run once on old DBs only |

---

## Quick Start (Local)

### 1. Get a GitHub PAT with Models Access

Go to [github.com/settings/tokens](https://github.com/settings/tokens) → Generate new token (classic) → enable `models:read` (or just use a fine-grained token with GitHub Models access).

### 2. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set GITHUB_TOKEN=ghp_xxx
uvicorn app.main:app --reload --port 8000
```

Health check: http://localhost:8000/health  
API docs: http://localhost:8000/docs

### 3. Frontend

```bash
cd frontend
cp .env.example .env        # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev
```

Open http://localhost:3000

### 4. Docker (full stack)

```bash
cp backend/.env.example backend/.env   # set GITHUB_TOKEN
cp frontend/.env.example frontend/.env
docker compose up --build
```

### 5. Smoke Test

```bash
# Requires the server running at localhost:8000
python scripts/test_live.py
```

This runs 4 tests: health check, turn-1 (clarifying question), turn-2 (more context), turn-3 (generate trigger), and validates DB message ordering invariants.

---

## Environment Variables Reference

### Backend (`.env`)

```env
# Required
GITHUB_TOKEN=ghp_xxxxxxxxxxxx

# LLM (defaults shown)
GITHUB_MODEL=gpt-4.1-mini
GITHUB_VISION_MODEL=gpt-4.1-mini

# Database
DATABASE_URL=sqlite+aiosqlite:///./vizzy.db

# Image generation
IMAGE_BACKEND=pollinations          # or: huggingface
HF_TOKEN=hf_xxxxx                   # only if IMAGE_BACKEND=huggingface

# Storage & URLs
STORAGE_DIR=./storage
PUBLIC_BASE_URL=http://localhost:8000

# CORS (comma-separated)
FRONTEND_ORIGIN=http://localhost:3000
```

### Frontend (`.env`)

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## Project Structure

```
backend/
  app/
    main.py                  # FastAPI app, CORS, StaticFiles, lifespan
    core/
      config.py              # Pydantic Settings — all env vars
      db.py                  # SQLAlchemy async engine + Base + init_db()
    models/
      models.py              # All ORM models (User, Session, Message, Asset, ...)
    services/
      conversation.py        # Agent loop: system prompt assembly, tool calling, DB persistence
      generate_tool.py       # generate() JSON schema (spec §5.2) + GenerateParams Pydantic model
      prompt_builder.py      # Taste/brand injection, quality boosters, negative prompts
      ollama_client.py       # GitHubModelsClient — chat, vision caption, text complete
      image_backend.py       # Pollinations & HuggingFace adapters
      storage.py             # LocalStorage — save_bytes / save_upload
    pipelines/
      router.py              # run_generation(): routes output_type → pipeline → AssetBundle
    memory/
      memory.py              # Session compression, taste update, cross-session summaries
    routers/
      chat.py                # /chat, /feedback, /sessions/{id}/end
      uploads.py             # /uploads (multipart image)
      profiles.py            # taste-profile, business-profile CRUD
      assets.py              # asset get/save/list
frontend/
  app/
    page.tsx                 # Main chat UI: message list, image grid, file upload
    layout.tsx               # Root layout + metadata
    globals.css              # Tailwind base + body color
  components/
    AssetGrid.tsx            # Responsive 1/2/3-col image grid with variant selection
  lib/
    api.ts                   # sendChat, uploadFile, sendFeedback typed fetch wrappers
scripts/
  e2e_test.py                # Async httpx smoke test
  test_live.py               # Sync urllib smoke test + DB sequence integrity check
  migrate_add_sequence.py    # One-shot SQLite migration for sequence column
docker-compose.yml
```

---

## Extending the System

### Swap to Postgres + Supabase Storage

```python
# services/storage.py — drop-in replacement skeleton
import httpx
from supabase import create_client

class SupabaseStorage:
    def save_bytes(self, data: bytes, suffix: str = ".jpg", subdir: str = "generated"):
        name = f"{uuid.uuid4().hex}{suffix}"
        client.storage.from_("vizzy").upload(f"{subdir}/{name}", data)
        url = client.storage.from_("vizzy").get_public_url(f"{subdir}/{name}")
        return name, url
```

### Add a New Output Type

1. Add the string literal to `OutputType` in `generate_tool.py`
2. Add it to the `"enum"` array in `GENERATE_TOOL_SCHEMA`
3. Add an `elif params.output_type == "your_type":` branch in `pipelines/router.py`
4. Return the standard `asset_records` list and set `bundle_type`

### Add Authentication

The session model is already user-scoped. Add a JWT middleware that validates a token and injects `user_id` — replace the `_get_or_create_user` logic in `routers/chat.py` with a lookup against the validated identity.

### Wire Real Video Generation

Replace the `video_loop` branch in `pipelines/router.py` with a call to Replicate's AnimateDiff API or a local ComfyUI endpoint. The `Asset.type = "video"` and `bundle_type = "video_loop"` fields are already in the schema.

---

## generate() Schema Reference

The LLM receives this exact tool definition. It is called only after creative direction is confirmed (enforced in the description).

```json
{
  "output_type": "image | poster | story_sequence | vision_board | video_loop | quote_card | style_transfer | before_after",
  "prompt": "naturalistic creative description",
  "style_tags": ["cinematic", "warm tones"],
  "negative_prompt": "blurry, watermark",
  "count": 3,
  "reference_image_url": "https://...",
  "reference_strength": 0.65,
  "aspect_ratio": "square | landscape | portrait",
  "output_size": "1024x1024",
  "sequence_count": 6,
  "poster_text": "Spring Collection 2025",
  "poster_layout": "hero_text_top | hero_text_bottom | split_text_right | minimal_center"
}
```

Required fields: `output_type`, `prompt`. Everything else has a default.

---

## AssetBundle Response Shape

Every generation returns this from `/api/v1/chat`:

```json
{
  "asset_bundle": {
    "bundle_id": "bnd_a1b2c3d4e5f6",
    "type": "image_grid",
    "assets": [
      { "id": "ast_...", "url": "https://backend/storage/generated/abc.jpg", "index": 1, "type": "image" },
      { "id": "ast_...", "url": "https://backend/storage/generated/def.jpg", "index": 2, "type": "image" },
      { "id": "ast_...", "url": "https://backend/storage/generated/ghi.jpg", "index": 3, "type": "image" }
    ],
    "prompt_used": "sunset over mountains, cinematic, warm tones, high quality, detailed, 8k resolution...",
    "negative_prompt_used": "blurry, low quality, oversaturated, watermark...",
    "actions": ["select", "download_all", "refine", "send_to_frame", "share", "save"]
  }
}
```
