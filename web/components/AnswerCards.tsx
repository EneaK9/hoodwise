"use client";

import type { Citation, ShopLinks } from "@/lib/types";

function Crop({ path }: { path?: string | null }) {
  if (!path) return null;
  return (
    <img
      src={`/api/images/${path.replace(/^data\//, "")}`}
      alt="Manual crop"
      className="mt-2 max-h-72 w-full rounded object-contain bg-black/5"
    />
  );
}

export function AnswerCard({
  answer,
  citations,
  refused,
  vehicleLabel,
  shop,
}: {
  answer: string;
  citations: Citation[];
  refused?: boolean;
  vehicleLabel?: string | null;
  shop?: ShopLinks | null;
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
          {c.value_raw && c.kind !== "page" && (
            <div className="font-mono text-xl">{c.value_raw}</div>
          )}
          <div className="text-xs font-medium text-ink">
            In the manual · p.{c.page_number}
          </div>
          <div className="mt-1 font-mono text-[11px] text-steel">
            {c.doc_id}
            {c.section_name ? ` · ${c.section_name}` : ""}
            {c.part_name && c.kind !== "page" ? ` · ${c.part_name}` : ""}
            {c.condition_note ? ` · ${c.condition_note}` : ""}
          </div>
          <Crop path={c.crop_path} />
        </div>
      ))}
      {refused && (
        <div className="bg-amber-50 px-4 py-2 text-xs">No grounded number in the ingested manual — nothing invented.</div>
      )}
      {shop && shop.links.length > 0 && (
        <div className="border-t border-black/10 bg-black/[0.02] px-4 py-3">
          <p className="text-xs font-medium text-ink">Check this spec on three sites</p>
          <p className="mt-1 text-[11px] text-steel">{shop.note}</p>
          <p className="mt-1 font-mono text-[11px] text-ink">{shop.spec}</p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {shop.links.map((link) => (
              <li key={link.name}>
                <a
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex rounded-full border border-black/15 bg-white px-3 py-1 text-xs text-ink hover:border-ink"
                >
                  {link.name}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}
