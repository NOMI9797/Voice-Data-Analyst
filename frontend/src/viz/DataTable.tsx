import { useMemo, useState } from "react";
import type { Cell } from "../api/client";
import { formatValue, humanize } from "./format";

const PAGE = 25;

// Sort order for table values (numbers, text, blanks last).
function compare(a: Cell, b: Cell): number {
  if (a === b) return 0;
  if (a === null) return 1; // empty values last
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

// MAIN: the sortable, paged results table.
export default function DataTable({ columns, rows }: { columns: string[]; rows: Cell[][] }) {
  const [sort, setSort] = useState<{ col: number; dir: 1 | -1 } | null>(null);
  const [page, setPage] = useState(0);

  const numeric = useMemo(
    () => columns.map((_, i) => rows.some((r) => typeof r[i] === "number") && rows.every((r) => r[i] === null || typeof r[i] === "number")),
    [columns, rows],
  );
  const sorted = useMemo(() => {
    if (!sort) return rows;
    return [...rows].sort((a, b) => sort.dir * compare(a[sort.col], b[sort.col]));
  }, [rows, sort]);

  const pages = Math.max(1, Math.ceil(sorted.length / PAGE));
  const current = Math.min(page, pages - 1);
  const visible = sorted.slice(current * PAGE, current * PAGE + PAGE);

  // Click a column header to sort by it.
  function toggle(i: number) {
    setPage(0);
    setSort((s) => (s?.col !== i ? { col: i, dir: numeric[i] ? -1 : 1 } : s.dir === (numeric[i] ? -1 : 1) ? { col: i, dir: s.dir === 1 ? -1 : 1 } : null));
  }

  return (
    <div>
      <div className="overflow-x-auto rounded-xl border border-slate-200">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
            <tr>
              {columns.map((c, i) => (
                <th key={i} className={`whitespace-nowrap px-3 py-2 font-medium ${numeric[i] ? "text-right" : ""}`}>
                  <button onClick={() => toggle(i)} className="inline-flex items-center gap-1 hover:text-slate-900" title={c}>
                    {humanize(c)}
                    <span className="w-2 text-slate-400">{sort?.col === i ? (sort.dir === 1 ? "▲" : "▼") : ""}</span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {visible.map((r, ri) => (
              <tr key={ri} className="hover:bg-slate-50">
                {r.map((v, i) => (
                  <td key={i} className={`max-w-xs truncate px-3 py-1.5 ${numeric[i] ? "text-right font-mono tabular-nums" : ""} ${v === null ? "text-slate-400" : "text-slate-800"}`}
                    title={formatValue(v)}>
                    {formatValue(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="mt-2 flex items-center justify-end gap-2 text-xs text-slate-600">
          <button disabled={current === 0} onClick={() => setPage(current - 1)} className="rounded border border-slate-300 px-2 py-0.5 disabled:opacity-40">
            ‹ Prev
          </button>
          <span>
            Page {current + 1} of {pages}
          </span>
          <button disabled={current >= pages - 1} onClick={() => setPage(current + 1)} className="rounded border border-slate-300 px-2 py-0.5 disabled:opacity-40">
            Next ›
          </button>
        </div>
      )}
    </div>
  );
}
