import type { ChatResponse, User, Variant } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : "Request failed");
  }
  return res.json();
}

export const api = {
  me: () => req<{ user: User | null }>("/api/me"),
  signup: (email: string, password: string) =>
    req<{ user: User }>("/api/auth/signup", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string) =>
    req<{ user: User }>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => req<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  claim: (session_id: string) =>
    req<{ ok: boolean }>("/api/auth/claim", { method: "POST", body: JSON.stringify({ session_id }) }),
  vehicles: () => req<{ variants: Variant[] }>("/api/vehicles"),
  garage: () => req<{ vehicles: Array<Record<string, unknown>> }>("/api/garage"),
  addGarage: (variant_id: string, nickname?: string, vin?: string) =>
    req<{ id: string }>("/api/garage", { method: "POST", body: JSON.stringify({ variant_id, nickname, vin }) }),
  decodeVin: (vin: string) =>
    req<{ decode: Record<string, unknown> }>("/api/vin/decode", { method: "POST", body: JSON.stringify({ vin }) }),
  setFuel: (vin: string, fuel: "diesel" | "gasoline") =>
    req<{ decode: Record<string, unknown> }>("/api/vin/fuel", { method: "POST", body: JSON.stringify({ vin, fuel }) }),
  chat: (payload: { session_id?: string; message: string; variant_id?: string; vin?: string }) =>
    req<ChatResponse>("/api/chat", { method: "POST", body: JSON.stringify(payload) }),
  sessions: () => req<{ sessions: Array<{ id: string; title: string; created_at: string }> }>("/api/sessions"),
  messages: (id: string) =>
    req<{ messages: Array<{ id: string; role: string; content: string; citations: unknown[] }> }>(
      `/api/sessions/${id}/messages`,
    ),
};
