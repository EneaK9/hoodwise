"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { VehiclePicker } from "@/components/VehiclePicker";

export default function GaragePage() {
  const [rows, setRows] = useState<Array<Record<string, unknown>>>([]);
  const [variantId, setVariantId] = useState("");
  const [nickname, setNickname] = useState("");
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    api.garage().then((r) => setRows(r.vehicles)).catch((e) => setError(e.message));
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <main className="mx-auto max-w-xl px-4 py-10">
      <h1 className="text-2xl">Garage</h1>
      <p className="mt-1 text-sm text-steel">Saved cars for your account. VIN is optional.</p>
      <form
        className="mt-6 flex flex-col gap-2"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!variantId) return;
          try {
            await api.addGarage(variantId, nickname || undefined);
            setNickname("");
            refresh();
          } catch (err) {
            setError(err instanceof Error ? err.message : "Could not save");
          }
        }}
      >
        <VehiclePicker value={variantId} onChange={setVariantId} />
        <input className="rounded border px-3 py-2" placeholder="Nickname (e.g. daily)" value={nickname} onChange={(e) => setNickname(e.target.value)} />
        <button className="rounded bg-ink py-2 text-paper">Add to garage</button>
      </form>
      {error && <p className="mt-3 text-sm text-rust">{error}</p>}
      <ul className="mt-8 space-y-2">
        {rows.map((r) => (
          <li key={String(r.id)} className="rounded border border-black/10 bg-white px-3 py-2 text-sm">
            {String(r.nickname || "Civic")} — {String(r.engine_label)} {String(r.transmission)} {String(r.body || "")}
          </li>
        ))}
      </ul>
    </main>
  );
}
