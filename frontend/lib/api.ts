function apiBaseUrl() {
  const configured = process.env.NEXT_PUBLIC_API_URL?.trim().replace(/\/$/, "");
  if (configured) return configured;
  if (process.env.NODE_ENV !== "production") return "http://localhost:8000";
  return "";
}

const API = apiBaseUrl();
const REQUEST_TIMEOUT_MS = 90000;

export const STORAGE_KEYS = {
  TOKEN: "vizzy_token",
  GUEST: "vizzy_guest_token",
  USER: "vizzy_user_id",
  SESSION: "vizzy_session_id",
} as const;

export type Attachment = { type: string; url: string; caption?: string };

export type Asset = {
  id: string;
  session_id?: string;
  url: string;
  index: number;
  type: string;
  prompt?: string;
  selected?: boolean;
  saved_permanently?: boolean;
  variant_index?: number;
  bundle_id?: string | null;
  created_at?: string | null;
};

export type AssetBundle = {
  bundle_id: string;
  type: string;
  assets: Asset[];
  prompt_used: string;
  actions: string[];
};

export type CreativeOutputItem =
  | { kind: "asset_bundle"; bundle: AssetBundle }
  | { kind: "story"; title?: string; logline?: string; scenes?: Array<{ title?: string; description?: string; visual_prompt?: string }> }
  | { kind: "campaign_brief"; campaign_name?: string; positioning?: string; headlines?: string[]; captions?: string[]; visual_direction?: string; poster_text?: string }
  | Record<string, any>;

export type CreativeOutput = {
  type: "story" | "image" | "campaign" | "moodboard" | "video" | "chat" | string;
  outputs: CreativeOutputItem[];
  metadata: Record<string, any>;
  actions?: string[];
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  bundle?: AssetBundle | null;
  creative_output?: CreativeOutput | null;
  attachments?: Attachment[];
};

export type ChatResponse = {
  reply: string;
  asset_bundle?: AssetBundle | null;
  creative_output?: CreativeOutput | null;
  intent?: Record<string, any> | null;
  tool_call?: unknown | null;
  session_id: string;
  user_id: string;
  guest_token?: string | null;
  job_id?: string | null;
  job_status?: string | null;
};

/** Shape of `result` on GET /api/v1/jobs/{id} when status is `done` (matches worker payload). */
export type JobResultPayload = {
  reply?: string;
  asset_bundle?: AssetBundle | null;
  creative_output?: CreativeOutput | null;
  intent?: Record<string, any> | null;
  tool_call?: unknown | null;
};

export type JobStatusResponse = {
  job_id: string;
  status: string;
  result?: JobResultPayload | null;
  error?: string | null;
  retry_after?: number | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
};

export type SessionSummary = {
  id: string;
  title: string;
  preview?: string;
  message_count: number;
  updated_at: string | null;
  started_at: string | null;
  status: string;
};

export type TasteProfile = {
  user_id: string;
  taste_summary: string;
  preferred_styles: string[];
  preferred_colors: string[];
  disliked_styles: string[];
  generation_count: number;
  last_updated: string | null;
};

export type BusinessProfile = {
  id?: string;
  user_id?: string;
  business_name: string;
  business_type?: string | null;
  sub_type?: string | null;
  location?: string | null;
  brand_tone?: string | null;
  brand_colors?: Record<string, string> | null;
  logo_url?: string | null;
  font_preference?: string | null;
  goals?: string[];
  disallowed_themes?: string[];
};

export type AuthState = {
  token: string | null;
  guestToken: string | null;
  userId: string | null;
  sessionId: string | null;
};

function storageGet(key: string) {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(key);
}

function storageSet(key: string, value: string | null | undefined) {
  if (typeof window === "undefined") return;
  if (value) localStorage.setItem(key, value);
  else localStorage.removeItem(key);
}

export function readAuthState(): AuthState {
  return {
    token: storageGet(STORAGE_KEYS.TOKEN),
    guestToken: storageGet(STORAGE_KEYS.GUEST),
    userId: storageGet(STORAGE_KEYS.USER),
    sessionId: storageGet(STORAGE_KEYS.SESSION),
  };
}

export function persistAuthState(next: Partial<AuthState>) {
  if ("token" in next) storageSet(STORAGE_KEYS.TOKEN, next.token);
  if ("guestToken" in next) storageSet(STORAGE_KEYS.GUEST, next.guestToken);
  if ("userId" in next) storageSet(STORAGE_KEYS.USER, next.userId);
  if ("sessionId" in next) storageSet(STORAGE_KEYS.SESSION, next.sessionId);
}

export function clearAuthState() {
  persistAuthState({ token: null, guestToken: null, userId: null, sessionId: null });
}

function normalizeAssetUrl(url: string | null | undefined) {
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  if (url.startsWith("data:")) return url;

  const apiBase = API.replace(/\/$/, "");
  if (url.startsWith("/")) return `${apiBase}${url}`;
  return `${apiBase}/${url.replace(/^\/+/, "")}`;
}

function normalizeBundle(bundle: AssetBundle | null | undefined): AssetBundle | null {
  if (!bundle) return null;
  return {
    ...bundle,
    assets: (bundle.assets || []).map((asset) => ({
      ...asset,
      url: normalizeAssetUrl(asset.url),
    })),
  };
}

function normalizeCreativeOutput(output: CreativeOutput | null | undefined): CreativeOutput | null {
  if (!output) return null;
  return {
    ...output,
    outputs: (output.outputs || []).map((item: any) => {
      if (item?.kind === "asset_bundle") {
        return { ...item, bundle: normalizeBundle(item.bundle) };
      }
      return item;
    }),
  };
}

function authHeaders(): Record<string, string> {
  const { token, guestToken } = readAuthState();
  if (token) return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
  if (guestToken) return { "Content-Type": "application/json", "X-Guest-Token": guestToken };
  return { "Content-Type": "application/json" };
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 401) {
    clearAuthState();
    throw new Error("Your session expired. Please sign in again.");
  }

  if (!response.ok) {
    let detail = `Request failed: ${response.status}`;
    try {
      const body = await response.json();
      detail = body.detail || body.message || detail;
    } catch {
      detail = await response.text() || detail;
    }
    throw new Error(detail);
  }

  return response.json() as Promise<T>;
}

async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  if (!API) {
    throw new Error("NEXT_PUBLIC_API_URL is not configured.");
  }
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(input, {
      ...init,
      signal: controller.signal,
    });
  } catch (error: any) {
    if (error?.name === "AbortError") {
      throw new Error("Request timed out. Please try again.");
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

export async function register(body: {
  email: string;
  password: string;
  name?: string;
}): Promise<{ access_token: string; user_id: string }> {
  const data = await parseResponse<{ access_token: string; user_id: string }>(
    await apiFetch(`${API}/api/v1/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  clearAuthState();
  persistAuthState({ token: data.access_token, userId: data.user_id });
  return data;
}

export async function login(body: {
  email: string;
  password: string;
}): Promise<{ access_token: string; user_id: string }> {
  const data = await parseResponse<{ access_token: string; user_id: string }>(
    await apiFetch(`${API}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  clearAuthState();
  persistAuthState({ token: data.access_token, userId: data.user_id });
  return data;
}

export async function createGuest(): Promise<{ guest_token: string; user_id: string }> {
  const data = await parseResponse<{ guest_token: string; user_id: string }>(
    await apiFetch(`${API}/api/v1/auth/guest`, { method: "POST" }),
  );
  clearAuthState();
  persistAuthState({ guestToken: data.guest_token, userId: data.user_id });
  return data;
}

export async function listSessions(): Promise<SessionSummary[]> {
  return parseResponse<SessionSummary[]>(
    await apiFetch(`${API}/api/v1/sessions`, { headers: authHeaders() }),
  );
}

export async function endSession(sessionId: string): Promise<void> {
  await parseResponse<{ ok: boolean }>(
    await apiFetch(`${API}/api/v1/sessions/${sessionId}/end`, {
      method: "POST",
      headers: authHeaders(),
    }),
  );
}

export async function getSessionMessages(sessionId: string): Promise<ChatMessage[]> {
  const data = await parseResponse<ChatMessage[]>(
    await apiFetch(`${API}/api/v1/sessions/${sessionId}/messages`, { headers: authHeaders() }),
  );
  return data.map((message) => ({
    ...message,
    bundle: normalizeBundle(message.bundle),
    creative_output: normalizeCreativeOutput(message.creative_output),
  }));
}

export async function sendChat(body: {
  session_id?: string | null;
  message: string;
  attachments?: Attachment[];
}): Promise<ChatResponse> {
  const data = await parseResponse<ChatResponse>(
    await apiFetch(`${API}/api/v1/chat`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({
        session_id: body.session_id || undefined,
        message: body.message,
        attachments: body.attachments || [],
      }),
    }),
  );

  persistAuthState({ userId: data.user_id, sessionId: data.session_id });
  if (data.guest_token) persistAuthState({ guestToken: data.guest_token });

  return {
    ...data,
    asset_bundle: normalizeBundle(data.asset_bundle),
    creative_output: normalizeCreativeOutput(data.creative_output),
  };
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  return parseResponse<JobStatusResponse>(
    await apiFetch(`${API}/api/v1/jobs/${encodeURIComponent(jobId)}`, {
      headers: authHeaders(),
    }),
  );
}

/** Normalize `asset_bundle` from a completed job's `result` (not top-level `response.asset_bundle`). */
export function bundleFromJobResult(result: JobResultPayload | null | undefined): AssetBundle | null {
  if (!result) return null;
  return normalizeBundle(result.asset_bundle);
}

export function creativeOutputFromJobResult(result: JobResultPayload | null | undefined): CreativeOutput | null {
  if (!result) return null;
  return normalizeCreativeOutput(result.creative_output);
}

export async function uploadFile(file: File): Promise<Attachment> {
  const fd = new FormData();
  fd.append("file", file);

  const { token, guestToken } = readAuthState();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  else if (guestToken) headers["X-Guest-Token"] = guestToken;

  const data = await parseResponse<Attachment>(
    await apiFetch(`${API}/api/v1/uploads`, { method: "POST", headers, body: fd }),
  );

  return { ...data, url: normalizeAssetUrl(data.url) };
}

export async function sendFeedback(body: {
  session_id?: string | null;
  bundle_id: string;
  chosen_variant?: number;
  feedback?: string;
}): Promise<void> {
  const sessionId = body.session_id || readAuthState().sessionId;
  if (!sessionId) return;

  await parseResponse<{ ok: boolean }>(
    await apiFetch(`${API}/api/v1/feedback`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ ...body, session_id: sessionId }),
    }),
  );
}

export async function saveAsset(assetId: string): Promise<void> {
  await parseResponse<{ ok: boolean }>(
    await apiFetch(`${API}/api/v1/assets/${assetId}/save`, {
      method: "POST",
      headers: authHeaders(),
    }),
  );
}

export async function listAssets(userId: string, saved = false): Promise<Asset[]> {
  const data = await parseResponse<Asset[]>(
    await apiFetch(`${API}/api/v1/users/${userId}/assets?saved=${saved ? "true" : "false"}`, {
      headers: authHeaders(),
    }),
  );
  return data.map((asset) => ({ ...asset, url: normalizeAssetUrl(asset.url) }));
}

export async function getTasteProfile(userId: string): Promise<TasteProfile> {
  return parseResponse<TasteProfile>(
    await apiFetch(`${API}/api/v1/users/${userId}/taste-profile`, { headers: authHeaders() }),
  );
}

export async function getBusinessProfile(userId: string): Promise<BusinessProfile | null> {
  try {
    const data = await parseResponse<BusinessProfile>(
      await apiFetch(`${API}/api/v1/users/${userId}/business-profile`, { headers: authHeaders() }),
    );
    return { ...data, logo_url: normalizeAssetUrl(data.logo_url) };
  } catch (error: any) {
    if (String(error.message || "").toLowerCase().includes("no business profile")) return null;
    throw error;
  }
}

export async function saveBusinessProfile(
  userId: string,
  body: BusinessProfile,
): Promise<void> {
  await parseResponse<{ ok: boolean }>(
    await apiFetch(`${API}/api/v1/users/${userId}/business-profile`, {
      method: "PUT",
      headers: authHeaders(),
      body: JSON.stringify(body),
    }),
  );
}
