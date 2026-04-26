"""End-to-end smoke test. Requires Ollama running with llama3.1:8b pulled.

Run:
  python -m scripts.e2e_test
"""
from __future__ import annotations

import asyncio

import httpx

API = "http://localhost:8000"


async def main() -> None:
    async with httpx.AsyncClient(timeout=600) as c:
        r = await c.get(f"{API}/health"); r.raise_for_status()
        print("health:", r.json())

        r = await c.post(f"{API}/api/v1/chat", json={
            "message": "I want a dreamy painterly artwork for my living room. Warm and golden."
        })
        d = r.json(); print("turn 1:", d["reply"])
        sid, uid = d["session_id"], d["user_id"]

        r = await c.post(f"{API}/api/v1/chat", json={
            "session_id": sid, "user_id": uid,
            "message": "Yes, that sounds great. Make 3 variations.",
        })
        d = r.json(); print("turn 2:", d["reply"])
        if d.get("asset_bundle"):
            for a in d["asset_bundle"]["assets"]:
                print(" ->", a["url"])
        else:
            print("(no bundle yet — try one more turn)")


if __name__ == "__main__":
    asyncio.run(main())
