const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/* =========================
🧠 TYPES
========================= */

export type Attachment = { type: string; url: string; caption?: string };

export type Asset = { id: string; url: string; index: number; type: string };

export type AssetBundle = {
  bundle_id: string;
  type: string;
  assets: Asset[];
  prompt_used: string;
  actions: string[];
};

export type ChatResponse = {
  reply: string;
  asset_bundle: AssetBundle | null;
  tool_call: any | null;
  session_id: string;
  user_id: string;
  guest_token?: string | null;
};

/* =========================
🔐 STORAGE
========================= */

const STORAGE = {
  TOKEN: "vizzy_token",
  GUEST: "vizzy_guest_token",
  SESSION: "vizzy_session_id",
};

function get(key: string) {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(key);
}

function set(key: string, value: string) {
  if (typeof window !== "undefined") {
    localStorage.setItem(key, value);
  }
}

function remove(key: string) {
  if (typeof window !== "undefined") {
    localStorage.removeItem(key);
  }
}

function clearIdentity() {
  remove(STORAGE.TOKEN);
  remove(STORAGE.GUEST);
  remove(STORAGE.SESSION);
}

/* =========================
🔐 HEADERS
========================= */

function authHeaders(): Record<string, string> {
  const token = get(STORAGE.TOKEN);
  const guest = get(STORAGE.GUEST);

  if (token) {
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };
  }

  if (guest) {
    return {
      "Content-Type": "application/json",
      "X-Guest-Token": guest,
    };
  }

  return {
    "Content-Type": "application/json",
  };
}

/* =========================
🔐 AUTH
========================= */

export async function register(body: {
  email: string;
  password: string;
  name?: string;
}): Promise<{ access_token: string; user_id: string }> {
  const r = await fetch(`${API}/api/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!r.ok) throw new Error(await r.text());

  const data = await r.json();

  clearIdentity();
  set(STORAGE.TOKEN, data.access_token);

  window.location.reload();

  return data;
}

export async function login(body: {
  email: string;
  password: string;
}): Promise<{ access_token: string; user_id: string }> {
  const r = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!r.ok) throw new Error(await r.text());

  const data = await r.json();

  clearIdentity();
  set(STORAGE.TOKEN, data.access_token);

  window.location.reload();

  return data;
}

/* =========================
👤 GUEST (EXPLICIT ONLY)
========================= */

export async function createGuest(): Promise<{ guest_token: string }> {
  const r = await fetch(`${API}/api/v1/auth/guest`, {
    method: "POST",
  });

  if (!r.ok) throw new Error("guest failed");

  const data = await r.json();

  clearIdentity();
  set(STORAGE.GUEST, data.guest_token);

  window.location.reload();

  return data;
}

/* =========================
💬 CHAT
========================= */

export async function sendChat(body: {
  session_id?: string;
  message: string;
  attachments?: Attachment[];
}): Promise<ChatResponse> {
  const sessionId = get(STORAGE.SESSION);

  const r = await fetch(`${API}/api/v1/chat`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({
      ...body,
      session_id: sessionId || body.session_id,
    }),
  });

  if (r.status === 401) {
    clearIdentity();
    window.location.reload();
  }

  if (!r.ok) throw new Error(`chat failed: ${r.status}`);

  const data = await r.json();

  if (data?.session_id) {
    set(STORAGE.SESSION, data.session_id);
  }

  return data;
}

/* =========================
📂 UPLOAD
========================= */

export async function uploadFile(file: File): Promise<Attachment> {
  const fd = new FormData();
  fd.append("file", file);

  const headers: Record<string, string> = {};
  const token = get(STORAGE.TOKEN);
  const guest = get(STORAGE.GUEST);

  if (token) headers["Authorization"] = `Bearer ${token}`;
  else if (guest) headers["X-Guest-Token"] = guest;

  const r = await fetch(`${API}/api/v1/uploads`, {
    method: "POST",
    headers,
    body: fd,
  });

  if (!r.ok) throw new Error(`upload failed: ${r.status}`);

  return r.json();
}

/* =========================
👍 FEEDBACK (FIXED)
========================= */

export async function sendFeedback(body: {
  session_id?: string;
  bundle_id: string;
  chosen_variant?: number;
  feedback?: string;
}): Promise<void> {
  const sessionId = get(STORAGE.SESSION);

  const finalSessionId = body.session_id || sessionId;
  if (!finalSessionId) return;

  await fetch(`${API}/api/v1/feedback`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({
      ...body,
      session_id: finalSessionId,
    }),
  });
}