import { useEffect, useState } from "react";
import type { ChartType, SqlRun } from "../api/client";
import ChartView from "./ChartView";
import DataTable from "./DataTable";
import { humanize, toCsv } from "./format";

type Tab = "chart" | "table" | "sql";
type Plottable = "kpi" | "line" | "bar" | "scatter";
const PLOTTABLE: ChartType[] = ["kpi", "line", "bar", "scatter"];
const TYPE_LABEL: Record<ChartType, string> = { kpi: "Number", line: "Line", bar: "Bar", scatter: "Scatter", table: "Table", empty: "Empty" };

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  URL.revokeObjectURL(url);
}

function title(spec: Extract<SqlRun, { ok: true }>["chart"]): string | null {
  if (!spec.x || spec.y.length === 0 || spec.type === "kpi") return null; // cards are self-labelled
  const y = spec.y.map(humanize).join(" and ");
  if (spec.type === "scatter") return `${humanize(spec.y[0])} vs ${humanize(spec.x).toLowerCase()}`;
  return `${y} by ${humanize(spec.x).toLowerCase()}${spec.series ? `, per ${humanize(spec.series).toLowerCase()}` : ""}`;
}

/** Shows a query result as a chart, a table and the SQL. `onRun` re-runs edited SQL. */
export default function ResultView({ result, sql, onRun, running }: {
  result: SqlRun | null;
  sql: string;
  onRun: (sql: string) => void;
  running: boolean;
}) {
  const ok = result?.ok ? result : null;
  const plottable = ok ? PLOTTABLE.includes(ok.chart.type) : false;
  const [tab, setTab] = useState<Tab>(ok && PLOTTABLE.includes(ok.chart.type) ? "chart" : "sql");
  const [chartType, setChartType] = useState<Plottable>("bar");
  const [draft, setDraft] = useState(sql);
  const [copied, setCopied] = useState(false);

  // new result: back to the recommended view
  useEffect(() => {
    if (!result) setDraft(sql); // new question
    else if (ok) setDraft(ok.sql); // the guard's normalized SQL; a blocked query keeps the user's text
    if (ok) {
      setChartType((PLOTTABLE.includes(ok.chart.type) ? ok.chart.type : "bar") as Plottable);
      setTab(PLOTTABLE.includes(ok.chart.type) ? "chart" : "table");
    } else setTab("sql");
  }, [result]); // eslint-disable-line react-hooks/exhaustive-deps

  const tabs: { id: Tab; label: string; show: boolean }[] = [
    { id: "chart", label: "Chart", show: plottable },
    { id: "table", label: ok ? `Table (${ok.row_count.toLocaleString()})` : "Table", show: !!ok },
    { id: "sql", label: "SQL", show: true },
  ];
  const switchable = ok ? [ok.chart.type, ...ok.chart.alternatives].filter((t): t is Plottable => PLOTTABLE.includes(t)) : [];

  return (
    <div className="rounded-lg border border-slate-200">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-3 py-2">
        <div className="flex gap-1" role="tablist">
          {tabs.filter((t) => t.show).map((t) => (
            <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
              className={`rounded-md px-3 py-1 text-sm ${tab === t.id ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>
              {t.label}
            </button>
          ))}
        </div>
        {ok && (
          <div className="flex items-center gap-3 text-xs text-slate-500">
            <span>
              {ok.row_count.toLocaleString()} row{ok.row_count === 1 ? "" : "s"} · {ok.ms} ms
            </span>
            {ok.row_count > 0 && (
              <button onClick={() => download("result.csv", toCsv(ok.columns, ok.rows))} className="text-sky-700 hover:underline">
                Download CSV
              </button>
            )}
          </div>
        )}
      </div>

      <div className="p-4">
        {ok?.truncated && tab !== "sql" && (
          <p className="mb-3 rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-800">
            Only the first {ok.row_count.toLocaleString()} rows are shown. Add filters or grouping to see everything.
          </p>
        )}

        {tab === "chart" && ok && plottable && (
          <div>
            <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
              <div>
                {title(ok.chart) && <p className="text-sm font-medium text-slate-900">{title(ok.chart)}</p>}
                <p className="text-xs text-slate-500">{ok.chart.reason}</p>
              </div>
              {switchable.length > 1 && (
                <div className="flex gap-1" aria-label="Chart type">
                  {switchable.map((t) => (
                    <button key={t} onClick={() => setChartType(t)}
                      className={`rounded border px-2 py-0.5 text-xs ${chartType === t ? "border-slate-900 bg-slate-900 text-white" : "border-slate-300 text-slate-600 hover:bg-slate-50"}`}>
                      {TYPE_LABEL[t]}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <ChartView spec={ok.chart} type={chartType} columns={ok.columns} rows={ok.rows} />
            {ok.chart.note && <p className="mt-2 text-xs text-slate-500">{ok.chart.note}</p>}
          </div>
        )}

        {tab === "table" && ok && (ok.row_count === 0 ? (
          <p className="text-sm text-slate-500">The query ran but returned no rows.</p>
        ) : (
          <DataTable columns={ok.columns} rows={ok.rows} />
        ))}

        {tab === "sql" && (
          <div className="space-y-2">
            <textarea value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false}
              rows={Math.min(14, Math.max(4, draft.split("\n").length + 1))}
              onKeyDown={(e) => {
                if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && draft.trim()) onRun(draft);
              }}
              className="w-full rounded-md bg-slate-900 p-3 font-mono text-xs leading-relaxed text-slate-100 focus:outline-none focus:ring-2 focus:ring-sky-500" />
            <div className="flex flex-wrap items-center gap-2">
              <button disabled={running || !draft.trim()} onClick={() => onRun(draft)}
                className="rounded-md bg-slate-900 px-3 py-1 text-xs text-white disabled:opacity-40">
                {running ? "Running…" : "Run SQL"}
              </button>
              <button onClick={() => navigator.clipboard?.writeText(draft).then(() => {
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1200);
                })}
                className="rounded-md border border-slate-300 px-3 py-1 text-xs text-slate-700">
                {copied ? "Copied" : "Copy"}
              </button>
              <span className="text-xs text-slate-400">⌘/Ctrl + Enter to run · read-only, checked by the safety guard</span>
            </div>
            {result && !result.ok && (
              <p className="rounded-md bg-rose-50 p-2 text-xs text-rose-700">
                {result.blocked ? `Blocked by the safety guard: ${result.reason}` : `Database error: ${result.error}`}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
