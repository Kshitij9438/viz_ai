const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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

export async function register(body: {
  email: string; password: string; name?: string;
}): Promise<{ access_token: string; user_id: string }> {
  const r = await fetch(`${API}/api/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export async function login(body: {
  email: string; password: string;
}): Promise<{ access_token: string; user_id: string }> {
  const r = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function authHeaders(): Record<string, string> {
  const token = typeof window !== "undefined"
    ? localStorage.getItem("vizzy_token")
    : null;
  const guestToken = typeof window !== "undefined"
    ? localStorage.getItem("vizzy_guest_token")
    : null;
  if (token) return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
  if (guestToken) return { "Content-Type": "application/json", "X-Guest-Token": guestToken };
  return { "Content-Type": "application/json" };
}

export async function sendChat(body: {
  session_id?: string;
  message: string;
  attachments?: Attachment[];
}): Promise<ChatResponse> {
  const r = await fetch(`${API}/api/v1/chat`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  if (r.status === 401) {
    localStorage.removeItem("vizzy_token");
    localStorage.removeItem("vizzy_guest_token");
    window.location.reload();
  }
  if (!r.ok) throw new Error(`chat failed: ${r.status}`);
  const data = await r.json();
  if (data?.guest_token) localStorage.setItem("vizzy_guest_token", data.guest_token);
  return data;
}

export async function uploadFile(file: File): Promise<Attachment> {
  const fd = new FormData();
  fd.append("file", file);
  const headers: Record<string, string> = {};
  const token = localStorage.getItem("vizzy_token");
  const guestToken = localStorage.getItem("vizzy_guest_token");
  if (token) headers["Authorization"] = `Bearer ${token}`;
  else if (guestToken) headers["X-Guest-Token"] = guestToken;
  const r = await fetch(`${API}/api/v1/uploads`, { method: "POST", headers, body: fd });
  if (!r.ok) throw new Error(`upload failed: ${r.status}`);
  return r.json();
}

export async function sendFeedback(body: {
  session_id: string;
  bundle_id: string;
  chosen_variant?: number;
  feedback?: string;
}): Promise<void> {
  await fetch(`${API}/api/v1/feedback`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
}
