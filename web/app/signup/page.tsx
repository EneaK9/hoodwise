"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

export default function SignupPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  return (
    <main className="mx-auto max-w-sm px-4 py-16">
      <h1 className="text-2xl">Create an account</h1>
      <p className="mt-2 text-sm text-steel">Saves your garage and chat history. Guest chats can be claimed after signup.</p>
      <form
        className="mt-6 flex flex-col gap-3"
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            await api.signup(email, password);
            const sessionId = localStorage.getItem("hoodwise_session");
            if (sessionId) {
              await api.claim(sessionId).catch(() => undefined);
            }
            router.push("/");
            router.refresh();
          } catch (err) {
            setError(err instanceof Error ? err.message : "Signup failed");
          }
        }}
      >
        <input className="rounded border px-3 py-2" type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <input className="rounded border px-3 py-2" type="password" placeholder="Password (10+ chars)" value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <p className="text-sm text-rust">{error}</p>}
        <button className="rounded bg-ink py-2 text-paper">Sign up</button>
      </form>
    </main>
  );
}
