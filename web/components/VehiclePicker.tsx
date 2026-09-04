"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Variant } from "@/lib/types";

export function VehiclePicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (variantId: string) => void;
}) {
  const [variants, setVariants] = useState<Variant[]>([]);

  useEffect(() => {
    api.vehicles().then((r) => setVariants(r.variants)).catch(() => setVariants([]));
  }, []);

  return (
    <select
      className="w-full rounded border border-black/15 bg-white px-2 py-2 text-sm"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">General (no vehicle pinned)</option>
      {variants.map((v) => (
        <option key={v.variant_id} value={v.variant_id}>
          {v.year_from}–{v.year_to} Civic {v.body} {v.engine_label} {v.transmission}
          {v.trim ? ` ${v.trim}` : ""}
        </option>
      ))}
    </select>
  );
}
