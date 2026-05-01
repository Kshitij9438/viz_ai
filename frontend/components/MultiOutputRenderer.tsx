"use client";

import { AssetGrid } from "@/components/AssetGrid";
import { AssetBundle, CreativeOutput } from "@/lib/api";

export function MultiOutputRenderer({
  output,
  fallbackBundle,
  onSelect,
  onRefine,
  onSaveBundle,
}: {
  output?: CreativeOutput | null;
  fallbackBundle?: AssetBundle | null;
  onSelect?: (bundle: AssetBundle, variant: number) => void;
  onRefine?: () => void;
  onSaveBundle?: (bundle: AssetBundle) => void;
}) {
  const items = output?.outputs?.length
    ? output.outputs
    : fallbackBundle
      ? [{ kind: "asset_bundle", bundle: fallbackBundle }]
      : [];

  if (!items.length) return null;

  return (
    <div className="mt-3 space-y-4">
      {output?.metadata?.intent && (
        <div className="rounded-lg border border-stone-200 bg-white px-3 py-2 text-xs text-stone-500">
          {output.metadata.intent.intent} / {output.metadata.intent.pipeline}
        </div>
      )}

      {items.map((item: any, index) => {
        if (item.kind === "asset_bundle" && item.bundle) {
          const bundle = item.bundle as AssetBundle;
          return (
            <AssetGrid
              key={`${bundle.bundle_id}-${index}`}
              bundle={bundle}
              onSelect={(variant) => onSelect?.(bundle, variant)}
              onRefine={onRefine}
              onSave={() => onSaveBundle?.(bundle)}
            />
          );
        }

        if (item.kind === "story") {
          return (
            <div key={`story-${index}`} className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
              <h3 className="text-sm font-semibold text-stone-900">{item.title || "Story"}</h3>
              {item.logline && <p className="mt-1 text-sm text-stone-600">{item.logline}</p>}
              <div className="mt-3 grid gap-2">
                {(item.scenes || []).map((scene: any, sceneIndex: number) => (
                  <div key={sceneIndex} className="rounded-lg bg-stone-50 p-3">
                    <p className="text-sm font-medium text-stone-800">
                      {sceneIndex + 1}. {scene.title || "Scene"}
                    </p>
                    <p className="mt-1 text-sm text-stone-600">{scene.description || scene.visual_prompt}</p>
                  </div>
                ))}
              </div>
            </div>
          );
        }

        if (item.kind === "campaign_brief") {
          return (
            <div key={`campaign-${index}`} className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
              <h3 className="text-sm font-semibold text-stone-900">{item.campaign_name || "Campaign"}</h3>
              {item.positioning && <p className="mt-1 text-sm text-stone-600">{item.positioning}</p>}
              {!!item.headlines?.length && (
                <div className="mt-3">
                  <p className="text-xs font-semibold uppercase text-stone-500">Headlines</p>
                  <ul className="mt-1 space-y-1 text-sm text-stone-700">
                    {item.headlines.map((headline: string) => <li key={headline}>{headline}</li>)}
                  </ul>
                </div>
              )}
              {!!item.captions?.length && (
                <div className="mt-3">
                  <p className="text-xs font-semibold uppercase text-stone-500">Captions</p>
                  <ul className="mt-1 space-y-1 text-sm text-stone-700">
                    {item.captions.map((caption: string) => <li key={caption}>{caption}</li>)}
                  </ul>
                </div>
              )}
            </div>
          );
        }

        return null;
      })}
    </div>
  );
}
