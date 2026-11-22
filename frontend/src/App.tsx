import { useEffect, useState } from "react";
import { api, type Health } from "./api/client";
import SourcesPanel from "./components/SourcesPanel";

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="flex items-center gap-1.5 text-xs text-slate-600">
      <span className={`h-2 w-2 rounded-full ${ok ? "bg-emerald-500" : "bg-rose-500"}`} />
      {label}
    </span>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealthError(true));
  }, []);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-3">
          <div>
            <h1 className="text-xl font-bold">Voice Data Analyst</h1>
            <p className="text-xs text-slate-500">Ask your databases questions out loud</p>
          </div>
          <div className="flex gap-4">
            <StatusDot ok={!healthError && health?.status === "ok"} label="API" />
            <StatusDot ok={!!health?.llm_key_set} label={`LLM (${health?.llm_provider ?? "?"})`} />
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-7xl gap-6 px-4 py-6 md:grid-cols-[300px_1fr]">
        <SourcesPanel />

        <section className="flex min-h-[60vh] flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white p-8 text-center">
          <div className="text-5xl">🎙️</div>
          <h2 className="mt-4 text-lg font-semibold">Ask a question</h2>
          <p className="mt-1 max-w-md text-sm text-slate-500">
            Voice questions, charts, tables and spoken answers will appear here (coming in the next steps).
          </p>
        </section>
      </main>
    </div>
  );
}
