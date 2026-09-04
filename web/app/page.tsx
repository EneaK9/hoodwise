import { Chat } from "@/components/Chat";

export default function HomePage() {
  return (
    <main>
      <div className="mx-auto max-w-3xl px-4 pt-8">
        <h1 className="text-3xl font-medium tracking-tight">Ask the factory manual.</h1>
        <p className="mt-2 text-sm text-steel">
          Add a VIN in the composer. Hoodwise identifies the car, then answers from the factory page.
        </p>
      </div>
      <Chat />
    </main>
  );
}
