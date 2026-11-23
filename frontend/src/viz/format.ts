import type { Cell } from "../api/client";

/** Categorical series colors, fixed order (validated palette, light mode). Never cycled:
 *  results with more series than this are capped by the backend (max 8). */
export const SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];

/** order_purchase_timestamp -> Order purchase timestamp, CategoryName -> Category name */
export function humanize(name: string): string {
  const spaced = name
    .replace(/_/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

const full = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

/** Value in a table cell or tooltip: full precision, thousands separators. */
export function formatValue(v: Cell): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return full.format(v);
  if (typeof v === "boolean") return v ? "true" : "false";
  return v;
}

/** Axis tick: full under 10,000, compact (12K, 3.4M) above. */
export function formatTick(v: unknown): string {
  if (typeof v !== "number") return String(v ?? "");
  return Math.abs(v) < 10_000 ? full.format(v) : compact.format(v);
}

/** Headline number: compact for big values, full otherwise. */
export function formatHeadline(v: Cell): string {
  if (typeof v !== "number") return formatValue(v);
  return Math.abs(v) >= 1_000_000 ? compact.format(v) : full.format(v);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** True when every value is a whole month (2017-03 or 2017-03-01[ 00:00:00]). */
export function isMonthly(values: Cell[]): boolean {
  const vals = values.filter((v) => v !== null);
  return vals.length > 0 && vals.every((v) => typeof v === "string" && /^\d{4}-\d{2}(-01([ T]00:00:00(\.0+)?([+-]\d{2}:?\d{2}|Z)?)?)?$/.test(v));
}

/** Time axis label. Years stay plain (2021, not 2,021); whole months read "Mar 2017";
 *  a midnight time part is dropped so axes stay short. */
export function formatTemporal(v: Cell, monthly = false): string {
  if (typeof v === "number") return String(v);
  if (typeof v !== "string") return formatValue(v);
  if (monthly) {
    const m = /^(\d{4})-(\d{2})/.exec(v);
    if (m) return `${MONTHS[Number(m[2]) - 1]} ${m[1]}`;
  }
  return v.replace(/[ T]00:00:00(\.0+)?([+-]\d{2}:?\d{2}|Z)?$/, "");
}

/** Round axis ticks from 0 (or the minimum, if negative): steps of 1, 2, 2.5 or 5 x 10^k. */
export function niceTicks(min: number, max: number, target = 5): number[] {
  const lo = Math.min(0, min);
  const hi = Math.max(0, max);
  if (hi === lo) return [0, 1];
  const raw = (hi - lo) / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw) ?? 10 * mag;
  const ticks: number[] = [];
  for (let t = Math.floor(lo / step) * step; t < hi + step - 1e-9; t += step) ticks.push(Number(t.toFixed(10)));
  return ticks;
}

export function toCsv(columns: string[], rows: Cell[][]): string {
  const esc = (v: Cell) => {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [columns.map(esc).join(","), ...rows.map((r) => r.map(esc).join(","))].join("\r\n");
}
