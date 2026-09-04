"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function HistoryPage() {
  const [sessions, setSessions] = useState<Array<{ id: string; title: string; created_at: string }>>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.sessions().then((r) => setSessions(r.sessions)).catch((e) => setError(e.message));
  }, []);

  return (
    <main className="mx-auto max-w-xl px-4 py-10">
      <h1 className="text-2xl">History</h1>
      <p className="mt-1 text-sm text-steel">Logged-in chats only. Guest sessions stay on this device until claimed.</p>
      {error && <p className="mt-3 text-sm text-rust">{error}</p>}
      <ul className="mt-6 space-y-2">
        {sessions.map((s) => (
          <li key={s.id} className="rounded border border-black/10 bg-white px-3 py-2 text-sm">
            <Link href={`/?session=${s.id}`}>{s.title || "Untitled"}</Link>
            <div className="text-xs text-steel">{new Date(s.created_at).toLocaleString()}</div>
          </li>
        ))}
        {sessions.length === 0 && <li className="text-sm text-steel">No saved sessions yet.</li>}
      </ul>
    </main>
  );
}
