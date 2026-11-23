import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Cell, ChartSpec } from "../api/client";
import { SERIES_COLORS, formatHeadline, formatTemporal, formatTick, formatValue, humanize, isMonthly, niceTicks } from "./format";

const INK = "#52514e";
const GRID = "#e7e5e4";
const AXIS = { fontSize: 11, fill: INK };

type Point = Record<string, Cell>;
type Series = { key: string; name: string };

/** Rows -> recharts data. Values live under safe keys (v0, v1, ...) so column
 *  names with dots or spaces can't be misread as object paths. */
function shape(spec: ChartSpec, columns: string[], rows: Cell[][]): { data: Point[]; series: Series[] } {
  const col = (name: string) => columns.indexOf(name);
  const xi = spec.x ? col(spec.x) : -1;
  const temporal = spec.x ? spec.column_types[spec.x] === "temporal" : false;
  const monthly = temporal && isMonthly(rows.map((r) => r[xi]));
  const label = (v: Cell) => (temporal ? formatTemporal(v, monthly) : v === null ? "(none)" : v);
  let data: Point[];
  let series: Series[];

  if (spec.series) {
    // long -> wide: one value column per series member (backend caps members at 8)
    const si = col(spec.series);
    const yi = col(spec.y[0]);
    const members: string[] = [];
    const byX = new Map<string, Point>();
    for (const r of rows) {
      const m = String(r[si] ?? "(none)");
      let k = members.indexOf(m);
      if (k === -1) {
        if (members.length >= SERIES_COLORS.length) continue;
        members.push(m);
        k = members.length - 1;
      }
      const xKey = String(r[xi]);
      const p = byX.get(xKey) ?? { __x: label(r[xi]), __sort: r[xi] };
      p[`v${k}`] = r[yi];
      byX.set(xKey, p);
    }
    data = [...byX.values()];
    series = members.map((m, k) => ({ key: `v${k}`, name: m }));
  } else {
    const yIdx = spec.y.map(col);
    data = rows.map((r) => {
      const p: Point = { __x: label(r[xi]), __sort: r[xi] };
      yIdx.forEach((yi, k) => (p[`v${k}`] = r[yi]));
      return p;
    });
    series = spec.y.map((y, k) => ({ key: `v${k}`, name: humanize(y) }));
  }

  if (temporal) data.sort((a, b) => (String(a.__sort) < String(b.__sort) ? -1 : String(a.__sort) > String(b.__sort) ? 1 : 0));
  if (spec.limit) data = data.slice(0, spec.limit);
  return { data, series };
}

function Kpis({ spec, columns, rows }: { spec: ChartSpec; columns: string[]; rows: Cell[][] }) {
  return (
    <div className="flex flex-wrap gap-3">
      {spec.y.map((y) => {
        const v = rows[0][columns.indexOf(y)];
        return (
          <div key={y} className="min-w-40 flex-1 rounded-lg border border-slate-200 px-4 py-3">
            <p className="text-xs text-slate-500">{humanize(y)}</p>
            <p className="mt-1 text-3xl font-semibold tabular-nums text-slate-900" title={formatValue(v)}>
              {formatHeadline(v)}
            </p>
          </div>
        );
      })}
    </div>
  );
}

const tooltipProps = {
  formatter: (v: unknown) => formatValue(v as Cell),
  contentStyle: { fontSize: 12, borderRadius: 6, borderColor: GRID },
  labelStyle: { color: "#0b0b0b", fontWeight: 600 },
};

export default function ChartView({
  spec,
  type,
  columns,
  rows,
}: {
  spec: ChartSpec;
  type: "kpi" | "line" | "bar" | "scatter";
  columns: string[];
  rows: Cell[][];
}) {
  const { data, series } = useMemo(() => shape(spec, columns, rows), [spec, columns, rows]);
  const multi = series.length > 1;
  const values = data.flatMap((d) => series.map((s) => d[s.key])).filter((v): v is number => typeof v === "number");
  const ticks = values.length ? niceTicks(Math.min(...values), Math.max(...values)) : undefined;
  const domain: [number, number] | undefined = ticks ? [ticks[0], ticks[ticks.length - 1]] : undefined;
  const legend = <Legend wrapperStyle={{ fontSize: 12 }} formatter={(v) => <span style={{ color: INK }}>{v}</span>} />;
  const yLabel = spec.series ? humanize(spec.y[0]) : series.length === 1 ? series[0].name : undefined;

  if (type === "kpi") return <Kpis spec={spec} columns={columns} rows={rows} />;

  if (type === "scatter") {
    const xi = columns.indexOf(spec.x!);
    const yi = columns.indexOf(spec.y[0]);
    const pts = rows.map((r) => ({ x: r[xi], y: r[yi] }));
    return (
      <ResponsiveContainer width="100%" height={320}>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
          <CartesianGrid stroke={GRID} />
          <XAxis type="number" dataKey="x" name={humanize(spec.x!)} tick={AXIS} tickFormatter={formatTick}
            label={{ value: humanize(spec.x!), position: "insideBottom", offset: -12, ...AXIS }} />
          <YAxis type="number" dataKey="y" name={humanize(spec.y[0])} tick={AXIS} tickFormatter={formatTick} width={56} />
          <Tooltip {...tooltipProps} cursor={{ strokeDasharray: "3 3" }} />
          <Scatter data={pts} fill={SERIES_COLORS[0]} fillOpacity={0.6} stroke="#fff" strokeWidth={1} />
        </ScatterChart>
      </ResponsiveContainer>
    );
  }

  if (type === "line") {
    return (
      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 8 }}>
          <CartesianGrid stroke={GRID} vertical={false} />
          <XAxis dataKey="__x" tick={AXIS} minTickGap={24} />
          <YAxis tick={AXIS} tickFormatter={formatTick} width={56} ticks={ticks} domain={domain} interval={0}
            label={yLabel ? { value: yLabel, angle: -90, position: "insideLeft", ...AXIS, style: { textAnchor: "middle" } } : undefined} />
          <Tooltip {...tooltipProps} />
          {multi && legend}
          {series.map((s, k) => (
            <Line key={s.key} dataKey={s.key} name={s.name} stroke={SERIES_COLORS[k]} strokeWidth={2}
              dot={data.length <= 24 ? { r: 3, strokeWidth: 2, fill: SERIES_COLORS[k], stroke: "#fff" } : false}
              activeDot={{ r: 5, stroke: "#fff", strokeWidth: 2 }} connectNulls isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    );
  }

  // bar
  const horizontal = spec.orientation === "horizontal";
  const longest = Math.max(...data.map((d) => String(d.__x).length), 4);
  const height = horizontal ? Math.max(240, data.length * (multi ? 34 : 28) + 48) : 320;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout={horizontal ? "vertical" : "horizontal"} barGap={2} barCategoryGap="20%"
        margin={{ top: 8, right: 24, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={GRID} vertical={horizontal} horizontal={!horizontal} />
        {horizontal ? (
          <>
            <XAxis type="number" tick={AXIS} tickFormatter={formatTick} ticks={ticks} domain={domain} interval={0} />
            <YAxis type="category" dataKey="__x" tick={AXIS} interval={0} width={Math.min(180, longest * 6.5 + 12)} />
          </>
        ) : (
          <>
            <XAxis dataKey="__x" tick={AXIS} interval={data.length > 12 ? "preserveStartEnd" : 0} />
            <YAxis tick={AXIS} tickFormatter={formatTick} width={56} ticks={ticks} domain={domain} interval={0}
              label={yLabel ? { value: yLabel, angle: -90, position: "insideLeft", ...AXIS, style: { textAnchor: "middle" } } : undefined} />
          </>
        )}
        <Tooltip {...tooltipProps} cursor={{ fill: "rgba(15,23,42,0.05)" }} />
        {multi && legend}
        {series.map((s, k) => (
          <Bar key={s.key} dataKey={s.key} name={s.name} fill={SERIES_COLORS[k]} maxBarSize={24}
            radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} isAnimationActive={false} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
