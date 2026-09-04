"use client";

import type { Citation } from "@/lib/types";

function Crop({ path }: { path?: string | null }) {
  if (!path) return null;
  return (
    <img
      src={`/api/images/${path.replace(/^data\//, "")}`}
      alt="Manual crop"
      className="mt-3 max-h-40 w-full rounded object-contain bg-black/5"
    />
  );
}

export function AnswerCard({
  answer,
  citations,
  refused,
  vehicleLabel,
}: {
  answer: string;
  citations: Citation[];
  refused?: boolean;
  vehicleLabel?: string | null;
}) {
  return (
    <article className="overflow-hidden rounded-xl border border-black/10 bg-white shadow-sm">
      {vehicleLabel && (
        <div className="border-b border-black/5 bg-ink px-4 py-1.5 font-mono text-[11px] uppercase tracking-wide text-paper">
          {vehicleLabel}
        </div>
      )}
      <div className="px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap">{answer}</div>
      {citations.map((c, i) => (
        <div key={i} className="border-t border-black/5 px-4 py-3">
          <div className="font-mono text-xl">{c.value_raw}</div>
          <div className="mt-1 text-sm text-steel">
            {c.part_name}
            {c.condition_note ? ` · ${c.condition_note}` : ""}
          </div>
          <div className="mt-1 font-mono text-xs text-steel">
            {c.doc_id} · p.{c.page_number}
            {c.section_name ? ` · ${c.section_name}` : ""}
          </div>
          <Crop path={c.crop_path} />
        </div>
      ))}
      {refused && (
        <div className="bg-amber-50 px-4 py-2 text-xs">No grounded number in the ingested manual — nothing invented.</div>
      )}
    </article>
  );
}
