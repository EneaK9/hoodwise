"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { User } from "@/lib/types";

export function Nav() {
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    api.me().then((r) => setUser(r.user)).catch(() => setUser(null));
  }, []);

  return (
    <header className="border-b border-black/10 bg-paper/80 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
        <Link href="/" className="font-mono text-sm tracking-[0.2em] uppercase">
          Hoodwise
        </Link>
        <nav className="flex items-center gap-4 text-sm">
          <Link href="/" className="hover:text-rust">
            Chat
          </Link>
          <Link href="/history" className="hover:text-rust">
            History
          </Link>
          <Link href="/garage" className="hover:text-rust">
            Garage
          </Link>
          {user ? (
            <button
              className="text-steel"
              onClick={async () => {
                await api.logout();
                setUser(null);
              }}
            >
              {user.email} · out
            </button>
          ) : (
            <>
              <Link href="/login">Log in</Link>
              <Link href="/signup" className="rounded bg-ink px-3 py-1 text-paper">
                Sign up
              </Link>
            </>
          )}
        </nav>
      </div>
    </header>
  );
}
