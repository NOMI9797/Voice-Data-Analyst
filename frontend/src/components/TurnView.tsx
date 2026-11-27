import { Suspense, lazy, useState } from "react";
import type { AskResponse, CorrectionResult, Pending, SqlRun } from "../api/client";
import { phaseLabel, type Phase } from "./VoiceOrb";

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
  refused: { label: "Refused for safety", tone: "bg-amber-100 text-amber-900" },
  cannot_answer: { label: "Not in this data", tone: "bg-slate-200 text-slate-700" },
  failed: { label: "Couldn't answer", tone: "bg-rose-100 text-rose-800" },
  clarify: { label: "Needs your choice", tone: "bg-indigo-100 text-indigo-800" },
  no_source: { label: "No data source", tone: "bg-slate-200 text-slate-700" },
  not_ready: { label: "Still analysing", tone: "bg-slate-200 text-slate-700" },
};

/** Follow-ups that edit the previous query (module 9), offered where they make sense. */
function followUps(reply: AskResponse): string[] {
  const chart = reply.result?.chart;
  if (!chart || reply.status !== "answered") return [];
  const temporal = chart.x ? chart.column_types[chart.x] === "temporal" : false;
  if (chart.type === "kpi") return ["Now break it down by month", "Same for last year"];
  if (temporal) return ["Same for last year", "Show it by year instead"];
  if (chart.type === "bar") return ["Show only the top 5", "Now break it down by month"];
  return [];
}

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
  db: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3Zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
};

/** The question as the system understood it, with corrected names highlighted. */
function UserBubble({ turn }: { turn: TurnData }) {
  const fixes = turn.correction?.corrections ?? [];
  const shown = turn.reply?.question ?? turn.correction?.corrected ?? turn.asked;
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-slate-900 px-4 py-2.5 text-white shadow-sm">
        <p className="text-[15px] leading-relaxed">{shown}</p>
        {(turn.viaVoice || fixes.length > 0) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-white/15 pt-1.5 text-xs text-slate-300">
            {turn.viaVoice && (
              <span className="inline-flex items-center gap-1" title="Transcribed by Whisper on this computer">
                <Icon d={ICON.mic} className="h-3 w-3" /> heard “{turn.asked}”
              </span>
            )}
            {fixes.map((c, i) => (
              <span key={i} className="inline-flex items-center gap-1"
                title={`Matched a real value in ${c.locations.map((l) => `${l.source} · ${l.table}.${l.column}`).join(", ")}`}>
                <span className="text-rose-300 line-through">{c.original}</span>
                <span>→</span>
                <span className="rounded bg-emerald-400/20 px-1 font-medium text-emerald-200">{c.corrected}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Details({ turn }: { turn: TurnData }) {
  const [open, setOpen] = useState(false);
  const [schema, setSchema] = useState(false);
  const r = turn.reply;
  const retrieval = r?.retrieval;
  const timings = Object.entries(turn.timings);
  if (!timings.length && !retrieval) return null;
  return (
    <div className="mt-3 border-t border-slate-100 pt-2">
      <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800" aria-expanded={open}>
        <Icon d={ICON.chevron} className={`h-3 w-3 transition ${open ? "" : "-rotate-90"}`} /> Details: timing, tables used, query attempts
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

export default function TurnView({ turn, isLatest, phase, userSpeaking, pending, onPickSource, onReply, onReplay,
  onRunSql, sqlRunning }: {
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
}) {
  const [copied, setCopied] = useState(false);
  const r = turn.reply;
  const status = r ? STATUS[r.status] : undefined;
  const firstAudio = turn.timings["end of question → first audio"];

  return (
    <div className="fade-up space-y-3">
      <UserBubble turn={turn} />

      <div className="flex gap-3">
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-sky-500 text-white shadow-sm" aria-hidden>
          <Icon d={ICON.db} className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1 rounded-2xl rounded-tl-md border border-slate-200 bg-white p-4 shadow-sm">
          {turn.loading ? (
            <div className="flex items-center gap-2 text-sm text-slate-500" aria-live="polite">
              <span className="flex gap-1">
                {[0, 0.15, 0.3].map((d) => (
                  <span key={d} className="orb-bar h-2 w-2 rounded-full bg-indigo-400" style={{ animationDelay: `${d}s` }} />
                ))}
              </span>
              {phaseLabel(phase === "idle" || phase === "listening" ? "thinking" : phase, userSpeaking)}
            </div>
          ) : turn.error ? (
            <p className="text-sm text-rose-700">{turn.error}</p>
          ) : r ? (
            <>
              <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs">
                {r.source_name && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 font-medium text-slate-600">
                    <Icon d={ICON.db} className="h-3 w-3" /> {r.source_name}
                    {r.switched_source && " (switched)"}
                  </span>
                )}
                {status && <span className={`rounded-full px-2 py-0.5 font-medium ${status.tone}`}>{status.label}</span>}
              </div>

              <p className="text-[17px] leading-relaxed text-slate-900" aria-live={isLatest ? "polite" : undefined}>{r.answer}</p>

              {r.status === "clarify" && isLatest && pending && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  {pending.kind === "source"
                    ? pending.options.map((o, i) => (
                        <button key={o.source_id} onClick={() => onPickSource(pending.question, o.source_id)}
                          className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm font-medium text-indigo-900 hover:bg-indigo-100">
                          {i + 1}. {o.name}
                        </button>
                      ))
                    : pending.options.map((o, i) => (
                        <button key={o} onClick={() => onReply(o)}
                          className="rounded-full border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm font-medium text-indigo-900 hover:bg-indigo-100">
                          {i + 1}. {o}
                        </button>
                      ))}
                  <span className="text-xs text-slate-500">or just say your answer</span>
                </div>
              )}

              {r.other_sources && r.other_sources.length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="text-xs text-slate-500">Meant other data?</span>
                  {r.other_sources.map((o) => (
                    <button key={o.source_id} onClick={() => onPickSource(r.question, o.source_id)}
                      className="rounded-full border border-slate-200 bg-white px-3 py-1 text-sm text-slate-700 hover:border-indigo-300 hover:bg-indigo-50">
                      Answer from {o.name}
                    </button>
                  ))}
                </div>
              )}

              {(r.assumption || (r.attempts.length > 0 && r.status === "answered")) && (
                <div className="mt-2 space-y-0.5 text-xs text-slate-500">
                  {r.assumption && <p>Assumed: {r.assumption}</p>}
                  {r.attempts.length > 0 && r.status === "answered" && (
                    <p>The first query didn't work and was fixed automatically ({r.attempts.length} {r.attempts.length === 1 ? "repair" : "repairs"}).</p>
                  )}
                </div>
              )}

              <div className="mt-3 flex flex-wrap items-center gap-1 text-xs">
                <button onClick={() => onReplay(r.answer)} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-slate-600 hover:bg-slate-100">
                  <Icon d={ICON.speaker} /> Play answer
                </button>
                <button onClick={() => navigator.clipboard?.writeText(r.answer).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200); })}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-slate-600 hover:bg-slate-100">
                  <Icon d={copied ? ICON.check : ICON.copy} /> {copied ? "Copied" : "Copy"}
                </button>
                {firstAudio !== undefined && (
                  <span className="ml-auto text-slate-400" title="From the moment you stopped speaking to the first sound of the answer">
                    answered in {(firstAudio / 1000).toFixed(1)} s
                  </span>
                )}
              </div>

              {(r.result || r.sql) && (
                <div className="mt-3">
                  <Suspense fallback={<p className="text-xs text-slate-400">Loading chart…</p>}>
                    <ResultView result={turn.sqlResult ?? null} sql={r.sql ?? ""} onRun={(sql) => onRunSql(turn.id, sql)} running={sqlRunning} />
                  </Suspense>
                </div>
              )}

              {isLatest && followUps(r).length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <span className="text-xs text-slate-500">Follow up:</span>
                  {followUps(r).map((f) => (
                    <button key={f} onClick={() => onReply(f)}
                      className="rounded-full border border-slate-200 bg-white px-3 py-1 text-sm text-slate-700 hover:border-slate-300 hover:bg-slate-50">
                      {f}
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
