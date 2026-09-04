"use client";

import { useState } from "react";
import type { Citation, ShopLinks } from "@/lib/types";

function pageSrc(docId: string, page: number, query?: string) {
  const q = query ? `?q=${encodeURIComponent(query)}` : "";
  return `/api/manual/${docId}/page/${page}${q}`;
}

function Crop({ path }: { path?: string | null }) {
  if (!path) return null;
  return (
    <img
      src={`/api/images/${path.replace(/^data\//, "")}`}
      alt="Manual page"
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
  question,
}: {
  answer: string;
  citations: Citation[];
  refused?: boolean;
  vehicleLabel?: string | null;
  shop?: ShopLinks | null;
  question?: string;
}) {
  const [open, setOpen] = useState<{ docId: string; page: number } | null>(null);

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
          {c.doc_id && c.page_number ? (
            <button
              type="button"
              className="text-left text-xs font-medium text-ink underline decoration-black/25 hover:decoration-ink"
              onClick={() => setOpen({ docId: c.doc_id as string, page: c.page_number as number })}
            >
              Open manual p.{c.page_number} only
            </button>
          ) : (
            <div className="text-xs font-medium text-ink">In the manual · p.{c.page_number}</div>
          )}
          <div className="mt-1 font-mono text-[11px] text-steel">
            {c.doc_id}
            {c.section_name ? ` · ${c.section_name}` : ""}
          </div>
          <Crop path={c.crop_path} />
        </div>
      ))}
      {refused && (
        <div className="bg-amber-50 px-4 py-2 text-xs">No grounded number in the ingested manual — nothing invented.</div>
      )}
      {shop && shop.links.length > 0 && (
        <div className="border-t border-black/10 bg-black/[0.02] px-4 py-3">
          <p className="text-xs font-medium text-ink">
            {shop.source === "web" ? "Type from the web — check these sites" : "Search this spec on working shop pages"}
          </p>
          <p className="mt-1 text-[11px] text-steel">{shop.note}</p>
          {shop.query && <p className="mt-1 font-mono text-[11px] text-ink">{shop.query}</p>}
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
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
          onClick={() => setOpen(null)}
        >
          <div
            className="max-h-[90vh] w-full max-w-3xl overflow-auto rounded-xl bg-white p-3"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="font-mono text-xs text-steel">
                {open.docId} · p.{open.page} only
              </p>
              <button type="button" className="text-xs text-steel" onClick={() => setOpen(null)}>
                Close
              </button>
            </div>
            <img
              src={pageSrc(open.docId, open.page, question)}
              alt={`Manual page ${open.page}`}
              className="w-full rounded bg-black/5 object-contain"
            />
          </div>
        </div>
      )}
    </article>
  );
}
