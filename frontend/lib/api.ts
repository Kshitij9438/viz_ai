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
};

export async function sendChat(body: {
  user_id?: string;
  session_id?: string;
  message: string;
  attachments?: Attachment[];
}): Promise<ChatResponse> {
  const r = await fetch(`${API}/api/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`chat failed: ${r.status}`);
  return r.json();
}

export async function uploadFile(file: File): Promise<Attachment> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API}/api/v1/uploads`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`upload failed: ${r.status}`);
  return r.json();
}

export async function sendFeedback(body: {
  user_id: string;
  session_id: string;
  bundle_id: string;
  chosen_variant?: number;
  feedback?: string;
}): Promise<void> {
  await fetch(`${API}/api/v1/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
