"use client";
import { AssetBundle } from "@/lib/api";

export function AssetGrid({
  bundle,
  onSelect,
}: {
  bundle: AssetBundle;
  onSelect?: (variant: number) => void;
}) {
  const single = bundle.assets.length === 1;
  return (
    <div
      className={
        single
          ? "mt-3"
          : "mt-3 grid gap-3 " +
          (bundle.assets.length >= 6
            ? "grid-cols-3"
            : bundle.assets.length >= 2
              ? "grid-cols-2"
              : "grid-cols-1")
      }
    >
      {bundle.assets.map((a) => (
        <button
          key={a.id}
          onClick={() => onSelect?.(a.index)}
          className="group relative overflow-hidden rounded-xl border border-stone-300 bg-white shadow-sm transition hover:shadow-md"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={a.url} alt={`variant ${a.index}`} className="h-full w-full object-cover" />
          console.log("ASSET URL:", a.url);
          {!single && (
            <span className="absolute left-2 top-2 rounded-md bg-black/60 px-2 py-0.5 text-xs text-white">
              {a.index}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
