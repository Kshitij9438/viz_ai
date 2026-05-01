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
  const [guestToken, setGuestToken] = useState<string | null>(null);

  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState("");

  const fileRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // =========================
  // 🔥 INITIAL LOAD (FIXED)
  // =========================
  useEffect(() => {
    const storedToken = localStorage.getItem("vizzy_token");

    // ✅ ONLY restore real login
    if (storedToken) {
      setToken(storedToken);
    } else {
      setToken(null);
    }

    // ❌ DO NOT restore guest automatically
    setGuestToken(null);

    setUserId(localStorage.getItem("vizzy_user_id") || undefined);
    setSessionId(localStorage.getItem("vizzy_session_id") || undefined);
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  // =========================
  // 💬 CHAT
  // =========================
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
        { role: "assistant", content: `Error: ${e.message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  // =========================
  // 📎 FILE UPLOAD
  // =========================
  async function handleFile(f: File) {
    setBusy(true);
    try {
      const att = await uploadFile(f);
      setPendingAttachments((a) => [...a, att]);
    } finally {
      setBusy(false);
    }
  }

  // =========================
  // 👍 FEEDBACK
  // =========================
  async function pickVariant(bundle: AssetBundle, variant: number) {
    if (!sessionId) return;

    await sendFeedback({
      session_id: sessionId,
      bundle_id: bundle.bundle_id,
      chosen_variant: variant,
    });

    setInput((s) => (s ? s : `I like number ${variant}.`));
  }

  // =========================
  // 🔐 AUTH
  // =========================
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
      setGuestToken(null);
    } catch (e: any) {
      setAuthError(e.message);
    }
  }

  // =========================
  // 👤 GUEST (FIXED)
  // =========================
  async function handleGuest() {
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/api/v1/auth/guest`,
        { method: "POST" }
      );

      const data = await res.json();

      localStorage.setItem("vizzy_guest_token", data.guest_token);

      setGuestToken(data.guest_token);
      setToken(null);
    } catch {
      alert("Guest login failed");
    }
  }

  // =========================
  // 🚪 LOGOUT
  // =========================
  function logout() {
    localStorage.clear();
    setToken(null);
    setGuestToken(null);
    setUserId(undefined);
    setSessionId(undefined);
  }

  // =========================
  // 🔒 AUTH SCREEN
  // =========================
  if (!token && !guestToken) {
    return (
      <main className="mx-auto flex h-screen max-w-3xl flex-col">
        <header className="border-b border-stone-200 px-6 py-4">
          <h1 className="text-xl font-semibold">Vizzy</h1>
          <p className="text-xs text-stone-500">
            conversational creative OS
          </p>
        </header>

        <div className="flex flex-1 items-center justify-center px-4">
          <div className="w-full max-w-md rounded-2xl border p-6">
            <h2 className="mb-4 text-lg">
              {authMode === "login" ? "Login" : "Register"}
            </h2>

            <div className="mb-3 flex gap-2">
              <button onClick={() => setAuthMode("login")}>Login</button>
              <button onClick={() => setAuthMode("register")}>
                Register
              </button>
            </div>

            <input
              type="email"
              value={authEmail}
              onChange={(e) => setAuthEmail(e.target.value)}
              placeholder="Email"
              className="mb-2 w-full border px-3 py-2"
            />

            <input
              type="password"
              value={authPassword}
              onChange={(e) => setAuthPassword(e.target.value)}
              placeholder="Password"
              className="mb-2 w-full border px-3 py-2"
            />

            {authError && (
              <p className="mb-2 text-sm text-red-600">{authError}</p>
            )}

            <button onClick={handleAuth}>Submit</button>

            <button onClick={handleGuest} className="ml-2">
              Continue as Guest
            </button>
          </div>
        </div>
      </main>
    );
  }

  // =========================
  // 🧠 MAIN APP
  // =========================
  return (
    <main className="mx-auto flex h-screen max-w-3xl flex-col">
      <header className="flex justify-between border-b px-6 py-4">
        <h1>Vizzy</h1>
        <button onClick={logout}>Logout</button>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
        {messages.map((m, i) => (
          <div key={i}>
            <ReactMarkdown>{m.content}</ReactMarkdown>
            {m.bundle && (
              <AssetGrid
                bundle={m.bundle}
                onSelect={(v) => pickVariant(m.bundle!, v)}
              />
            )}
          </div>
        ))}
        {busy && <div>Thinking…</div>}
        <div ref={endRef} />
      </div>

      <div className="border-t px-4 py-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type..."
        />
        <button onClick={send}>Send</button>
      </div>
    </main>
  );
}