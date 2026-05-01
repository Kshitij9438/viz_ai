"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { AppShell } from "@/components/AppShell";
import { downloadAsset } from "@/components/AssetGrid";
import { Asset, listAssets, readAuthState, saveAsset } from "@/lib/api";

function groupByDate(assets: Asset[]) {
  return assets.reduce<Record<string, Asset[]>>((groups, asset) => {
    const key = asset.created_at
      ? new Date(asset.created_at).toLocaleDateString()
      : "Undated";
    groups[key] = groups[key] || [];
    groups[key].push(asset);
    return groups;
  }, {});
}

export default function GalleryPage() {
  const [savedOnly, setSavedOnly] = useState(false);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState<string | null>(null);

  async function load(nextSavedOnly = savedOnly) {
    const auth = readAuthState();
    if (!auth.userId || (!auth.token && !auth.guestToken)) {
      setLoading(false);
      return;
    }

    setLoading(true);
    setError("");
    try {
      setAssets(await listAssets(auth.userId, nextSavedOnly));
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    queueMicrotask(() => {
      void load(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const grouped = useMemo(() => groupByDate(assets), [assets]);

  return (
    <AppShell>
      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-stone-200 bg-white/90 px-4 py-3 shadow-sm backdrop-blur md:px-8">
        <div>
          <h1 className="text-lg font-semibold">Gallery</h1>
          <p className="text-sm text-stone-500">Saved and generated assets</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              const next = !savedOnly;
              setSavedOnly(next);
              load(next);
            }}
            className="rounded-lg border border-stone-200 px-3 py-2 text-sm hover:bg-stone-100"
          >
            {savedOnly ? "Show all" : "Saved only"}
          </button>
          <Link
            href="/"
            className="rounded-lg bg-stone-900 px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-stone-800"
          >
            Back to Chat
          </Link>
        </div>
      </header>

      <section className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
        {loading ? (
          <div className="rounded-xl border border-stone-200 bg-white p-6 text-sm text-stone-500 shadow-sm">
            Loading gallery...
          </div>
        ) : error ? (
          <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 shadow-sm">
            {error}
          </div>
        ) : assets.length === 0 ? (
          <div className="rounded-xl border border-dashed border-stone-300 bg-white p-8 text-sm text-stone-500 shadow-sm">
            No assets yet. Generate something in chat and it will appear here.
          </div>
        ) : (
          <div className="space-y-8">
            {Object.entries(grouped).map(([date, group]) => (
              <section key={date}>
                <h2 className="mb-3 text-sm font-semibold text-stone-600">{date}</h2>
                <div className="columns-1 gap-4 sm:columns-2 lg:columns-3 xl:columns-4">
                  {group.map((asset) => (
                    <article
                      key={asset.id}
                      className="mb-4 break-inside-avoid overflow-hidden rounded-xl border border-stone-200 bg-white shadow-sm"
                    >
                      <div className="bg-stone-100">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={asset.url}
                          alt={asset.prompt || "Generated asset"}
                          loading="lazy"
                          referrerPolicy="no-referrer"
                          className="w-full object-cover"
                        />
                      </div>
                      <div className="space-y-3 p-3">
                        <p className="line-clamp-3 text-sm text-stone-700">
                          {asset.prompt || asset.type}
                        </p>
                        <div className="flex gap-2">
                          <button
                            type="button"
                            disabled={savingId === asset.id || asset.saved_permanently}
                            onClick={async () => {
                              setSavingId(asset.id);
                              try {
                                await saveAsset(asset.id);
                                setAssets((current) =>
                                  current.map((item) =>
                                    item.id === asset.id ? { ...item, saved_permanently: true } : item,
                                  ),
                                );
                              } finally {
                                setSavingId(null);
                              }
                            }}
                            className="rounded-lg bg-stone-900 px-3 py-2 text-xs font-medium text-white hover:bg-stone-800 disabled:opacity-50"
                          >
                            {asset.saved_permanently ? "Saved" : "Save"}
                          </button>
                          <button
                            type="button"
                            onClick={() => downloadAsset(asset.url, `${asset.id}.jpg`)}
                            className="rounded-lg border border-stone-200 px-3 py-2 text-xs font-medium text-stone-700 hover:bg-stone-100"
                          >
                            Download
                          </button>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </section>
    </AppShell>
  );
}
