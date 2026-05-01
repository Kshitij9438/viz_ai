"use client";

import {
    createContext,
    useContext,
    useEffect,
    useState,
} from "react";

type SessionContextType = {
    sessionId: string | null;
    setSessionId: (id: string | null) => void;
    clearSession: () => void;
};

const STORAGE_KEY = "vizzy_session_id";

const SessionContext = createContext<SessionContextType | null>(null);

export function SessionProvider({
    children,
}: {
    children: React.ReactNode;
}) {
    const [sessionId, setSessionIdState] = useState<string | null>(null);
    const [ready, setReady] = useState(false);

    // =========================
    // LOAD FROM STORAGE (ONCE)
    // =========================
    useEffect(() => {
        const stored =
            typeof window !== "undefined"
                ? localStorage.getItem(STORAGE_KEY)
                : null;

        if (stored && stored !== "null" && stored !== "undefined") {
            setSessionIdState(stored);
        }

        setReady(true);
    }, []);

    // =========================
    // SET SESSION (SYNC STORAGE)
    // =========================
    function setSessionId(id: string | null) {
        setSessionIdState(id);

        if (typeof window !== "undefined") {
            if (id) {
                localStorage.setItem(STORAGE_KEY, id);
            } else {
                localStorage.removeItem(STORAGE_KEY);
            }
        }
    }

    // =========================
    // CLEAR SESSION
    // =========================
    function clearSession() {
        setSessionId(null);
    }

    // ⛔ Prevent hydration mismatch
    if (!ready) return null;

    return (
        <SessionContext.Provider
            value={{ sessionId, setSessionId, clearSession }}
        >
            {children}
        </SessionContext.Provider>
    );
}

export function useSession() {
    const ctx = useContext(SessionContext);

    if (!ctx) {
        throw new Error(
            "useSession must be used inside SessionProvider"
        );
    }

    return ctx;
}