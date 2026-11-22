import { useEffect, useState } from "react";
import { getHealth, type Health } from "./api/client";

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`rounded-full px-3 py-1 text-sm font-medium ${
        ok ? "bg-emerald-100 text-emerald-800" : "bg-rose-100 text-rose-800"
      }`}
    >
      {label}
    </span>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHealth().then(setHealth).catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-3xl px-4 py-12">
        <h1 className="text-3xl font-bold">Voice Data Analyst</h1>
        <p className="mt-2 text-slate-600">Ask your databases questions out loud.</p>

        <section className="mt-8 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-lg font-semibold">System status</h2>
          {error && <p className="text-rose-700">Backend unreachable: {error}</p>}
          {!health && !error && <p className="text-slate-500">Checking…</p>}
          {health && (
            <div className="flex flex-wrap gap-2">
              <Badge ok={health.status === "ok"} label={`API: ${health.status}`} />
              <Badge
                ok={health.llm_key_set}
                label={`LLM (${health.llm_provider}): ${health.llm_key_set ? "key set" : "no key"}`}
              />
              {Object.entries(health.databases).map(([name, s]) => (
                <Badge key={name} ok={s === "ok"} label={`${name}: ${s}`} />
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
