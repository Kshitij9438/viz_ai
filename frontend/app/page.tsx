"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";

import { AppShell } from "@/components/AppShell";
import { MultiOutputRenderer } from "@/components/MultiOutputRenderer";
import { Sidebar } from "@/components/Sidebar";
import {
  AssetBundle,
  Attachment,
  ChatMessage,
  SessionSummary,
  clearAuthState,
  createGuest,
  endSession,
  getSessionMessages,
  listSessions,
  login,
  persistAuthState,
  readAuthState,
  register,
  sendChat,
  sendFeedback,
  saveAsset,
  uploadFile,
} from "@/lib/api";

const WELCOME: ChatMessage = {
  role: "assistant",
  content:
    "Hi, I'm Vizzy. Tell me what you want to create today. You can describe an idea, share a photo, or start with a mood.",
};

export default function Page() {
  const [mounted, setMounted] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME]);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [guestToken, setGuestToken] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [pendingAttachments, setPendingAttachments] = useState<Attachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState("");

  const fileRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const activeLoadRef = useRef(0);
  const restoredSessionRef = useRef(false);
  const sessionIdRef = useRef<string | null>(null);

  const authenticated = Boolean(token || guestToken);

  const refreshSessions = useCallback(async () => {
    if (!readAuthState().token && !readAuthState().guestToken) return;
    setLoadingSessions(true);
    try {
      setSessions(await listSessions());
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  const loadSession = useCallback(async (nextSessionId: string) => {
    const loadId = activeLoadRef.current + 1;
    activeLoadRef.current = loadId;
    restoredSessionRef.current = true;
    setLoadingHistory(true);
    setSessionId(nextSessionId);
    persistAuthState({ sessionId: nextSessionId });

    try {
      const history = await getSessionMessages(nextSessionId);
      if (activeLoadRef.current === loadId) {
        setMessages(history.length ? history : [WELCOME]);
      }
    } catch (error: any) {
      if (activeLoadRef.current === loadId) {
        setMessages([{ role: "assistant", content: `Could not load that session: ${error.message}` }]);
      }
    } finally {
      if (activeLoadRef.current === loadId) setLoadingHistory(false);
    }
  }, []);

  useEffect(() => {
    const auth = readAuthState();
    setToken(auth.token);
    setGuestToken(auth.guestToken);
    setUserId(auth.userId);
    setSessionId(auth.sessionId);
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted || !authenticated) return;
    refreshSessions();
  }, [mounted, authenticated, refreshSessions]);

  useEffect(() => {
    if (!mounted || !authenticated || !sessionId || restoredSessionRef.current) return;
    restoredSessionRef.current = true;
    loadSession(sessionId);
  }, [mounted, authenticated, sessionId, loadSession]);

  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, busy, loadingHistory]);

  async function handleAuth(event: FormEvent) {
    event.preventDefault();
    setAuthError("");
    setBusy(true);
    try {
      const fn = authMode === "login" ? login : register;
      const res = await fn({ email: authEmail.trim(), password: authPassword });
      setToken(res.access_token);
      setGuestToken(null);
      setUserId(res.user_id);
      setSessionId(null);
      restoredSessionRef.current = false;
      setMessages([WELCOME]);
      await refreshSessions();
    } catch (error: any) {
      setAuthError(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleGuest() {
    setAuthError("");
    setBusy(true);
    try {
      const res = await createGuest();
      setToken(null);
      setGuestToken(res.guest_token);
      setUserId(res.user_id);
      setSessionId(null);
      restoredSessionRef.current = false;
      setMessages([WELCOME]);
      await refreshSessions();
    } catch (error: any) {
      setAuthError(error.message);
    } finally {
      setBusy(false);
    }
  }

  function logout() {
    clearAuthState();
    setToken(null);
    setGuestToken(null);
    setUserId(null);
    setSessionId(null);
    restoredSessionRef.current = false;
    setSessions([]);
    setMessages([WELCOME]);
    setInput("");
    setPendingAttachments([]);
  }

  async function newChat() {
    const currentSessionId = sessionIdRef.current;
    activeLoadRef.current += 1;
    setSessionId(null);
    restoredSessionRef.current = true;
    persistAuthState({ sessionId: null });
    setMessages([WELCOME]);
    setInput("");
    setPendingAttachments([]);
    setLoadingHistory(false);
    if (currentSessionId) {
      try {
        await endSession(currentSessionId);
      } finally {
        await refreshSessions();
      }
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || busy || loadingHistory) return;

    const currentSessionId = sessionId;
    const attachments = pendingAttachments;
    setInput("");
    setPendingAttachments([]);
    setBusy(true);
    setMessages((current) => [...current, { role: "user", content: text, attachments }]);

    try {
      const res = await sendChat({
        session_id: currentSessionId,
        message: text,
        attachments,
      });

      if (!currentSessionId || currentSessionId === sessionIdRef.current) {
        setUserId(res.user_id);
        setSessionId(res.session_id);
        setMessages((current) => [
          ...current,
          {
            role: "assistant",
            content: res.reply,
            bundle: res.asset_bundle,
            creative_output: res.creative_output,
          },
        ]);
      }

      await refreshSessions();
    } catch (error: any) {
      setMessages((current) => [
        ...current,
        { role: "assistant", content: `Error: ${error.message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function handleFile(file: File) {
    setBusy(true);
    try {
      const attachment = await uploadFile(file);
      setPendingAttachments((current) => [...current, attachment]);
    } catch (error: any) {
      setMessages((current) => [
        ...current,
        { role: "assistant", content: `Upload failed: ${error.message}` },
      ]);
    } finally {
      setBusy(false);
    }
  }

  async function pickVariant(bundle: AssetBundle, variant: number) {
    if (!sessionId) return;
    await sendFeedback({ session_id: sessionId, bundle_id: bundle.bundle_id, chosen_variant: variant });
    setInput((current) => current || `I like number ${variant}.`);
  }

  function refineBundle() {
    setInput("Refine the last image: ");
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  async function saveBundle(bundle: AssetBundle) {
    await Promise.all(bundle.assets.map((asset) => saveAsset(asset.id)));
  }

  if (!mounted) {
    return <main className="min-h-screen bg-stone-50" />;
  }

  if (!authenticated) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-stone-50 px-4 text-stone-900">
        <form
          onSubmit={handleAuth}
          className="w-full max-w-md rounded-xl border border-stone-200 bg-white p-6 shadow-sm"
        >
          <div className="mb-6">
            <h1 className="text-2xl font-semibold">Vizzy</h1>
            <p className="mt-1 text-sm text-stone-500">Sign in to your creative workspace.</p>
          </div>

          <div className="mb-4 grid grid-cols-2 gap-2 rounded-lg bg-stone-100 p-1">
            <button
              type="button"
              onClick={() => setAuthMode("login")}
              className={`rounded-md px-3 py-2 text-sm ${authMode === "login" ? "bg-white shadow-sm" : ""}`}
            >
              Login
            </button>
            <button
              type="button"
              onClick={() => setAuthMode("register")}
              className={`rounded-md px-3 py-2 text-sm ${authMode === "register" ? "bg-white shadow-sm" : ""}`}
            >
              Register
            </button>
          </div>

          <input
            type="email"
            value={authEmail}
            onChange={(event) => setAuthEmail(event.target.value)}
            placeholder="Email"
            className="mb-3 w-full rounded-lg border border-stone-200 px-3 py-2 outline-none focus:border-stone-400"
            required
          />
          <input
            type="password"
            value={authPassword}
            onChange={(event) => setAuthPassword(event.target.value)}
            placeholder="Password"
            className="mb-3 w-full rounded-lg border border-stone-200 px-3 py-2 outline-none focus:border-stone-400"
            required
          />

          {authError && <p className="mb-3 text-sm text-red-600">{authError}</p>}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-stone-900 px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-stone-800 disabled:opacity-60"
          >
            {busy ? "Please wait..." : authMode === "login" ? "Login" : "Create account"}
          </button>
          <button
            type="button"
            onClick={handleGuest}
            disabled={busy}
            className="mt-3 w-full rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-700 hover:bg-stone-100 disabled:opacity-60"
          >
            Continue as Guest
          </button>
        </form>
      </main>
    );
  }

  return (
    <AppShell
      sidebarCollapsed={sidebarCollapsed}
      sidebar={
        <Sidebar
          sessions={sessions}
          activeSessionId={sessionId}
          loading={loadingSessions}
          onNewChat={newChat}
          onSelectSession={loadSession}
          onLogout={logout}
          onCollapsedChange={setSidebarCollapsed}
        />
      }
    >
      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-stone-200 bg-white/90 px-4 py-3 shadow-sm backdrop-blur md:px-6">
        <div>
          <h1 className="text-lg font-semibold md:hidden">Vizzy</h1>
          <p className="hidden text-sm text-stone-500 md:block">
            {sessionId ? "Chat session" : "New chat"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={newChat}
            className="rounded-lg border border-stone-200 px-3 py-2 text-sm hover:bg-stone-100 md:hidden"
          >
            New
          </button>
          <Link
            href="/gallery"
            className="rounded-lg border border-stone-200 px-3 py-2 text-sm hover:bg-stone-100"
          >
            Gallery
          </Link>
          <Link
            href="/settings"
            className="rounded-lg border border-stone-200 px-3 py-2 text-sm hover:bg-stone-100"
          >
            Settings
          </Link>
          <button
            type="button"
            onClick={logout}
            className="rounded-lg border border-stone-200 px-3 py-2 text-sm hover:bg-stone-100 md:hidden"
          >
            Logout
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
        <div className="mx-auto max-w-3xl space-y-4">
          {messages.map((message, index) => (
            <div
              key={`${message.role}-${index}`}
              className={`rounded-xl border px-4 py-3 shadow-sm ${
                message.role === "user"
                  ? "ml-auto max-w-2xl border-stone-200 bg-white"
                  : "mr-auto max-w-3xl border-stone-200 bg-stone-100"
              }`}
            >
              <div className="prose prose-stone max-w-none text-sm">
                <ReactMarkdown>{message.content || " "}</ReactMarkdown>
              </div>

              {message.attachments && message.attachments.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {message.attachments.map((attachment) => (
                    <span
                      key={attachment.url}
                      className="rounded-md bg-stone-200 px-2 py-1 text-xs text-stone-600"
                    >
                      {attachment.caption || attachment.type}
                    </span>
                  ))}
                </div>
              )}

              <MultiOutputRenderer
                output={message.creative_output}
                fallbackBundle={message.bundle}
                onSelect={(bundle, variant) => pickVariant(bundle, variant)}
                onRefine={refineBundle}
                onSaveBundle={saveBundle}
              />
            </div>
          ))}

          {(busy || loadingHistory) && (
            <div className="mr-auto max-w-sm rounded-xl border border-stone-200 bg-stone-100 px-4 py-3 text-sm text-stone-500 shadow-sm">
              {loadingHistory ? "Loading history..." : "Thinking..."}
            </div>
          )}
          <div ref={endRef} />
        </div>
      </div>

      <footer className="border-t border-stone-200 bg-white/90 px-4 py-3 md:px-8">
        <div className="mx-auto max-w-3xl">
          {pendingAttachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {pendingAttachments.map((attachment) => (
                <span
                  key={attachment.url}
                  className="rounded-md border border-stone-200 bg-stone-50 px-2 py-1 text-xs text-stone-600"
                >
                  {attachment.caption || attachment.type}
                </span>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <input
              ref={fileRef}
              type="file"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) handleFile(file);
                event.currentTarget.value = "";
              }}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="rounded-lg border border-stone-200 px-3 py-2 text-sm hover:bg-stone-100"
            >
              Attach
            </button>
            <input
              ref={inputRef}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              placeholder="Type what you want to create..."
              className="min-w-0 flex-1 rounded-lg border border-stone-200 px-3 py-2 outline-none focus:border-stone-400"
            />
            <button
              type="button"
              onClick={send}
              disabled={busy || loadingHistory || !input.trim()}
              className="rounded-lg bg-stone-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-stone-800 disabled:opacity-60"
            >
              Send
            </button>
          </div>
        </div>
      </footer>
    </AppShell>
  );
}
