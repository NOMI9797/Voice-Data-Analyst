import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Source } from "../api/client";

const ENGINE_LABEL: Record<Source["engine"], string> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  sqlite: "SQLite",
  duckdb: "CSV · DuckDB",
};

const ENGINE_COLOR: Record<Source["engine"], string> = {
  postgresql: "bg-sky-400",
  mysql: "bg-orange-400",
  sqlite: "bg-violet-400",
  duckdb: "bg-amber-400",
};

// Shows a source's profiling status (profiled, describing...).
function ProfileBadge({ status }: { status?: string }) {
  if (!status || status === "ready") return null;
  if (status.startsWith("error")) return <span className="text-rose-400" title={status}>profile failed</span>;
  return <span className="ml-1 animate-pulse text-sky-300">{status}…</span>;
}

// One database in the rail: engine, tables, on/off switch.
function SourceCard({
  source,
  onToggle,
  onRemove,
  onReprofile,
}: {
  source: Source;
  onToggle: () => void;
  onRemove: () => void;
  onReprofile: () => void;
}) {
  const [open, setOpen] = useState(false);
  const tables = source.tables ?? [];
  return (
    <li className={`rounded-xl border p-3 transition ${source.active ? "border-white/10 bg-white/[0.04] hover:bg-white/[0.07]" : "border-dashed border-white/10 bg-transparent opacity-55"}`}>
      <div className="flex items-start gap-3">
        <div className="relative mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-white/5 text-slate-300" aria-hidden>
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
            <path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
          </svg>
          <span title={source.error ?? "Connected"}
            className={`absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full border-2 border-[#0c0f1d] ${source.status === "ok" ? "bg-emerald-400" : "bg-rose-400"}`} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-white">{source.name}</div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-400">
            <span className="mr-1 inline-flex items-center gap-1.5">
              <span className={`h-1.5 w-1.5 rounded-full ${ENGINE_COLOR[source.engine]}`} />
              {ENGINE_LABEL[source.engine]}
            </span>
            <button onClick={() => setOpen(!open)} className="hover:text-white" aria-expanded={open}>
              {tables.length} tables {open ? "▴" : "▾"}
            </button>
            <ProfileBadge status={source.profile_status} />
          </div>
          {source.error && <p className="mt-1 text-xs text-rose-300">{source.error}</p>}
        </div>
        <label className="mt-1 flex cursor-pointer items-center" title={source.active ? "Used for questions" : "Ignored"}>
          <input type="checkbox" className="peer sr-only" checked={source.active} onChange={onToggle} />
          <span className="h-5 w-9 rounded-full bg-white/15 transition peer-checked:bg-indigo-500 peer-focus-visible:ring-2 peer-focus-visible:ring-indigo-300 after:ml-0.5 after:mt-0.5 after:block after:h-4 after:w-4 after:rounded-full after:bg-white after:shadow after:transition peer-checked:after:translate-x-4" />
        </label>
      </div>
      {open && (
        <div className="mt-3 border-t border-white/5 pt-2">
          <p className="truncate font-mono text-[11px] text-slate-500" title={source.display_url}>
            {source.display_url}
          </p>
          <ul className="mt-1.5 grid max-h-40 grid-cols-2 gap-x-2 overflow-y-auto font-mono text-[11px] text-slate-300">
            {tables.map((t) => (
              <li key={t} className="truncate" title={t}>{t}</li>
            ))}
          </ul>
          <div className="mt-2 flex gap-3 text-xs">
            <button onClick={onReprofile} className="text-indigo-300 hover:text-indigo-200">Re-profile</button>
            <button onClick={onRemove} className="text-rose-300 hover:text-rose-200">Remove</button>
          </div>
        </div>
      )}
    </li>
  );
}

// MAIN: the sidebar: list databases, switch on/off, connect, upload.
export default function SourcesPanel({ onChange }: { onChange?: () => void }) {
  const [sources, setSources] = useState<Source[]>([]);
  const signature = useRef("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // Reloads the list of databases from the backend.
  const refresh = useCallback(async () => {
    try {
      const list = await api.listSources();
      setSources(list);
      // tell the conversation when sources were added, removed, toggled or finished profiling
      const sig = list.map((s) => `${s.id}:${s.active}:${s.profile_status}`).join("|");
      if (sig !== signature.current) {
        signature.current = sig;
        onChange?.();
      }
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      setLoading(false);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    refresh();
    // a source switched on from the conversation ("Turn on ... and ask")
    window.addEventListener("sources-changed", refresh);
    return () => window.removeEventListener("sources-changed", refresh);
  }, [refresh]);

  const profiling = sources.some((s) => s.profile_status && !["ready", "missing"].includes(s.profile_status) && !s.profile_status.startsWith("error"));
  useEffect(() => {
    if (!profiling) return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [profiling, refresh]);

  // Runs a sidebar action and refreshes the list.
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await refresh();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  // Connects a database from the pasted connection string.
  async function connect(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    if (await run(() => api.addConnection(name, url))) {
      setUrl("");
      setName("");
    }
  }

  // Uploads CSV or SQLite files as a new source.
  function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    run(() => api.uploadFiles(Array.from(files)));
    if (fileInput.current) fileInput.current.value = "";
  }

  const activeCount = sources.filter((s) => s.active).length;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between px-2 pt-1">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">Data sources</h2>
        <span className="text-[11px] text-slate-500">{activeCount} of {sources.length} on</span>
      </div>

      {loading ? (
        <p className="px-2 text-sm text-slate-500">Loading…</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {sources.map((s) => (
            <SourceCard
              key={s.id}
              source={s}
              onToggle={() => run(() => api.setActive(s.id, !s.active))}
              onReprofile={() => run(() => api.reprofile(s.id))}
              onRemove={() => {
                if (confirm(`Remove "${s.name}"?`)) run(() => api.removeSource(s.id));
              }}
            />
          ))}
          {sources.length === 0 && <li className="px-2 text-sm text-slate-400">No sources yet. Add one below.</li>}
        </ul>
      )}

      {error && <p className="rounded-lg bg-rose-500/10 p-2 text-sm text-rose-300">{error}</p>}

      <details className="group rounded-xl border border-white/10 bg-white/[0.02] open:bg-white/[0.04]" open={sources.length === 0}>
        <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2.5 text-sm font-medium text-slate-200 hover:text-white">
          <span className="inline-flex items-center gap-2">
            <span className="flex h-5 w-5 items-center justify-center rounded-md bg-indigo-500/20 text-indigo-300" aria-hidden>+</span>
            Add a data source
          </span>
        </summary>
        <div className="flex flex-col gap-3 px-3 pb-3">
          <form onSubmit={connect} className="flex flex-col gap-2">
            <span className="text-xs font-medium text-slate-400">Connection string</span>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="postgresql://user:pass@host:5432/db"
              className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-2 font-mono text-xs text-white placeholder:text-slate-500 focus:border-indigo-400 focus:outline-none"
            />
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name (optional)"
              className="rounded-lg border border-white/10 bg-white/5 px-2.5 py-2 text-sm text-white placeholder:text-slate-500 focus:border-indigo-400 focus:outline-none"
            />
            <button
              disabled={busy || !url.trim()}
              className="rounded-lg bg-indigo-500 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-400 disabled:opacity-40"
            >
              {busy ? "Working…" : "Connect"}
            </button>
            <p className="text-[11px] leading-snug text-slate-500">PostgreSQL, MySQL or SQLite. Connections are read-only.</p>
          </form>

          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              upload(e.dataTransfer.files);
            }}
            onClick={() => fileInput.current?.click()}
            className={`cursor-pointer rounded-lg border border-dashed p-4 text-center text-sm transition ${
              dragOver ? "border-indigo-400 bg-indigo-500/10" : "border-white/15 hover:border-white/30"
            }`}
          >
            <p className="font-medium text-slate-200">Drop a file or click to upload</p>
            <p className="mt-0.5 text-[11px] text-slate-500">CSV (one table each) or a SQLite .db</p>
            <input
              ref={fileInput}
              type="file"
              multiple
              accept=".csv,.tsv,.txt,.db,.sqlite,.sqlite3"
              className="hidden"
              onChange={(e) => upload(e.target.files)}
            />
          </div>
        </div>
      </details>
    </div>
  );
}
