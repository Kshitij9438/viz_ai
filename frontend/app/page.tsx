"use client";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { AssetGrid } from "@/components/AssetGrid";
import {
  Attachment,
  AssetBundle,
  login,
  register,
  sendChat,
  sendFeedback,
  uploadFile,
} from "@/lib/api";

type Msg = {
  role: "user" | "assistant";
  content: string;
  bundle?: AssetBundle | null;
  attachments?: Attachment[];
};

export default function Page() {
  const [messages, setMessages] = useState<Msg[]>([
    {
      role: "assistant",
      content:
        "Hi — I'm Vizzy. Tell me what you want to create today. You can describe an idea, share a photo, or just a feeling.",
    },
  ]);
  const [input, setInput] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [userId, setUserId] = useState<string | undefined>();
  const [token, setToken] = useState<string | null>(null);
  const [guestMode, setGuestMode] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setUserId(localStorage.getItem("vizzy_user_id") || undefined);
    setSessionId(localStorage.getItem("vizzy_session_id") || undefined);
    const storedToken = localStorage.getItem("vizzy_token");
    const storedGuest = localStorage.getItem("vizzy_guest_token");
    setToken(storedToken);
    setGuestMode(Boolean(storedGuest));
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    const attachments = pendingAttachments;
    setPendingAttachments([]);
    setMessages((m) => [...m, { role: "user", content: text, attachments }]);
    setBusy(true);
    try {
      const res = await sendChat({
        session_id: sessionId,
        message: text,
        attachments,
      });
      setUserId(res.user_id);
      setSessionId(res.session_id);
      localStorage.setItem("vizzy_user_id", res.user_id);
      localStorage.setItem("vizzy_session_id", res.session_id);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: res.reply, bundle: res.asset_bundle },
      ]);
    } catch (e: any) {
      setMessages((m) => [
        ...m,
        { role: "assistant", content: `Something went wrong: ${e.message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function handleFile(f: File) {
    setBusy(true);
    try {
      const att = await uploadFile(f);
      setPendingAttachments((a) => [...a, att]);
    } finally {
      setBusy(false);
    }
  }

  async function pickVariant(bundle: AssetBundle, variant: number) {
    if (!sessionId) return;
    await sendFeedback({
      session_id: sessionId,
      bundle_id: bundle.bundle_id,
      chosen_variant: variant,
    });
    setInput((s) => (s ? s : `I like number ${variant}.`));
  }

  async function handleAuth() {
    try {
      setAuthError("");
      const fn = authMode === "login" ? login : register;
      const res = await fn({ email: authEmail, password: authPassword });
      localStorage.setItem("vizzy_token", res.access_token);
      localStorage.setItem("vizzy_user_id", res.user_id);
      localStorage.removeItem("vizzy_guest_token");
      setToken(res.access_token);
      setUserId(res.user_id);
    } catch (e: any) {
      setAuthError(e.message);
    }
  }

  if (!token && !guestMode) {
    return (
      <main className="mx-auto flex h-screen max-w-3xl flex-col">
        <header className="border-b border-stone-200 px-6 py-4">
          <h1 className="text-xl font-semibold tracking-tight">Vizzy</h1>
          <p className="text-xs text-stone-500">conversational creative OS</p>
        </header>
        <div className="flex flex-1 items-center justify-center px-4">
          <div className="w-full max-w-md rounded-2xl border border-stone-200 p-6">
            <h2 className="mb-4 text-lg font-medium text-stone-900">
              {authMode === "login" ? "Login" : "Create account"}
            </h2>
            <div className="mb-3 flex gap-2">
              <button
                onClick={() => setAuthMode("login")}
                className={`rounded-xl px-3 py-1.5 text-sm ${authMode === "login" ? "bg-stone-900 text-white" : "border border-stone-300 text-stone-700"}`}
              >
                Login
              </button>
              <button
                onClick={() => setAuthMode("register")}
                className={`rounded-xl px-3 py-1.5 text-sm ${authMode === "register" ? "bg-stone-900 text-white" : "border border-stone-300 text-stone-700"}`}
              >
                Register
              </button>
            </div>
            <input
              type="email"
              value={authEmail}
              onChange={(e) => setAuthEmail(e.target.value)}
              placeholder="Email"
              className="mb-2 w-full rounded-xl border border-stone-300 px-3 py-2 outline-none focus:border-stone-500"
            />
            <input
              type="password"
              value={authPassword}
              onChange={(e) => setAuthPassword(e.target.value)}
              placeholder="Password"
              className="mb-2 w-full rounded-xl border border-stone-300 px-3 py-2 outline-none focus:border-stone-500"
            />
            {authError && <p className="mb-2 text-sm text-red-600">{authError}</p>}
            <button
              onClick={handleAuth}
              disabled={busy || !authEmail.trim() || !authPassword.trim()}
              className="rounded-xl bg-stone-900 px-4 py-2 text-white disabled:opacity-40"
            >
              {authMode === "login" ? "Login" : "Register"}
            </button>
            <button
              onClick={() => setGuestMode(true)}
              className="ml-2 rounded-xl border border-stone-300 px-4 py-2 text-sm hover:bg-stone-100"
            >
              Continue as guest
            </button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex h-screen max-w-3xl flex-col">
      <header className="flex items-center justify-between border-b border-stone-200 px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Vizzy</h1>
          <p className="text-xs text-stone-500">conversational creative OS</p>
        </div>
        <button
          onClick={() => {
            localStorage.removeItem("vizzy_token");
            localStorage.removeItem("vizzy_user_id");
            localStorage.removeItem("vizzy_guest_token");
            localStorage.removeItem("vizzy_session_id");
            setToken(null);
            setGuestMode(false);
          }}
          className="rounded-xl border border-stone-300 px-3 py-2 text-sm hover:bg-stone-100"
        >
          Logout
        </button>
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto px-6 py-6">
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
            <div
              className={
                m.role === "user"
                  ? "max-w-[85%] rounded-2xl bg-stone-900 px-4 py-2.5 text-white"
                  : "max-w-[90%]"
              }
            >
              {m.attachments?.map((a, j) => (
                <div key={j} className="mb-2">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={a.url} alt="" className="max-h-60 rounded-xl" />
                </div>
              ))}
              <div className="prose prose-stone prose-sm max-w-none">
                <ReactMarkdown>{m.content}</ReactMarkdown>
              </div>
              {m.bundle && (
                <AssetGrid
                  bundle={m.bundle}
                  onSelect={(v) => pickVariant(m.bundle!, v)}
                />
              )}
            </div>
          </div>
        ))}
        {busy && <div className="text-sm text-stone-500">Vizzy is thinking…</div>}
        <div ref={endRef} />
      </div>

      <div className="border-t border-stone-200 px-4 py-3">
        {pendingAttachments.length > 0 && (
          <div className="mb-2 flex gap-2">
            {pendingAttachments.map((a, i) => (
              // eslint-disable-next-line @next/next/no-img-element
              <img key={i} src={a.url} alt="" className="h-12 w-12 rounded-md object-cover" />
            ))}
          </div>
        )}
        <div className="flex items-end gap-2">
          <button
            onClick={() => fileRef.current?.click()}
            className="rounded-xl border border-stone-300 px-3 py-2 text-sm hover:bg-stone-100"
            disabled={busy}
          >
            +
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFile(f);
              e.target.value = "";
            }}
          />
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Describe what you want to create…"
            rows={1}
            className="flex-1 resize-none rounded-xl border border-stone-300 px-3 py-2 outline-none focus:border-stone-500"
          />
          <button
            onClick={send}
            disabled={busy || !input.trim()}
            className="rounded-xl bg-stone-900 px-4 py-2 text-white disabled:opacity-40"
          >
            Send
          </button>
        </div>
      </div>
    </main>
  );
}
