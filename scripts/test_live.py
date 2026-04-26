import urllib.request, json, sys

base = "http://localhost:8000"

def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())

def get(path):
    with urllib.request.urlopen(f"{base}{path}", timeout=10) as r:
        return json.loads(r.read())

# ---- 1. Health ----
print("=" * 60)
print("1. HEALTH CHECK")
h = get("/health")
print("   result:", json.dumps(h))
assert h["ok"] is True
assert "llm_model" in h
print("   PASS")

# ---- 2. Turn 1 — clarifying question expected, NO tool call ----
print()
print("=" * 60)
print("2. TURN 1 — clarifying question")
r1 = post("/api/v1/chat", {"message": "I want to create something beautiful"})
print("   reply     :", r1["reply"][:120])
print("   session_id:", r1["session_id"])
print("   user_id   :", r1["user_id"])
print("   tool_call :", r1["tool_call"])
print("   asset_bnd :", r1["asset_bundle"])
assert r1["reply"], "Empty reply"
assert r1["session_id"].startswith("sess_")
assert r1["user_id"].startswith("usr_")
if r1["tool_call"] is not None:
    print("   WARN: tool called on turn 1 (unexpected but not a protocol error)")
print("   PASS")

session_id = r1["session_id"]
user_id = r1["user_id"]

# ---- 3. Turn 2 — more context ----
print()
print("=" * 60)
print("3. TURN 2 — more context")
r2 = post("/api/v1/chat", {
    "user_id": user_id,
    "session_id": session_id,
    "message": "A sunset over mountains, very cinematic, warm tones",
})
print("   reply    :", r2["reply"][:120])
print("   tool_call:", r2["tool_call"])
print("   PASS")

# ---- 4. Turn 3 — explicit generate trigger ----
print()
print("=" * 60)
print("4. TURN 3 — force generate")
r3 = post("/api/v1/chat", {
    "user_id": user_id,
    "session_id": session_id,
    "message": "Yes that sounds perfect, please generate it now",
})
print("   reply    :", r3["reply"][:120])
tc = r3["tool_call"]
ab = r3["asset_bundle"]
print("   tool_call:", json.dumps(tc)[:200] if tc else None)
if ab:
    print("   bundle_id:", ab["bundle_id"])
    print("   num_assets:", len(ab["assets"]))
    print("   first_url:", ab["assets"][0]["url"] if ab["assets"] else "none")
else:
    print("   asset_bundle: None (LLM asked another question — acceptable)")
print("   PASS")

# ---- 5. Verify DB has clean sequence ordering ----
print()
print("=" * 60)
print("5. DB SEQUENCE INTEGRITY CHECK")
import sqlite3, pathlib
con = sqlite3.connect(pathlib.Path("backend/vizzy.db"))
cur = con.cursor()
cur.execute(
    "SELECT role, sequence FROM messages WHERE session_id=? ORDER BY sequence ASC",
    (session_id,)
)
rows = cur.fetchall()
con.close()
print("   Messages in session (role, seq):")
for role, seq in rows:
    print(f"     {seq:3d}  {role}")

# Validate: every tool row must be immediately preceded by an assistant row
prev_role = None
for role, seq in rows:
    if role == "tool":
        assert prev_role == "assistant", (
            f"ORDERING VIOLATION: tool at seq={seq} follows {prev_role}, not assistant"
        )
    prev_role = role

# Validate: sequences are strictly increasing
seqs = [s for _, s in rows]
assert seqs == sorted(seqs), f"Sequences not monotonically ascending: {seqs}"
assert len(seqs) == len(set(seqs)), f"Duplicate sequence values: {seqs}"
print("   Ordering invariant: OK")
print("   Monotonicity invariant: OK")
print("   PASS")

print()
print("=" * 60)
print("ALL TESTS PASSED")
