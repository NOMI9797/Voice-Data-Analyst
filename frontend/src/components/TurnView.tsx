import { Suspense, lazy, useState } from "react";
import type { AskResponse, CorrectionResult, Pending, SqlRun } from "../api/client";
import type { Phase } from "./VoiceOrb";

const ResultView = lazy(() => import("../viz/ResultView")); // charts library loads on first use

export type Timings = Record<string, number>;

export type TurnData = {
  id: number;
  asked: string; // what was typed, or what speech-to-text heard
  viaVoice: boolean;
  correction?: CorrectionResult | null;
  reply?: AskResponse;
  sqlResult?: SqlRun | null;
  timings: Timings;
  error?: string;
  loading: boolean;
};

const STATUS: Record<string, { label: string; tone: string }> = {
  refused: { label: "Refused for safety", tone: "bg-amber-50 text-amber-800 ring-amber-200" },
  cannot_answer: { label: "Not in this data", tone: "bg-slate-50 text-slate-600 ring-slate-200" },
  failed: { label: "Couldn't answer", tone: "bg-rose-50 text-rose-700 ring-rose-200" },
  clarify: { label: "Needs your choice", tone: "bg-indigo-50 text-indigo-700 ring-indigo-200" },
  no_source: { label: "No data source", tone: "bg-slate-50 text-slate-600 ring-slate-200" },
  not_ready: { label: "Still analysing", tone: "bg-sky-50 text-sky-700 ring-sky-200" },
};

/** Follow-ups that edit the previous query (module 9), offered where they make sense. */
// Chooses the follow-up chips ('Same for last year', 'Now break it down by month').
function followUps(reply: AskResponse): string[] {
  const chart = reply.result?.chart;
  if (!chart || reply.status !== "answered") return [];
  const temporal = chart.x ? chart.column_types[chart.x] === "temporal" : false;
  if (chart.type === "kpi") return ["Now break it down by month", "Same for last year"];
  if (temporal) return ["Same for last year", "Show it by year instead"];
  if (chart.type === "bar") return ["Show only the top 5", "Now break it down by month"];
  return [];
}

// Small line icon.
function Icon({ d, className = "h-3.5 w-3.5" }: { d: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}
const ICON = {
  speaker: "M11 5 6 9H2v6h4l5 4V5Zm4.5 3.5a5 5 0 0 1 0 7M19 5a9 9 0 0 1 0 14",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  check: "M20 6 9 17l-5-5",
  chevron: "m6 9 6 6 6-6",
  mic: "M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Zm7 9a7 7 0 0 1-14 0m7 7v4",
  arrow: "M5 12h14M13 6l6 6-6 6",
  layers: "M12 3 3 8l9 5 9-5-9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
  db: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
};

/** The question as the system understood it, with corrected names highlighted. */
// The user's question bubble: what was heard, and corrected names in green.
function UserBubble({ turn }: { turn: TurnData }) {
  const fixes = turn.correction?.corrections ?? [];
  const shown = turn.reply?.question ?? turn.correction?.corrected ?? turn.asked;
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-3xl rounded-br-lg bg-gradient-to-br from-indigo-600 to-violet-600 px-5 py-3 text-white shadow-[0_6px_20px_rgba(79,70,229,0.25)]">
        <p className="text-[15px] leading-relaxed">{shown}</p>
        {(turn.viaVoice || fixes.length > 0) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-indigo-100/80">
            {turn.viaVoice && (
              <span className="inline-flex items-center gap-1" title="Transcribed by Whisper on this computer">
                <Icon d={ICON.mic} className="h-3 w-3" /> heard “{turn.asked}”
              </span>
            )}
            {fixes.map((c, i) => (
              <span key={i} className="inline-flex items-center gap-1"
                title={`Matched a real value in ${c.locations.map((l) => `${l.source} · ${l.table}.${l.column}`).join(", ")}`}>
                <span className="text-rose-200 line-through">{c.original}</span>
                <span>→</span>
                <span className="rounded-md bg-white/20 px-1.5 font-medium text-white">{c.corrected}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// What the app is doing right now, as a short checklist (instead of a spinner).
function Progress({ phase }: { phase: Phase }) {
  const steps: { k: Phase; label: string }[] = [
    { k: "correcting", label: "Matching names to your data" },
    { k: "thinking", label: "Writing and checking the SQL" },
  ];
  const at = steps.findIndex((x) => x.k === phase);
  return (
    <ol className="space-y-2" aria-live="polite">
      {steps.map((x, i) => {
        const done = at > i;
        const now = at === i || (at === -1 && i === steps.length - 1);
        return (
          <li key={x.k} className={`flex items-center gap-2.5 text-sm ${now ? "text-slate-800" : done ? "text-slate-400" : "text-slate-300"}`}>
            <span className={`flex h-5 w-5 items-center justify-center rounded-full ${done ? "bg-emerald-100 text-emerald-600" : now ? "bg-indigo-100" : "bg-slate-100"}`}>
              {done ? <Icon d={ICON.check} className="h-3 w-3" /> : now ? <span className="orb-spin h-3 w-3 rounded-full border-2 border-indigo-200 border-t-indigo-600" /> : null}
            </span>
            {x.label}
          </li>
        );
      })}
    </ol>
  );
}

// The collapsible Details: time per step, tables used, repaired attempts.
function Details({ turn }: { turn: TurnData }) {
  const [open, setOpen] = useState(false);
  const [schema, setSchema] = useState(false);
  const r = turn.reply;
  const retrieval = r?.retrieval;
  const timings = Object.entries(turn.timings);
  if (!timings.length && !retrieval) return null;
  return (
    <div className="mt-4 border-t border-slate-100 pt-3">
      <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-800" aria-expanded={open}>
        <Icon d={ICON.layers} className="h-3.5 w-3.5" /> How I got this
        <Icon d={ICON.chevron} className={`h-3 w-3 transition ${open ? "" : "-rotate-90"}`} />
      </button>
      {open && (
        <div className="mt-2 space-y-3 text-xs text-slate-600">
          {timings.length > 0 && (
            <div>
              <p className="mb-1 font-medium text-slate-500">Time per stage (ms)</p>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {timings.map(([k, v]) => (
                  <span key={k}>{k}: <b className="font-mono text-slate-800">{v.toLocaleString()}</b></span>
                ))}
              </div>
            </div>
          )}
          {retrieval && retrieval.tables.length > 0 && (
            <div>
              <p className="mb-1 font-medium text-slate-500">Tables sent to the language model ({retrieval.source_name})</p>
              <div className="flex flex-wrap gap-1.5">
                {retrieval.tables.map((t) => (
                  <span key={t.table} title={`score ${t.score}${t.value_hits.length ? " · " + t.value_hits.join(", ") : ""}`}
                    className={`rounded px-1.5 py-0.5 font-mono text-[11px] ${t.bridge ? "bg-slate-100 text-slate-600" : "bg-sky-100 text-sky-900"}`}>
                    {t.table}{t.value_hits.length > 0 && " ★"}
                  </span>
                ))}
              </div>
              <button onClick={() => setSchema(!schema)} className="mt-1 text-sky-700 hover:underline">
                {schema ? "Hide" : "Show"} the schema text
              </button>
              {schema && (
                <pre className="mt-1 max-h-72 overflow-auto rounded-md bg-slate-900 p-3 text-[11px] leading-snug text-slate-100">{retrieval.schema_text}</pre>
              )}
            </div>
          )}
          {r && r.attempts.length > 0 && (
            <div>
              <p className="mb-1 font-medium text-slate-500">Query attempts that were repaired</p>
              <ul className="space-y-1">
                {r.attempts.map((a, i) => (
                  <li key={i} className="rounded bg-slate-50 p-1.5">
                    <span className="font-medium">{a.stage}</span>: {a.error}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// MAIN: one question and its answer card (answer, buttons, chart/table/SQL, follow-ups).
export default function TurnView({ turn, isLatest, phase, userSpeaking, pending, onPickSource, onReply, onReplay,
  onRunSql, sqlRunning, onEnableSource }: {
  turn: TurnData;
  isLatest: boolean;
  phase: Phase;
  userSpeaking: boolean;
  pending: Pending | null;
  onPickSource: (question: string, sourceId: string) => void;
  onReply: (text: string) => void;
  onReplay: (text: string) => void;
  onRunSql: (turnId: number, sql: string) => void;
  sqlRunning: boolean;
  onEnableSource: (sourceId: string, question: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const r = turn.reply;
  const status = r ? STATUS[r.status] : undefined;
  const firstAudio = turn.timings["end of question → first audio"];

  const optionCls = "group flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-left text-sm font-medium text-slate-800 transition hover:border-indigo-300 hover:bg-indigo-50/60";
  const num = (i: number) => (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-500 group-hover:bg-indigo-100 group-hover:text-indigo-700">{i + 1}</span>
  );

  return (
    <div className="fade-up space-y-4">
      <UserBubble turn={turn} />

      <div className="flex gap-3">
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 via-violet-500 to-fuchsia-500 text-white shadow-md shadow-indigo-500/20" aria-hidden>
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2" /></svg>
        </div>
        <div className="min-w-0 flex-1 rounded-3xl rounded-tl-lg border border-slate-200/80 bg-white p-5 shadow-[0_1px_3px_rgba(15,23,42,0.04),0_8px_24px_rgba(15,23,42,0.04)]">
          {turn.loading ? (
            <Progress phase={phase === "idle" || phase === "listening" ? "thinking" : phase} />
          ) : turn.error ? (
            <p className="text-sm text-rose-700">{turn.error}</p>
          ) : r ? (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
                {r.switched_off_source ? (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-50 px-2.5 py-1 font-medium text-slate-500 ring-1 ring-slate-200">
                    <Icon d={ICON.db} className="h-3 w-3" /> {r.switched_off_source.name} (switched off)
                  </span>
                ) : r.source_name && (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-50 px-2.5 py-1 font-medium text-slate-600 ring-1 ring-slate-200">
                    <Icon d={ICON.db} className="h-3 w-3" /> {r.source_name}
                    {r.switched_source && <span className="text-slate-400">· switched</span>}
                  </span>
                )}
                {status && <span className={`rounded-full px-2.5 py-1 font-medium ring-1 ${status.tone}`}>{status.label}</span>}
                <span className="ml-auto flex items-center gap-0.5">
                  {firstAudio !== undefined && (
                    <span className="mr-1.5 tabular-nums text-slate-400" title="From the moment you stopped speaking to the first sound of the answer">
                      {(firstAudio / 1000).toFixed(1)} s
                    </span>
                  )}
                  <button onClick={() => onReplay(r.answer)} title="Play answer" aria-label="Play answer"
                    className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700">
                    <Icon d={ICON.speaker} className="h-4 w-4" />
                  </button>
                  <button onClick={() => navigator.clipboard?.writeText(r.answer).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); })}
                    title={copied ? "Copied" : "Copy answer"} aria-label="Copy answer"
                    className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700">
                    <Icon d={copied ? ICON.check : ICON.copy} className="h-4 w-4" />
                  </button>
                </span>
              </div>

              <p className="text-[18px] leading-relaxed text-slate-900" aria-live={isLatest ? "polite" : undefined}>{r.answer}</p>

              {r.status === "clarify" && isLatest && pending && (
                <div className="mt-4">
                  <div className="grid gap-2 sm:grid-cols-2">
                    {pending.kind === "source"
                      ? pending.options.map((o, i) => (
                          <button key={o.source_id} onClick={() => onPickSource(pending.question, o.source_id)} className={optionCls}>
                            {num(i)}
                            <span className="flex-1">{o.name}</span>
                            <Icon d={ICON.db} className="h-3.5 w-3.5 text-slate-300" />
                          </button>
                        )).concat(pending.options.length > 1 ? [
                          <button key="*all*" onClick={() => onPickSource(pending.question, "*all*")}
                            className="group flex items-center gap-3 rounded-xl border border-indigo-200 bg-indigo-50/70 px-3.5 py-2.5 text-left text-sm font-medium text-indigo-800 transition hover:border-indigo-300 hover:bg-indigo-100">
                            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-indigo-600 text-white">
                              <Icon d={ICON.layers} className="h-3.5 w-3.5" />
                            </span>
                            <span className="flex-1">All of them</span>
                            <span className="text-xs font-normal text-indigo-500">combined</span>
                          </button>,
                        ] : [])
                      : pending.options.map((o, i) => (
                          <button key={o} onClick={() => onReply(o)} className={optionCls}>
                            {num(i)}
                            <span className="flex-1">{o}</span>
                          </button>
                        ))}
                  </div>
                  <p className="mt-2 inline-flex items-center gap-1.5 text-xs text-slate-500">
                    <Icon d={ICON.mic} className="h-3 w-3" /> or just say your answer
                  </p>
                </div>
              )}

              {r.switched_off_source && isLatest && (
                <div className="mt-4">
                  <button onClick={() => onEnableSource(r.switched_off_source!.source_id, r.question)}
                    className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-500">
                    Turn on {r.switched_off_source.name} and ask <Icon d={ICON.arrow} />
                  </button>
                </div>
              )}

              {r.other_sources && r.other_sources.length > 0 && (
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <span className="text-xs text-slate-500">Meant other data?</span>
                  {r.other_sources.map((o) => (
                    <button key={o.source_id} onClick={() => onPickSource(r.question, o.source_id)}
                      className="rounded-full border border-slate-200 bg-white px-3 py-1 text-sm text-slate-700 transition hover:border-indigo-300 hover:bg-indigo-50">
                      Answer from {o.name}
                    </button>
                  ))}
                </div>
              )}

              {(r.assumption || (r.attempts.length > 0 && r.status === "answered")) && (
                <div className="mt-3 space-y-1 rounded-xl bg-slate-50 px-3 py-2 text-xs text-slate-500">
                  {r.assumption && <p><span className="font-medium text-slate-600">Assumed:</span> {r.assumption}</p>}
                  {r.attempts.length > 0 && r.status === "answered" && (
                    <p>The first query didn't work and was fixed automatically ({r.attempts.length} {r.attempts.length === 1 ? "repair" : "repairs"}).</p>
                  )}
                </div>
              )}

              {(r.result || r.sql) && (
                <div className="mt-4">
                  <Suspense fallback={<div className="h-40 animate-pulse rounded-2xl bg-slate-50" />}>
                    <ResultView result={turn.sqlResult ?? null} sql={r.sql ?? ""} onRun={(sql) => onRunSql(turn.id, sql)} running={sqlRunning} />
                  </Suspense>
                </div>
              )}

              {isLatest && followUps(r).length > 0 && (
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">Ask next</span>
                  {followUps(r).map((f) => (
                    <button key={f} onClick={() => onReply(f)}
                      className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-700 transition hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-800">
                      <span className="text-indigo-400" aria-hidden>↳</span> {f}
                    </button>
                  ))}
                </div>
              )}

              <Details turn={turn} />
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
