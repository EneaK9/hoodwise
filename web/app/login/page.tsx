"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <main className="mx-auto max-w-sm px-4 py-16">
      <h1 className="text-2xl">Log in</h1>
      <form
        className="mt-6 flex flex-col gap-3"
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            await api.login(email, password);
            router.push("/");
            router.refresh();
          } catch (err) {
            setError(err instanceof Error ? err.message : "Login failed");
          }
        }}
      >
        <input className="rounded border px-3 py-2" type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input className="rounded border px-3 py-2" type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <p className="text-sm text-rust">{error}</p>}
        <button className="rounded bg-ink py-2 text-paper">Log in</button>
      </form>
    </main>
  );
}
