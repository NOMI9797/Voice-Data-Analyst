import { useEffect, useState } from "react";
import { api, type Health } from "./api/client";
import Conversation from "./components/Conversation";
import SourcesPanel from "./components/SourcesPanel";

// The app mark: a small sound wave.
export function Logo({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <div className={`flex shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 via-violet-500 to-fuchsia-500 text-white shadow-lg shadow-indigo-500/20 ${className}`} aria-hidden>
      <svg viewBox="0 0 24 24" className="h-1/2 w-1/2" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
        <path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2" />
      </svg>
    </div>
  );
}

// One status row at the bottom of the data rail.
function Status({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <div className="flex items-center gap-2" title={detail}>
      <span className={`h-1.5 w-1.5 rounded-full ${ok ? "bg-emerald-400 shadow-[0_0_6px] shadow-emerald-400/70" : "bg-rose-400"}`} />
      <span className="text-slate-300">{label}</span>
      <span className="ml-auto truncate text-slate-500">{detail}</span>
    </div>
  );
}

// The dark left rail: brand, data sources, system status.
function Rail({ health, healthError, onChange, onClose }: {
  health: Health | null;
  healthError: boolean;
  onChange: () => void;
  onClose?: () => void;
}) {
  const up = !healthError && health?.status === "ok";
  return (
    <div className="flex h-full flex-col bg-[#0c0f1d] text-slate-200">
      <div className="flex items-center gap-3 px-5 pb-4 pt-5">
        <Logo />
        <div className="min-w-0">
          <p className="text-[15px] font-semibold leading-tight text-white">Voice Data Analyst</p>
          <p className="text-xs text-slate-400">Ask your databases out loud</p>
        </div>
        {onClose && (
          <button onClick={onClose} className="ml-auto rounded-lg px-2 py-1 text-sm text-slate-400 hover:bg-white/5 hover:text-white">
            Close
          </button>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        <SourcesPanel onChange={onChange} />
      </div>
      <div className="space-y-1.5 border-t border-white/5 px-5 py-4 text-xs">
        <Status ok={up} label="Server" detail={up ? "connected" : "offline"} />
        <Status ok={!!health?.llm_key_set} label="Language model" detail={health?.llm_provider ?? "…"} />
        <Status ok={!healthError && !!health} label="Voice" detail="on this computer" />
      </div>
    </div>
  );
}

// MAIN: the page layout: dark data rail on the left, the conversation on the right.
export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [sourcesVersion, setSourcesVersion] = useState(0);
  const [drawer, setDrawer] = useState(false); // data sources on small screens

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealthError(true));
  }, []);

  const bump = () => setSourcesVersion((v) => v + 1);

  return (
    <div className="flex h-dvh bg-[#f5f6fa] text-slate-900">
      <aside className="hidden w-[300px] shrink-0 lg:block">
        <Rail health={health} healthError={healthError} onChange={bump} />
      </aside>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Conversation sourcesVersion={sourcesVersion} onOpenData={() => setDrawer(true)} />
      </main>

      {drawer && (
        <div className="fixed inset-0 z-30 flex lg:hidden" role="dialog" aria-label="Data sources">
          <div className="absolute inset-0 bg-slate-950/50 backdrop-blur-sm" onClick={() => setDrawer(false)} />
          <div className="relative h-full w-[88%] max-w-sm shadow-2xl">
            <Rail health={health} healthError={healthError} onChange={bump} onClose={() => setDrawer(false)} />
          </div>
        </div>
      )}
    </div>
  );
}
