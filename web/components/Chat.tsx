"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ChatMessage, Citation, ShopLinks } from "@/lib/types";
import { AnswerCard } from "./AnswerCards";

type VehicleInfo = {
  label?: string | null;
  year?: number | string | null;
  make?: string | null;
  model?: string | null;
  engine_label?: string | null;
  transmission?: string | null;
  trim?: string | null;
  note?: string | null;
  confidence?: string | null;
  source?: string | null;
  specs?: Array<{ label: string; value: string }>;
  history?: {
    note?: string;
    stolen?: Array<{ region: string; status: string }>;
    stolen_note?: string;
    market?: {
      currency?: string;
      median?: number;
      low?: number;
      high?: number;
      odometer_median_km?: number;
      sample_size?: number;
      markets?: string[];
    } | null;
    market_note?: string;
  } | null;
};

const VIN_SOURCE_LABEL: Record<string, string> = {
  vpic: "NHTSA vPIC",
  vincario: "Vincario",
  carsxe: "CarsXE",
  api_ninjas: "API Ninjas",
  pattern: "Civic VIN pattern",
  wmi: "manufacturer code only",
};

function VinIcon({ active }: { active: boolean }) {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M4 16.5V15a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v1.5"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <path d="M6.5 13l1.4-4.2A2 2 0 0 1 9.8 7.5h4.4a2 2 0 0 1 1.9 1.3L17.5 13" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="7.5" cy="16.5" r="1.6" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="16.5" cy="16.5" r="1.6" stroke="currentColor" strokeWidth="1.6" />
      {active && <path d="M9 10.5h6" stroke="currentColor" strokeWidth="1.6" />}
    </svg>
  );
}

export function Chat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [vinOpen, setVinOpen] = useState(false);
  const [vin, setVin] = useState("");
  const [vehicle, setVehicle] = useState<VehicleInfo | null>(null);
  const [vinError, setVinError] = useState<string | null>(null);
  const vinRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (vinOpen) vinRef.current?.focus();
  }, [vinOpen]);

  async function lookupVin(value: string) {
    const cleaned = value.trim().toUpperCase();
    if (cleaned.length !== 17) {
      setVinError("VIN must be 17 characters");
      return;
    }
    setVinError(null);
    try {
      const res = await api.decodeVin(cleaned);
      const d = res.decode as VehicleInfo & { label?: string };
      const label =
        d.label ||
        [d.year, d.make, d.model, d.trim, d.engine_label, d.transmission].filter(Boolean).join(" ");
      setVehicle({
        ...d,
        label,
        note: d.note,
        confidence: d.confidence,
        source: d.source,
        specs: d.specs || [],
      });
      setVin(cleaned);
    } catch (err) {
      setVehicle(null);
      setVinError(err instanceof Error ? err.message : "Could not decode VIN");
    }
  }

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    setMessages((m) => [...m, { id: crypto.randomUUID(), role: "user", content: trimmed }]);
    setInput("");
    try {
      const res = await api.chat({
        session_id: sessionId,
        message: trimmed,
        vin: vin || undefined,
      });
      setSessionId(res.session_id);
      localStorage.setItem("hoodwise_session", res.session_id);
      if (res.vehicle?.label) setVehicle({ ...res.vehicle, specs: res.vehicle.specs || [] });
      if (res.detected_vins?.length && !vin) {
        setVin(res.detected_vins[0]);
        setVinOpen(true);
      }
      setMessages((m) => [
        ...m,
        {
          id: res.message_id,
          role: "assistant",
          content: res.answer,
          citations: res.citations as Citation[],
          vehicle: res.vehicle,
          shop: (res.shop as ShopLinks | null) || null,
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
      <div className="flex min-h-[48vh] flex-col gap-4">
        {messages.length === 0 && (
          <div className="rounded-xl border border-dashed border-black/20 p-8 text-sm text-steel">
            Tap the car icon, drop in a VIN, then ask. Hoodwise looks up the exact car and answers from the factory manual.
          </div>
        )}
        {messages.map((m, idx) =>
          m.role === "user" ? (
            <div key={m.id} className="self-end rounded-2xl bg-ink px-4 py-2 text-sm text-paper">
              {m.content}
            </div>
          ) : (
            <AnswerCard
              key={m.id}
              answer={m.content}
              citations={m.citations || []}
              vehicleLabel={m.vehicle?.label || vehicle?.label}
              shop={m.shop}
              question={[...messages.slice(0, idx)].reverse().find((row) => row.role === "user")?.content}
            />
          ),
        )}
      </div>

      {error && <p className="text-sm text-rust">{error}</p>}

      <form
        className="rounded-2xl border border-black/15 bg-white shadow-sm"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        {vinOpen && (
          <div className="flex flex-col gap-2 border-b border-black/10 px-3 py-2">
            <div className="flex items-center gap-2">
              <input
                ref={vinRef}
                className="flex-1 bg-transparent font-mono text-sm uppercase tracking-wide outline-none"
                placeholder="17-character VIN"
                value={vin}
                maxLength={17}
                autoCapitalize="characters"
                onChange={(e) => {
                  const next = e.target.value.toUpperCase().replace(/[^A-HJ-NPR-Z0-9]/g, "");
                  setVin(next);
                  if (next.length === 17) void lookupVin(next);
                }}
              />
              {vin && (
                <button
                  type="button"
                  className="text-xs text-steel"
                  onClick={() => {
                    setVin("");
                    setVehicle(null);
                    setVinError(null);
                  }}
                >
                  Clear
                </button>
              )}
            </div>
            {vehicle?.label && (
              <p className="text-xs text-steel">
                Decoded: {vehicle.label}
                {vehicle.source ? ` · ${VIN_SOURCE_LABEL[vehicle.source] || vehicle.source}` : ""}
              </p>
            )}
            {vehicle?.note && <p className="text-xs text-steel">{vehicle.note}</p>}
            {vehicle?.history && (
              <div className="space-y-1 text-[11px] text-steel">
                <p className="font-medium text-ink">History</p>
                {vehicle.history.note && <p>{vehicle.history.note}</p>}
                {vehicle.history.stolen && vehicle.history.stolen.length > 0 && (
                  <p>
                    Stolen check:{" "}
                    {vehicle.history.stolen
                      .map((row) => `${row.region} ${row.status}`)
                      .join(" · ")}
                  </p>
                )}
                {vehicle.history.stolen_note && <p>{vehicle.history.stolen_note}</p>}
                {vehicle.history.market && (
                  <p>
                    EU listings: {vehicle.history.market.currency}{" "}
                    {vehicle.history.market.low?.toLocaleString()}–
                    {vehicle.history.market.high?.toLocaleString()} (median{" "}
                    {vehicle.history.market.median?.toLocaleString()})
                    {vehicle.history.market.odometer_median_km
                      ? ` · typical ${vehicle.history.market.odometer_median_km.toLocaleString()} km`
                      : ""}
                    {vehicle.history.market.markets?.length
                      ? ` · ${vehicle.history.market.markets.join(", ")}`
                      : ""}
                  </p>
                )}
                {vehicle.history.market_note && <p>{vehicle.history.market_note}</p>}
              </div>
            )}
            {vehicle?.specs && vehicle.specs.length > 0 && (
              <dl className="grid max-h-48 grid-cols-2 gap-x-3 gap-y-1.5 overflow-y-auto pr-1 text-[11px] text-steel">
                {vehicle.specs.map((spec) => (
                  <div key={spec.label}>
                    <dt className="text-black/40">{spec.label}</dt>
                    <dd className="text-ink">{spec.value}</dd>
                  </div>
                ))}
              </dl>
            )}
            {vinError && <p className="text-xs text-rust">{vinError}</p>}
          </div>
        )}
        <div className="flex items-end gap-1 px-2 py-2">
          <button
            type="button"
            title="Add VIN so Hoodwise can identify the car"
            onClick={() => setVinOpen((open) => !open)}
            className={`mb-0.5 rounded-full p-2 ${vin || vinOpen ? "bg-ink text-paper" : "text-steel hover:bg-black/5"}`}
          >
            <VinIcon active={Boolean(vin)} />
          </button>
          <input
            className="min-w-0 flex-1 bg-transparent px-2 py-2 text-sm outline-none"
            placeholder={vin ? "Ask about this car…" : "Ask a question — add a VIN with the car icon"}
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <button
            type="submit"
            disabled={busy}
            className="rounded-full bg-rust px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {busy ? "…" : "Ask"}
          </button>
        </div>
      </form>
    </div>
  );
}
