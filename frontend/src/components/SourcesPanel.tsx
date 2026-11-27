import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Source } from "../api/client";

const ENGINE_LABEL: Record<Source["engine"], string> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  sqlite: "SQLite",
  duckdb: "CSV · DuckDB",
};

const ENGINE_COLOR: Record<Source["engine"], string> = {
  postgresql: "bg-sky-100 text-sky-800",
  mysql: "bg-orange-100 text-orange-800",
  sqlite: "bg-violet-100 text-violet-800",
  duckdb: "bg-amber-100 text-amber-800",
};

function ProfileBadge({ status }: { status?: string }) {
  if (!status || status === "ready") return <span className="text-emerald-700">· profiled</span>;
  if (status.startsWith("error")) return <span className="text-rose-700" title={status}>· profile failed</span>;
  return <span className="animate-pulse text-sky-700">· {status}…</span>;
}

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
    <li className={`rounded-lg border bg-white p-3 ${source.active ? "border-slate-200" : "border-dashed border-slate-300 opacity-60"}`}>
      <div className="flex items-start gap-2">
        <span
          title={source.error ?? "Connected"}
          className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${source.status === "ok" ? "bg-emerald-500" : "bg-rose-500"}`}
        />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{source.name}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
            <span className={`rounded px-1.5 py-0.5 font-medium ${ENGINE_COLOR[source.engine]}`}>
              {ENGINE_LABEL[source.engine]}
            </span>
            <button onClick={() => setOpen(!open)} className="text-slate-500 hover:text-slate-800">
              {tables.length} tables {open ? "▴" : "▾"}
            </button>
            <ProfileBadge status={source.profile_status} />
          </div>
          {source.error && <p className="mt-1 text-xs text-rose-700">{source.error}</p>}
        </div>
        <label className="flex cursor-pointer items-center" title={source.active ? "Used for questions" : "Ignored"}>
          <input type="checkbox" className="peer sr-only" checked={source.active} onChange={onToggle} />
          <span className="h-5 w-9 rounded-full bg-slate-300 transition peer-checked:bg-emerald-500 after:ml-0.5 after:mt-0.5 after:block after:h-4 after:w-4 after:rounded-full after:bg-white after:transition peer-checked:after:translate-x-4" />
        </label>
      </div>
      {open && (
        <div className="mt-2 border-t border-slate-100 pt-2">
          <p className="truncate font-mono text-[11px] text-slate-500" title={source.display_url}>
            {source.display_url}
          </p>
          <ul className="mt-1 max-h-40 overflow-y-auto font-mono text-xs text-slate-700">
            {tables.map((t) => (
              <li key={t} className="truncate">• {t}</li>
            ))}
          </ul>
          <div className="mt-2 flex gap-3">
            <button onClick={onReprofile} className="text-xs text-sky-700 hover:underline">
              Re-profile
            </button>
            <button onClick={onRemove} className="text-xs text-rose-600 hover:underline">
              Remove source
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

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
  }, [refresh]);

  const profiling = sources.some((s) => s.profile_status && !["ready", "missing"].includes(s.profile_status) && !s.profile_status.startsWith("error"));
  useEffect(() => {
    if (!profiling) return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [profiling, refresh]);

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

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    if (await run(() => api.addConnection(name, url))) {
      setUrl("");
      setName("");
    }
  }

  function upload(files: FileList | null) {
    if (!files || files.length === 0) return;
    run(() => api.uploadFiles(Array.from(files)));
    if (fileInput.current) fileInput.current.value = "";
  }

  const activeCount = sources.filter((s) => s.active).length;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Your data</h2>
        <span className="text-xs text-slate-500">{activeCount} of {sources.length} in use</span>
      </div>

      {loading ? (
        <p className="text-sm text-slate-500">Loading…</p>
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
          {sources.length === 0 && <li className="text-sm text-slate-500">No sources yet. Add one below.</li>}
        </ul>
      )}

      {error && <p className="rounded-md bg-rose-50 p-2 text-sm text-rose-700">{error}</p>}

      <details className="group rounded-lg border border-slate-200 bg-slate-50/60 open:bg-white" open={sources.length === 0}>
        <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2 text-sm font-medium text-slate-700">
          Add a data source
          <span className="text-slate-400 transition group-open:rotate-45">+</span>
        </summary>
        <div className="flex flex-col gap-3 px-3 pb-3">
      <form onSubmit={connect} className="flex flex-col gap-2">
        <span className="text-sm font-medium">Connect a database</span>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="postgresql://user:pass@host:5432/db"
          className="rounded-md border border-slate-300 px-2 py-1.5 font-mono text-xs focus:border-sky-500 focus:outline-none"
        />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name (optional)"
          className="rounded-md border border-slate-300 px-2 py-1.5 text-sm focus:border-sky-500 focus:outline-none"
        />
        <button
          disabled={busy || !url.trim()}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          {busy ? "Working…" : "Connect"}
        </button>
        <p className="text-[11px] leading-snug text-slate-500">
          PostgreSQL, MySQL, SQLite path. Connections are read-only.
        </p>
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
        className={`cursor-pointer rounded-lg border-2 border-dashed p-4 text-center text-sm transition ${
          dragOver ? "border-sky-500 bg-sky-50" : "border-slate-300 bg-white hover:border-slate-400"
        }`}
      >
        <p className="font-medium">Upload files</p>
        <p className="text-xs text-slate-500">CSV files (one table each) or a SQLite .db</p>
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
