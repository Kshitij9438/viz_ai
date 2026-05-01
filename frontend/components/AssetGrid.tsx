"use client";

import { useState } from "react";
import { AssetBundle } from "@/lib/api";

export function downloadAsset(url: string, filename: string) {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener noreferrer";
  anchor.target = "_blank";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

export function downloadBundle(bundle: AssetBundle) {
  bundle.assets.forEach((asset, index) => {
    window.setTimeout(() => {
      downloadAsset(asset.url, `${bundle.bundle_id}-${asset.index || index + 1}.jpg`);
    }, index * 150);
  });
}

export function AssetGrid({
  bundle,
  onSelect,
  onRefine,
  onSave,
}: {
  bundle: AssetBundle;
  onSelect?: (variant: number) => void;
  onRefine?: () => void;
  onSave?: () => void;
}) {
  const assets = bundle.assets.filter((asset) => asset.url);
  const actions = new Set(bundle.actions || []);
  const single = assets.length === 1;

  if (assets.length === 0) return null;

  return (
    <div className="mt-3">
      <div
        className={
          single
            ? "max-w-xl"
            : "grid max-w-3xl gap-3 " +
              (assets.length >= 6
                ? "grid-cols-2 sm:grid-cols-3"
                : assets.length >= 2
                  ? "grid-cols-2"
                  : "grid-cols-1")
        }
      >
        {assets.map((asset) => (
          <AssetTile
            key={asset.id}
            url={asset.url}
            index={asset.index}
            single={single}
            onClick={() => onSelect?.(asset.index)}
          />
        ))}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {actions.has("refine") && (
          <button
            type="button"
            onClick={onRefine}
            className="rounded-lg border border-stone-200 bg-white px-3 py-2 text-xs font-medium text-stone-700 shadow-sm hover:bg-stone-50"
          >
            Refine
          </button>
        )}
        {actions.has("download_all") && (
          <button
            type="button"
            onClick={() => downloadBundle(bundle)}
            className="rounded-lg border border-stone-200 bg-white px-3 py-2 text-xs font-medium text-stone-700 shadow-sm hover:bg-stone-50"
          >
            Download all
          </button>
        )}
        {actions.has("save") && (
          <button
            type="button"
            onClick={onSave}
            className="rounded-lg bg-stone-900 px-3 py-2 text-xs font-medium text-white shadow-sm hover:bg-stone-800"
          >
            Save
          </button>
        )}
      </div>
    </div>
  );
}

function AssetTile({
  url,
  index,
  single,
  onClick,
}: {
  url: string;
  index: number;
  single: boolean;
  onClick: () => void;
}) {
  const [failed, setFailed] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      className="group relative aspect-square overflow-hidden rounded-lg border border-stone-200 bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
    >
      {failed ? (
        <div className="flex h-full w-full items-center justify-center bg-stone-100 px-4 text-center text-xs text-stone-500">
          Image unavailable
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={url}
          alt={`Generated asset ${index}`}
          loading="lazy"
          referrerPolicy="no-referrer"
          className="h-full w-full object-cover"
          onError={() => setFailed(true)}
        />
      )}

      {!single && (
        <span className="absolute left-2 top-2 rounded-md bg-black/65 px-2 py-0.5 text-xs text-white">
          {index}
        </span>
      )}
    </button>
  );
}
