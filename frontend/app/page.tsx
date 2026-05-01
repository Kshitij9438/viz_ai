"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

import { AssetGrid } from "@/components/AssetGrid";
import { useSession } from "@/components/SessionContext";

import {
  Attachment,
  AssetBundle,
  login,
  register,
  sendChat,
  getSessionMessages,
} from "@/lib/api";

type Msg = {
  role: "user" | "assistant";
  content: string;
  bundle?: AssetBundle | null;
  attachments?: Attachment[];
};

export default function Page() {
  const { sessionId, setSessionId } = useSession();

  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  const [busy, setBusy] = useState(false);

  const [token, setToken] = useState<string | null>(null);
  const [guestToken, setGuestToken] = useState<string | null>(null);

  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState("");

  const endRef = useRef<HTMLDivElement>(null);

  // =========================
  // INIT AUTH + SESSION
  // =========================
  useEffect(() => {
    const t = localStorage.getItem("vizzy_token");
    const g = localStorage.getItem("vizzy_guest_token");

    setToken(t);
    setGuestToken(g);

    const saved = localStorage.getItem("vizzy_session_id");
    if (saved && saved !== "null") {
      setSessionId(saved);
    }
  }, []);

  // =========================
  // AUTO SCROLL
  // =========================
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // =========================
  // 🔥 SESSION HANDLER (FIXED)
  // =========================
  useEffect(() => {
    // 🧠 NEW CHAT
    if (!sessionId) {
      setMessages([
        {
          role: "assistant",
          content: "Hi — I'm Vizzy. What would you like to create?",
        },
      ]);
      return;
    }

    // 🧠 EXISTING SESSION
    const sid: string = sessionId; // ✅ FIX TS ERROR

    async function loadMessages() {
      try {
        setBusy(true);

        const data = await getSessionMessages(sid);

        setMessages(data || []);
      } catch (e) {
        console.error("Failed to load session messages", e);
      } finally {
        setBusy(false);
      }
    }

    loadMessages();
  }, [sessionId]);

  // =========================
  // CHAT SEND
  // =========================
  async function send() {
    const text = input.trim();
    if (!text || busy) return;

    setInput("");

    const attachments = pendingAttachments;
    setPendingAttachments([]);

    setMessages((prev) => [
      ...prev,
      { role: "user", content: text, attachments },
    ]);

    setBusy(true);

    try {
      const res = await sendChat({
        session_id: sessionId || undefined,
        message: text,
        attachments,
      });

      // 🔥 sync session safely
      if (!sessionId || sessionId !== res.session_id) {
        setSessionId(res.session_id);
        localStorage.setItem("vizzy_session_id", res.session_id);
      }

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: res.reply || "(empty response)",
          bundle: res.asset_bundle,
        },
      ]);
    } catch (e: any) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Error: ${e.message}`,
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  // =========================
  // AUTH
  // =========================
  async function handleAuth() {
    try {
      setAuthError("");

      const fn = authMode === "login" ? login : register;
      const res = await fn({ email: authEmail, password: authPassword });

      localStorage.setItem("vizzy_token", res.access_token);
      localStorage.setItem("vizzy_user_id", res.user_id);

      setToken(res.access_token);
      setGuestToken(null);
    } catch (e: any) {
      setAuthError(e.message);
    }
  }

  async function handleGuest() {
    const res = await fetch(
      `${process.env.NEXT_PUBLIC_API_URL}/api/v1/auth/guest`,
      { method: "POST" }
    );

    const data = await res.json();

    localStorage.setItem("vizzy_guest_token", data.guest_token);
    localStorage.setItem("vizzy_user_id", data.user_id);

    setGuestToken(data.guest_token);
    setToken(null);
  }

  function logout() {
    localStorage.clear();
    setToken(null);
    setGuestToken(null);
    setSessionId(null);
    setMessages([]);
  }

  // =========================
  // AUTH SCREEN
  // =========================
  if (!token && !guestToken) {
    return (
      <main className="flex h-screen items-center justify-center bg-gray-100">
        <div className="w-full max-w-md rounded-xl border bg-white p-6 shadow">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold">
              {authMode === "login" ? "Login" : "Register"}
            </h2>

            <button
              className="text-sm text-blue-600"
              onClick={() =>
                setAuthMode(authMode === "login" ? "register" : "login")
              }
            >
              {authMode === "login" ? "Register" : "Login"}
            </button>
          </div>

          <input
            className="mb-2 w-full border p-2 rounded"
            value={authEmail}
            onChange={(e) => setAuthEmail(e.target.value)}
            placeholder="Email"
          />

          <input
            className="mb-2 w-full border p-2 rounded"
            value={authPassword}
            onChange={(e) => setAuthPassword(e.target.value)}
            placeholder="Password"
          />

          {authError && <p className="text-red-500">{authError}</p>}

          <button
            className="mt-2 w-full bg-black text-white p-2 rounded"
            onClick={handleAuth}
          >
            Submit
          </button>

          <button
            className="mt-2 w-full border p-2 rounded"
            onClick={handleGuest}
          >
            Continue as Guest
          </button>
        </div>
      </main>
    );
  }

  // =========================
  // MAIN UI
  // =========================
  return (
    <main className="flex h-screen flex-col bg-gray-100">
      <header className="flex justify-between border-b bg-white px-6 py-4">
        <h1 className="font-semibold">Vizzy</h1>
        <button onClick={logout}>Logout</button>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4">
        {messages.map((m, i) => (
          <div
            key={i}
            className={`p-4 rounded-xl shadow ${
              m.role === "user"
                ? "bg-black text-white ml-auto max-w-xl"
                : "bg-white max-w-xl"
            }`}
          >
            <ReactMarkdown>{m.content}</ReactMarkdown>

            {m.bundle && (
              <AssetGrid bundle={m.bundle} onSelect={() => {}} />
            )}
          </div>
        ))}

        {busy && <div className="text-sm text-gray-500">Loading…</div>}

        <div ref={endRef} />
      </div>

      <div className="border-t bg-white px-4 py-3 flex gap-2">
        <input
          className="flex-1 border p-2 rounded"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
          placeholder="Type..."
        />

        <button
          className="bg-black text-white px-4 rounded"
          onClick={send}
        >
          Send
        </button>
      </div>
    </main>
  );
}