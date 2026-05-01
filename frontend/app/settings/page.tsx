"use client";

import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import Link from "next/link";

import { AppShell } from "@/components/AppShell";
import {
  BusinessProfile,
  TasteProfile,
  getBusinessProfile,
  getTasteProfile,
  readAuthState,
  saveBusinessProfile,
  uploadFile,
} from "@/lib/api";

const emptyBusiness: BusinessProfile = {
  business_name: "",
  business_type: "",
  brand_tone: "",
  brand_colors: { primary: "#111827", secondary: "#f5f5f4", accent: "#2563eb" },
  logo_url: "",
};

function ChipList({ items }: { items: string[] }) {
  if (!items?.length) return <p className="text-sm text-stone-500">Nothing recorded yet.</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <span key={item} className="rounded-lg border border-stone-200 bg-white px-3 py-1 text-sm text-stone-700">
          {item}
        </span>
      ))}
    </div>
  );
}

export default function SettingsPage() {
  const [tab, setTab] = useState<"personal" | "business">("personal");
  const [taste, setTaste] = useState<TasteProfile | null>(null);
  const [business, setBusiness] = useState<BusinessProfile>(emptyBusiness);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    queueMicrotask(() => {
      const auth = readAuthState();
      if (!auth.userId || (!auth.token && !auth.guestToken)) {
        setLoading(false);
        return;
      }

      Promise.all([getTasteProfile(auth.userId), getBusinessProfile(auth.userId)])
        .then(([tasteProfile, businessProfile]) => {
          setTaste(tasteProfile);
          if (businessProfile) {
            setBusiness({
              ...emptyBusiness,
              ...businessProfile,
              brand_colors: businessProfile.brand_colors || emptyBusiness.brand_colors,
            });
          }
        })
        .catch((err: any) => setError(err.message))
        .finally(() => setLoading(false));
    });
  }, []);

  function setBusinessField<K extends keyof BusinessProfile>(key: K, value: BusinessProfile[K]) {
    setBusiness((current) => ({ ...current, [key]: value }));
  }

  function setBrandColor(key: string, value: string) {
    setBusiness((current) => ({
      ...current,
      brand_colors: { ...(current.brand_colors || {}), [key]: value },
    }));
  }

  async function handleLogo(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setSaving(true);
    setMessage("");
    try {
      const attachment = await uploadFile(file);
      setBusinessField("logo_url", attachment.url);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
      event.currentTarget.value = "";
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const auth = readAuthState();
    if (!auth.userId) return;

    setSaving(true);
    setError("");
    setMessage("");
    try {
      await saveBusinessProfile(auth.userId, {
        ...business,
        business_name: business.business_name.trim(),
      });
      setMessage("Settings saved.");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <AppShell>
      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-stone-200 bg-white/90 px-4 py-3 shadow-sm backdrop-blur md:px-8">
        <div>
          <h1 className="text-lg font-semibold">Settings</h1>
          <p className="text-sm text-stone-500">Personal taste and business context</p>
        </div>
        <Link
          href="/"
          className="rounded-lg bg-stone-900 px-3 py-2 text-sm font-medium text-white shadow-sm hover:bg-stone-800"
        >
          Back to Chat
        </Link>
      </header>

      <section className="flex-1 overflow-y-auto px-4 py-6 md:px-8">
        <div className="mx-auto max-w-4xl">
          <div className="mb-5 inline-flex rounded-lg bg-stone-100 p-1">
            <button
              type="button"
              onClick={() => setTab("personal")}
              className={`rounded-md px-4 py-2 text-sm ${tab === "personal" ? "bg-white shadow-sm" : "text-stone-600"}`}
            >
              Personal
            </button>
            <button
              type="button"
              onClick={() => setTab("business")}
              className={`rounded-md px-4 py-2 text-sm ${tab === "business" ? "bg-white shadow-sm" : "text-stone-600"}`}
            >
              Business
            </button>
          </div>

          {loading ? (
            <div className="rounded-xl border border-stone-200 bg-white p-6 text-sm text-stone-500 shadow-sm">
              Loading settings...
            </div>
          ) : error ? (
            <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 shadow-sm">
              {error}
            </div>
          ) : tab === "personal" ? (
            <div className="space-y-4 rounded-xl border border-stone-200 bg-white p-6 shadow-sm">
              <div>
                <h2 className="mb-2 text-sm font-semibold text-stone-700">Taste summary</h2>
                <p className="text-sm text-stone-600">{taste?.taste_summary || "No taste profile yet."}</p>
              </div>
              <div>
                <h2 className="mb-2 text-sm font-semibold text-stone-700">Styles</h2>
                <ChipList items={taste?.preferred_styles || []} />
              </div>
              <div>
                <h2 className="mb-2 text-sm font-semibold text-stone-700">Colors</h2>
                <ChipList items={taste?.preferred_colors || []} />
              </div>
              <div>
                <h2 className="mb-2 text-sm font-semibold text-stone-700">Dislikes</h2>
                <ChipList items={taste?.disliked_styles || []} />
              </div>
            </div>
          ) : (
            <form onSubmit={save} className="space-y-5 rounded-xl border border-stone-200 bg-white p-6 shadow-sm">
              <div className="grid gap-4 md:grid-cols-2">
                <label className="block text-sm">
                  <span className="mb-1 block font-medium text-stone-700">Business name</span>
                  <input
                    value={business.business_name}
                    onChange={(event) => setBusinessField("business_name", event.target.value)}
                    className="w-full rounded-lg border border-stone-200 px-3 py-2 outline-none focus:border-stone-400"
                    required
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block font-medium text-stone-700">Business type</span>
                  <input
                    value={business.business_type || ""}
                    onChange={(event) => setBusinessField("business_type", event.target.value)}
                    className="w-full rounded-lg border border-stone-200 px-3 py-2 outline-none focus:border-stone-400"
                  />
                </label>
              </div>

              <label className="block text-sm">
                <span className="mb-1 block font-medium text-stone-700">Brand tone</span>
                <textarea
                  value={business.brand_tone || ""}
                  onChange={(event) => setBusinessField("brand_tone", event.target.value)}
                  rows={4}
                  className="w-full rounded-lg border border-stone-200 px-3 py-2 outline-none focus:border-stone-400"
                />
              </label>

              <div>
                <h2 className="mb-2 text-sm font-semibold text-stone-700">Brand colors</h2>
                <div className="grid gap-3 sm:grid-cols-3">
                  {Object.entries(business.brand_colors || emptyBusiness.brand_colors || {}).map(([key, value]) => (
                    <label key={key} className="rounded-lg border border-stone-200 p-3 text-sm">
                      <span className="mb-2 block capitalize text-stone-600">{key}</span>
                      <input
                        type="color"
                        value={value}
                        onChange={(event) => setBrandColor(key, event.target.value)}
                        className="h-10 w-full rounded-md"
                      />
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <h2 className="mb-2 text-sm font-semibold text-stone-700">Logo</h2>
                <div className="flex flex-wrap items-center gap-3">
                  {business.logo_url && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={business.logo_url} alt="Business logo" className="h-16 w-16 rounded-lg border object-cover" />
                  )}
                  <input type="file" accept="image/*" onChange={handleLogo} className="text-sm" />
                </div>
              </div>

              {message && <p className="text-sm text-green-700">{message}</p>}
              <button
                type="submit"
                disabled={saving}
                className="rounded-lg bg-stone-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-stone-800 disabled:opacity-60"
              >
                {saving ? "Saving..." : "Save settings"}
              </button>
            </form>
          )}
        </div>
      </section>
    </AppShell>
  );
}
