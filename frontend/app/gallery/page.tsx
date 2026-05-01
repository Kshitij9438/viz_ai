"use client";

import { useEffect, useState } from "react";
import { getUserAssets, saveAsset } from "@/lib/api";

type Asset = {
  id: string;
  url: string;
  type: string;
  prompt?: string;
};

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// 🔥 normalize ALL possible broken backend URLs
function resolveUrl(url: string) {
  if (!url) return "";

  // already correct (Supabase or external)
  if (url.startsWith("http")) return url;

  // ❌ broken case: /storage/generated/xyz.jpg
  if (url.startsWith("/storage")) {
    return `${API}${url}`;
  }

  // ❌ partial path: generated/xyz.jpg
  if (url.startsWith("generated")) {
    return `${API}/storage/${url}`;
  }

  // fallback
  return `${API}/${url}`;
}

export default function GalleryPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [loading, setLoading] = useState(true);

  async function loadAssets() {
    try {
      setLoading(true);
      const data = await getUserAssets();

      console.log("RAW ASSETS:", data);

      setAssets(data || []);
    } catch (e) {
      console.error("Failed to load assets", e);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAssets();
  }, []);

  function download(url: string) {
    const final = resolveUrl(url);

    const a = document.createElement("a");
    a.href = final;
    a.download = "vizzy-asset";
    a.click();
  }

  return (
    <main className="p-6 bg-gray-100 min-h-screen">
      <h1 className="text-xl font-semibold mb-6">Gallery</h1>

      {loading && <p>Loading...</p>}

      {!loading && assets.length === 0 && (
        <p className="text-gray-500">No assets yet</p>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
        {assets.map((a) => {
          const finalUrl = resolveUrl(a.url);

          console.log("IMAGE URL FIXED:", a.url, "→", finalUrl);

          return (
            <div
              key={a.id}
              className="bg-white rounded-xl shadow p-3 flex flex-col"
            >
              {/* Image */}
              <img
                src={finalUrl}
                className="rounded mb-2 object-cover w-full h-48"
                alt="asset"
                onError={(e) => {
                  console.error("❌ IMAGE FAILED:", finalUrl);
                  (e.target as HTMLImageElement).style.opacity = "0.3";
                }}
              />

              {/* Prompt */}
              {a.prompt && (
                <p className="text-xs text-gray-500 mb-2 line-clamp-2">
                  {a.prompt}
                </p>
              )}

              {/* Actions */}
              <div className="flex gap-2 mt-auto">
                <button
                  className="text-xs border px-2 py-1 rounded hover:bg-gray-50"
                  onClick={() => saveAsset(a.id)}
                >
                  Save
                </button>

                <button
                  className="text-xs border px-2 py-1 rounded hover:bg-gray-50"
                  onClick={() => download(a.url)}
                >
                  Download
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </main>
  );
}