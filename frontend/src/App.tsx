import { useEffect, useState } from "react";
import { api, type Health } from "./api/client";
import Conversation from "./components/Conversation";
import SourcesPanel from "./components/SourcesPanel";

function Status({ ok, label, title }: { ok: boolean; label: string; title: string }) {
  return (
    <span title={title} className="hidden items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600 sm:flex">
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-500" : "bg-rose-500"}`} />
      {label}
    </span>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [sourcesVersion, setSourcesVersion] = useState(0);
  const [drawer, setDrawer] = useState(false); // data sources on small screens

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealthError(true));
  }, []);

  const panel = <SourcesPanel onChange={() => setSourcesVersion((v) => v + 1)} />;

  return (
    <div className="flex h-dvh flex-col bg-slate-50 text-slate-900">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-sky-500 text-white" aria-hidden>
            <svg viewBox="0 0 24 24" className="h-4.5 w-4.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2" />
            </svg>
          </div>
          <div>
            <h1 className="text-[15px] font-semibold leading-tight">Voice Data Analyst</h1>
            <p className="hidden text-xs text-slate-500 sm:block">Ask your databases questions out loud</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Status ok={!healthError && health?.status === "ok"} label="Server" title="Backend API" />
          <Status ok={!!health?.llm_key_set} label="Language model" title={`LLM provider: ${health?.llm_provider ?? "?"}`} />
          <Status ok={!healthError && !!health} label="Voice on this computer" title="Speech recognition and speech synthesis run locally" />
          <button onClick={() => setDrawer(true)} className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 lg:hidden">
            Data
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-80 shrink-0 overflow-y-auto border-r border-slate-200 bg-white p-4 lg:block">{panel}</aside>
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <Conversation sourcesVersion={sourcesVersion} />
        </main>
      </div>

      {drawer && (
        <div className="fixed inset-0 z-20 flex lg:hidden" role="dialog" aria-label="Data sources">
          <div className="absolute inset-0 bg-slate-900/30" onClick={() => setDrawer(false)} />
          <div className="relative ml-auto h-full w-[88%] max-w-sm overflow-y-auto bg-white p-4 shadow-xl">
            <button onClick={() => setDrawer(false)} className="mb-3 text-sm text-slate-500">Close</button>
            {panel}
          </div>
        </div>
      )}
    </div>
  );
}
