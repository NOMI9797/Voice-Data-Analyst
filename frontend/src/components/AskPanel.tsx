import { Suspense, lazy, useState } from "react";
import { api, type AskResponse, type CorrectionResult, type Retrieval, type SqlRun, type Turn } from "../api/client";
import { useRecorder, type Recording } from "../hooks/useRecorder";
import { useSpeaker } from "../hooks/useSpeaker";
const ResultView = lazy(() => import("../viz/ResultView")); // charts library loads on first use

type Timings = Record<string, number>;

function MicButton({
  state,
  level,
  speaking,
  onClick,
}: {
  state: "idle" | "listening" | "processing";
  level: number;
  speaking: boolean;
  onClick: () => void;
}) {
  const ring = state === "listening" ? 8 + level * 40 : 0;
  return (
    <button
      onClick={onClick}
      aria-label={state === "listening" ? "Stop recording" : "Start recording"}
      className={`relative flex h-20 w-20 items-center justify-center rounded-full text-white shadow-lg transition ${
        state === "listening" ? "bg-rose-600" : state === "processing" ? "bg-slate-400" : "bg-slate-900 hover:bg-slate-700"
      }`}
      style={{ boxShadow: state === "listening" ? `0 0 0 ${ring}px rgba(225,29,72,${speaking ? 0.25 : 0.12})` : undefined }}
      disabled={state === "processing"}
    >
      {state === "processing" ? (
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-white border-t-transparent" />
      ) : state === "listening" ? (
        <span className="h-6 w-6 rounded-sm bg-white" />
      ) : (
        <svg viewBox="0 0 24 24" className="h-8 w-8" fill="currentColor">
          <path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z" />
        </svg>
      )}
    </button>
  );
}

export default function AskPanel() {
  const [question, setQuestion] = useState("");
  const [heard, setHeard] = useState<string | null>(null);
  const [correction, setCorrection] = useState<CorrectionResult | null>(null);
  const [retrieval, setRetrieval] = useState<Retrieval | null>(null);
  const [timings, setTimings] = useState<Timings>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showSchema, setShowSchema] = useState(false);
  const [sqlResult, setSqlResult] = useState<SqlRun | null>(null);
  const [sqlRunning, setSqlRunning] = useState(false);
  const [reply, setReply] = useState<AskResponse | null>(null);
  const [history, setHistory] = useState<Turn[]>([]);
  const speaker = useSpeaker();

  async function handle(text: string, t: Timings, speechEndAt: number | null) {
    setBusy(true);
    setError(null);
    try {
      const tc = performance.now();
      const fix = await api.correct(text);
      t["word correction"] = Math.round(performance.now() - tc);
      setCorrection(fix);
      text = fix.corrected;
      const t0 = performance.now();
      const res = await api.ask(text, history);
      t["ask (server total)"] = Math.round(performance.now() - t0);
      for (const [k, v] of Object.entries(res.timings)) if (k !== "total") t[`· ${k}`] = Math.round(v);
      setReply(res);
      setRetrieval(res.retrieval);
      // a failed query still shows what was tried, with its last error, in the SQL tab
      const last = res.attempts[res.attempts.length - 1];
      setSqlResult(
        res.result ??
          (res.sql && last
            ? last.stage === "guard"
              ? { ok: false, blocked: true, code: last.code ?? "", reason: last.error }
              : { ok: false, blocked: false, sql: res.sql, error: last.error }
            : null),
      );
      if (res.status === "answered" && res.sql && res.source_id)
        setHistory((h) => [...h, { question: text, sql: res.sql!, source_id: res.source_id! }].slice(-3));
      const answer = res.answer;
      const tSpeak = performance.now();
      speaker.speak(answer, (firstAudio) => {
        setTimings((prev) => ({
          ...prev,
          "TTS first audio": Math.round(firstAudio - tSpeak),
          ...(speechEndAt ? { "end of question → first audio": Math.round(firstAudio - speechEndAt) } : {}),
        }));
      });
      setTimings({ ...t });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const recorder = useRecorder({
    onRecording: async (rec: Recording) => {
      if (!rec.hadSpeech) {
        recorder.done();
        setError("I didn't hear anything. Try again, a bit louder or closer to the mic.");
        return;
      }
      try {
        const t0 = performance.now();
        const tr = await api.stt(rec.wav);
        const t: Timings = {
          "silence wait (VAD)": Math.round(rec.stoppedAt - rec.speechEndAt),
          "speech-to-text": Math.round(performance.now() - t0),
          "whisper inference": Math.round(tr.ms),
        };
        recorder.done();
        if (!tr.text || tr.no_speech) {
          setError("I couldn't make out any words. Please try again.");
          return;
        }
        setHeard(tr.text);
        setQuestion(tr.text);
        await handle(tr.text, t, rec.speechEndAt);
      } catch (e) {
        recorder.done();
        setError(e instanceof Error ? e.message : String(e));
      }
    },
  });

  async function runSql(sql: string) {
    const sourceId = reply?.source_id ?? retrieval?.source_id;
    if (!sourceId) return;
    setSqlRunning(true);
    try {
      setSqlResult(await api.runSql(sourceId, sql));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSqlRunning(false);
    }
  }

  function micClick() {
    speaker.stop(); // barge-in: talking interrupts the spoken answer
    if (recorder.state === "listening") recorder.stop();
    else {
      setError(null);
      recorder.start();
    }
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-6">
      <div className="flex flex-col items-center gap-3 text-center">
        <MicButton state={recorder.state} level={recorder.level} speaking={recorder.speaking} onClick={micClick} />
        <p className="text-sm text-slate-500">
          {recorder.state === "listening"
            ? recorder.speaking
              ? "Listening…"
              : "Go ahead, I'm listening (stops automatically when you pause)"
            : recorder.state === "processing"
              ? "Transcribing locally…"
              : speaker.speaking
                ? "Speaking… (click the mic to interrupt)"
                : "Click the mic and ask a question about your data"}
        </p>
        {recorder.error && <p className="text-sm text-rose-700">{recorder.error}</p>}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (question.trim()) {
            setHeard(null);
            handle(question.trim(), {}, null);
          }
        }}
        className="mt-5 flex gap-2"
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="…or type a question, e.g. which product categories have the worst reviews?"
          className="min-w-0 flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-sky-500 focus:outline-none"
        />
        <button disabled={busy || !question.trim()} className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40">
          Ask
        </button>
      </form>

      {error && <p className="mt-3 rounded-md bg-rose-50 p-2 text-sm text-rose-700">{error}</p>}

      {(heard || correction) && (
        <div className="mt-4 space-y-1 text-sm">
          {heard && (
            <p>
              <span className="text-slate-500">Heard: </span>
              <span className="font-medium">“{heard}”</span>
            </p>
          )}
          {correction && correction.corrections.length > 0 && (
            <p>
              <span className="text-slate-500">Corrected: </span>
              {correction.corrections.map((c, i) => (
                <span key={i} className="mr-2 inline-flex items-center gap-1" title={c.locations.map((l) => `${l.source} · ${l.table}.${l.column}`).join("\n")}>
                  <span className="text-rose-700 line-through">{c.original}</span>→
                  <span className="rounded bg-emerald-100 px-1 font-medium text-emerald-900">{c.corrected}</span>
                </span>
              ))}
            </p>
          )}
          {correction && correction.entities.length > 0 && (
            <p className="text-xs text-slate-500">
              Recognized:{" "}
              {correction.entities.map((e) => (
                <span key={e.value} className="mr-1.5 rounded bg-slate-100 px-1 py-0.5 text-slate-700" title={e.locations.map((l) => `${l.source} · ${l.table}.${l.column}`).join("\n")}>
                  {e.value}
                </span>
              ))}
            </p>
          )}
        </div>
      )}

      {retrieval && (
        <div className="mt-4 space-y-2 border-t border-slate-100 pt-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-500">Source:</span>
            <span className="font-medium">{retrieval.source_name ?? "none"}</span>
            {retrieval.ambiguous_source && (
              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">ambiguous source</span>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {retrieval.tables.map((t) => (
              <span
                key={t.table}
                title={`score ${t.score}${t.value_hits.length ? " · " + t.value_hits.join(", ") : ""}`}
                className={`rounded px-2 py-0.5 font-mono text-xs ${t.bridge ? "bg-slate-100 text-slate-600" : "bg-sky-100 text-sky-900"}`}
              >
                {t.table}
                {t.value_hits.length > 0 && " ★"}
              </span>
            ))}
          </div>
          <button onClick={() => setShowSchema(!showSchema)} className="text-xs text-sky-700 hover:underline">
            {showSchema ? "Hide" : "Show"} schema sent to the SQL generator
          </button>
          {showSchema && (
            <pre className="max-h-96 overflow-auto rounded-md bg-slate-900 p-3 text-[11px] leading-snug text-slate-100">
              {retrieval.schema_text}
            </pre>
          )}
        </div>
      )}

      {busy && !recorder.error && (
        <p className="mt-4 flex items-center gap-2 text-sm text-slate-500">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-400 border-t-transparent" />
          Working out the answer…
        </p>
      )}

      {reply && !busy && (
        <div className={`mt-4 rounded-lg p-3 text-sm ${reply.status === "answered" ? "bg-sky-50 text-slate-900" : reply.status === "refused" ? "bg-amber-50 text-amber-900" : "bg-slate-100 text-slate-800"}`}>
          <p className="text-base leading-relaxed">{reply.answer}</p>
          {reply.assumption && <p className="mt-1 text-xs text-slate-500">Assumed: {reply.assumption}</p>}
          {reply.attempts.length > 0 && reply.status === "answered" && (
            <p className="mt-1 text-xs text-slate-500">
              The first query failed and was fixed automatically ({reply.attempts.length} {reply.attempts.length === 1 ? "retry" : "retries"}).
            </p>
          )}
        </div>
      )}

      {(reply?.source_id ?? retrieval?.source_id) && (reply?.sql || reply?.result || !reply) && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-xs font-medium text-slate-500">Result · {reply?.source_name ?? retrieval?.source_name}</p>
            {history.length > 0 && (
              <button onClick={() => setHistory([])} className="text-xs text-sky-700 hover:underline"
                title="Follow-up questions use the last few questions as context. Start fresh to forget them.">
                New conversation ({history.length} remembered)
              </button>
            )}
          </div>
          <Suspense fallback={<p className="text-xs text-slate-400">Loading…</p>}>
            <ResultView result={sqlResult} sql={reply?.sql ?? ""} onRun={runSql} running={sqlRunning} />
          </Suspense>
        </div>
      )}

      {Object.keys(timings).length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <p className="mb-1 text-xs font-medium text-slate-500">Latency (ms)</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
            {Object.entries(timings).map(([k, v]) => (
              <span key={k}>
                {k}: <b className="font-mono">{v}</b>
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
