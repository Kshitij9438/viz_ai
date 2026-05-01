"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { getUserSessions, clearSession } from "@/lib/api";
import { useSession } from "./SessionContext";

type Session = {
    session_id: string;
    created_at: string;
    preview: string;
    status: string;
};

export default function Sidebar({
    onSelectSession,
}: {
    onSelectSession: (id: string | null) => void;
}) {
    const router = useRouter();

    const [sessions, setSessions] = useState<Session[]>([]);
    const [loading, setLoading] = useState(true);

    const { sessionId, setSessionId } = useSession();

    async function loadSessions() {
        try {
            setLoading(true);
            const data = await getUserSessions();
            setSessions(data || []);
        } catch (err) {
            console.error("Failed to load sessions", err);
        } finally {
            setLoading(false);
        }
    }

    // Initial load
    useEffect(() => {
        loadSessions();
    }, []);

    // Reload when a NEW session is created
    useEffect(() => {
        if (sessionId) {
            loadSessions();
        }
    }, [sessionId]);

    function handleSelect(id: string | null) {
        setSessionId(id);
        onSelectSession(id);

        if (id) {
            localStorage.setItem("vizzy_session_id", id);
        } else {
            localStorage.removeItem("vizzy_session_id");
        }

        // 🔥 Ensure we are on chat page
        router.push("/");
    }

    function handleNewChat() {
        // 🔥 Clear everything
        clearSession();
        localStorage.removeItem("vizzy_session_id");

        // Reset context
        setSessionId(null);

        // Notify parent
        onSelectSession(null);

        // 🔥 FORCE clean UI
        router.push("/");

        // Small refresh for sidebar consistency
        setTimeout(() => {
            loadSessions();
        }, 100);
    }

    return (
        <div className="w-72 h-screen border-r bg-white flex flex-col">
            {/* Header */}
            <div className="p-4 border-b">
                <h2 className="text-lg font-semibold">Vizzy</h2>
            </div>

            {/* Actions */}
            <div className="p-4 space-y-2">
                <button
                    className="w-full p-3 bg-black text-white rounded hover:opacity-90"
                    onClick={handleNewChat}
                >
                    + New Chat
                </button>

                <button
                    className="w-full p-3 border rounded hover:bg-gray-50"
                    onClick={() => router.push("/gallery")}
                >
                    🎨 Gallery
                </button>
            </div>

            {/* Sessions */}
            <div className="flex-1 overflow-y-auto px-2 pb-4">
                {loading && (
                    <p className="text-sm text-gray-400 px-2">Loading...</p>
                )}

                {!loading && sessions.length === 0 && (
                    <p className="text-sm text-gray-400 px-2">
                        No sessions yet
                    </p>
                )}

                {sessions.map((s) => {
                    const active = sessionId === s.session_id;

                    return (
                        <div
                            key={s.session_id}
                            onClick={() => handleSelect(s.session_id)}
                            className={`p-3 mb-2 rounded cursor-pointer transition ${
                                active
                                    ? "bg-gray-200"
                                    : "hover:bg-gray-100"
                            }`}
                        >
                            <p className="text-sm font-medium truncate">
                                {s.preview || "New Chat"}
                            </p>

                            <p className="text-xs text-gray-400">
                                {s.created_at
                                    ? new Date(
                                          s.created_at
                                      ).toLocaleString()
                                    : ""}
                            </p>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}