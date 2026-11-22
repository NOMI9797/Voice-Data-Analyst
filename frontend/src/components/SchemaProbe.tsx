import { useState } from "react";
import { api, type Retrieval } from "../api/client";

/** Developer tool: type a question and see which source + tables the retriever picks. */
export default function SchemaProbe() {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Retrieval | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSchema, setShowSchema] = useState(false);

  async function go(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      setRes(await api.retrieve(q));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold">Schema retrieval test</h3>
      <p className="text-xs text-slate-500">Shows which source and tables would be sent to the SQL generator.</p>
      <form onSubmit={go} className="mt-3 flex gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="e.g. which product categories have the worst reviews?"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-sky-500 focus:outline-none"
        />
        <button disabled={busy} className="rounded-md bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-40">
          {busy ? "…" : "Find"}
        </button>
      </form>
      {err && <p className="mt-2 text-sm text-rose-700">{err}</p>}
      {res && (
        <div className="mt-3 space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{res.source_name ?? "no source"}</span>
            {res.ambiguous_source && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">ambiguous source</span>
            )}
            <span className="text-xs text-slate-500">{res.ms} ms</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {res.tables.map((t) => (
              <span
                key={t.table}
                title={`score ${t.score}${t.value_hits.length ? " · " + t.value_hits.join(", ") : ""}`}
                className={`rounded px-2 py-0.5 font-mono text-xs ${t.bridge ? "bg-slate-100 text-slate-600" : "bg-sky-100 text-sky-900"}`}
              >
                {t.table}
                {t.value_hits.length > 0 && " ★"}
              </span>
            ))}
          </div>
          <p className="text-xs text-slate-500">
            {res.source_scores.map((s) => `${s.name}: ${s.score}`).join(" · ")}
          </p>
          <button onClick={() => setShowSchema(!showSchema)} className="text-xs text-sky-700 hover:underline">
            {showSchema ? "Hide" : "Show"} prompt schema
          </button>
          {showSchema && (
            <pre className="max-h-96 overflow-auto rounded-md bg-slate-900 p-3 text-[11px] leading-snug text-slate-100">
              {res.schema_text}
            </pre>
          )}
        </div>
      )}
    </section>
  );
}
