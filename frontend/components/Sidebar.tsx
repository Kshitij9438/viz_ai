"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { SessionSummary } from "@/lib/api";

export function Sidebar({
  sessions,
  activeSessionId,
  loading,
  onNewChat,
  onSelectSession,
  onLogout,
  onCollapsedChange,
}: {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  loading?: boolean;
  onNewChat: () => void;
  onSelectSession: (sessionId: string) => void;
  onLogout: () => void;
  onCollapsedChange?: (collapsed: boolean) => void;
}) {
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem("vizzy_sidebar_collapsed") === "true";
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem("vizzy_sidebar_collapsed", collapsed ? "true" : "false");
    onCollapsedChange?.(collapsed);
  }, [collapsed, onCollapsedChange]);

  return (
    <aside
      className={`fixed inset-y-0 left-0 z-30 hidden border-r border-stone-200 bg-stone-100 px-3 py-3 shadow-sm transition-all md:flex md:flex-col ${
        collapsed ? "w-16" : "w-72"
      }`}
    >
      <div className="mb-3 flex h-11 items-center justify-between px-1">
        {!collapsed && (
          <div>
            <h1 className="text-lg font-semibold tracking-normal">Vizzy</h1>
            <p className="text-xs text-stone-500">Creative workspace</p>
          </div>
        )}
        <button
          type="button"
          onClick={() => setCollapsed((value) => !value)}
          className="ml-auto flex h-9 w-9 items-center justify-center rounded-lg text-sm text-stone-600 hover:bg-white"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? ">" : "<"}
        </button>
      </div>

      <button
        type="button"
        onClick={onNewChat}
        className="mb-2 flex h-10 w-full items-center rounded-lg px-3 text-left text-sm font-medium text-stone-800 transition hover:bg-white"
        title="New Chat"
      >
        <span className="inline-block w-6">+</span>
        {!collapsed && <span>New Chat</span>}
      </button>

      <Link
        href="/gallery"
        className="flex h-10 items-center rounded-lg px-3 text-sm text-stone-700 transition hover:bg-white"
        title="Gallery"
      >
        <span className="inline-block w-6">G</span>
        {!collapsed && <span>Gallery</span>}
      </Link>

      <Link
        href="/settings"
        className="mb-4 flex h-10 items-center rounded-lg px-3 text-sm text-stone-700 transition hover:bg-white"
        title="Settings"
      >
        <span className="inline-block w-6">S</span>
        {!collapsed && <span>Settings</span>}
      </Link>

      {!collapsed && (
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-medium uppercase text-stone-500">Sessions</p>
          {loading && <span className="text-xs text-stone-400">Loading</span>}
        </div>
      )}

      <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
        {sessions.length === 0 && !loading && !collapsed ? (
          <p className="rounded-lg border border-dashed border-stone-200 px-3 py-4 text-sm text-stone-500">
            No sessions yet.
          </p>
        ) : (
          sessions.map((session) => {
            const active = session.id === activeSessionId;
            return (
              <button
                key={session.id}
                type="button"
                onClick={() => onSelectSession(session.id)}
                title={session.title}
                className={`w-full rounded-lg px-3 py-2 text-left text-sm transition ${
                  active ? "bg-white text-stone-950 shadow-sm" : "text-stone-700 hover:bg-white"
                }`}
              >
                {collapsed ? (
                  <span className="block text-center font-medium">{session.title.slice(0, 1) || "C"}</span>
                ) : (
                  <>
                    <span className="block truncate font-medium">{session.title}</span>
                    <span className={`block truncate text-xs ${active ? "text-stone-500" : "text-stone-400"}`}>
                      {session.preview || `${session.message_count} messages`}
                    </span>
                  </>
                )}
              </button>
            );
          })
        )}
      </nav>

      <button
        type="button"
        onClick={onLogout}
        className="mt-4 flex h-10 items-center rounded-lg px-3 text-left text-sm text-stone-700 transition hover:bg-white"
        title="Logout"
      >
        <span className="inline-block w-6">L</span>
        {!collapsed && <span>Logout</span>}
      </button>
    </aside>
  );
}
