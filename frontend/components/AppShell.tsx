"use client";

import { useEffect, useState } from "react";
import Sidebar from "./Sidebar";
import { SessionProvider, useSession } from "./SessionContext";

function LayoutInner({ children }: { children: React.ReactNode }) {
    const { setSessionId } = useSession();

    const [isAuthed, setIsAuthed] = useState<boolean | null>(null);

    // 🔥 SINGLE SOURCE OF TRUTH FOR AUTH STATE
    function computeAuth() {
        if (typeof window === "undefined") return false;

        const token = localStorage.getItem("vizzy_token");
        const guest = localStorage.getItem("vizzy_guest_token");

        return (
            (token && token !== "null" && token !== "undefined") ||
            (guest && guest !== "null" && guest !== "undefined")
        );
    }

    useEffect(() => {
        // Initial check
        setIsAuthed(!!computeAuth());

        // 🔥 React to login/logout (same tab)
        const interval = setInterval(() => {
            setIsAuthed(!!computeAuth());
        }, 500);

        // 🔥 React to login/logout (other tabs)
        function handleStorage() {
            setIsAuthed(!!computeAuth());
        }

        window.addEventListener("storage", handleStorage);

        return () => {
            clearInterval(interval);
            window.removeEventListener("storage", handleStorage);
        };
    }, []);

    // Prevent hydration mismatch
    if (isAuthed === null) return null;

    // 🔥 NO SIDEBAR when not authenticated
    if (!isAuthed) {
        return <>{children}</>;
    }

    return (
        <div className="flex h-screen bg-gray-100">
            {/* Sidebar */}
            <div className="w-72 border-r bg-white shadow-sm">
                <Sidebar onSelectSession={setSessionId} />
            </div>

            {/* Main */}
            <main className="flex-1 overflow-hidden">{children}</main>
        </div>
    );
}

export default function AppShell({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <SessionProvider>
            <LayoutInner>{children}</LayoutInner>
        </SessionProvider>
    );
}