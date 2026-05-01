"use client";

import { ReactNode } from "react";

export function AppShell({
  sidebar,
  sidebarCollapsed,
  children,
}: {
  sidebar?: ReactNode;
  sidebarCollapsed?: boolean;
  children: ReactNode;
}) {
  return (
    <main className="h-screen overflow-hidden bg-stone-50 text-stone-900">
      <div className={sidebar ? (sidebarCollapsed ? "h-full w-full md:pl-16" : "h-full w-full md:pl-72") : "h-full w-full"}>
        {sidebar}
        <section className="flex h-full min-w-0 flex-col">{children}</section>
      </div>
    </main>
  );
}
