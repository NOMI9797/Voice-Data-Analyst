import { useEffect, useRef, useState } from "react";
import { api, type Pending, type Source, type Suggestion, type Turn } from "../api/client";
import { useRecorder, type Recording } from "../hooks/useRecorder";
import { useSpeaker } from "../hooks/useSpeaker";
import TurnView, { type Timings, type TurnData } from "./TurnView";
import VoiceOrb, { phaseLabel, type Phase } from "./VoiceOrb";

let nextId = 1;

function EmptyState({ suggestions, onAsk }: { suggestions: Suggestion[]; onAsk: (q: string, sourceId: string) => void }) {
  const bySource = suggestions.reduce<Record<string, Suggestion[]>>((acc, s) => {
    (acc[s.source_name] ??= []).push(s);
    return acc;
  }, {});
  return (
    <div className="mx-auto flex max-w-2xl flex-col items-center px-4 py-10 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-sky-500 text-white shadow-lg">
        <svg viewBox="0 0 24 24" className="h-8 w-8" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
          <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Zm7 9a7 7 0 0 1-14 0m7 7v4" strokeLinecap="round" />
        </svg>
      </div>
      <h2 className="mt-4 text-2xl font-semibold text-slate-900">Ask your data a question</h2>
      <p className="mt-2 text-slate-600">
        Speak or type in plain English. You'll get the answer read out loud, a chart and a table, and the SQL that was run.
      </p>
      {Object.keys(bySource).length > 0 && (
        <div className="mt-8 w-full space-y-4 text-left">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Try asking</p>
          {Object.entries(bySource).map(([name, list]) => (
            <div key={name}>
              <p className="mb-1.5 text-xs font-medium text-slate-500">{name}</p>
              <div className="flex flex-wrap gap-2">
                {list.map((s) => (
                  <button key={s.question} onClick={() => onAsk(s.question, s.source_id)}
                    className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-left text-sm text-slate-700 shadow-sm transition hover:border-indigo-300 hover:bg-indigo-50/50">
                    {s.question}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="mt-8 text-xs text-slate-500">
        Tip: after an answer, say <i>“now break it down by month”</i> or <i>“same for last year”</i> to change it.
      </p>
    </div>
  );
}

export default function Conversation({ sourcesVersion }: { sourcesVersion: number }) {
  const [turns, setTurns] = useState<TurnData[]>([]);
  const [text, setText] = useState("");
  const [work, setWork] = useState<Phase>("idle"); // transcribing / correcting / thinking while requests run
  const [notice, setNotice] = useState<string | null>(null);
  const [history, setHistory] = useState<Turn[]>([]); // last answered turns, sent for follow-ups
  const [pending, setPending] = useState<Pending | null>(null); // clarification awaiting a reply
  const [sources, setSources] = useState<Source[]>([]);
  const [scope, setScope] = useState("auto");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [sqlRunning, setSqlRunning] = useState(false);
  const speaker = useSpeaker();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.listSources().then(setSources).catch(() => {});
    api.suggestions().then(setSuggestions).catch(() => {});
  }, [sourcesVersion]);
  const active = sources.filter((s) => s.active);
  useEffect(() => {
    if (scope !== "auto" && !active.some((s) => s.id === scope)) setScope("auto");
  }, [sources]); // eslint-disable-line react-hooks/exhaustive-deps

  const lastTurn = turns[turns.length - 1];
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length, lastTurn?.loading]);

  const update = (id: number, patch: Partial<TurnData> | ((t: TurnData) => Partial<TurnData>)) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)));

  async function ask(question: string, opts: { viaVoice?: boolean; speechEndAt?: number | null; timings?: Timings; pickSource?: string } = {}) {
    const id = nextId++;
    const t: Timings = { ...(opts.timings ?? {}) };
    setNotice(null);
    setTurns((all) => [...all, { id, asked: question, viaVoice: !!opts.viaVoice, timings: t, loading: true }]);
    try {
      let q = question;
      if (!opts.pickSource) {
        setWork("correcting");
        const t0 = performance.now();
        const fix = await api.correct(q);
        t["word correction"] = Math.round(performance.now() - t0);
        update(id, { correction: fix });
        q = fix.corrected;
      }
      setWork("thinking");
      const t1 = performance.now();
      const res = await api.ask(q, history, {
        source_ids: opts.pickSource ? [opts.pickSource] : scope !== "auto" ? [scope] : undefined,
        pending: opts.pickSource ? null : pending,
      });
      t["ask (server total)"] = Math.round(performance.now() - t1);
      for (const [k, v] of Object.entries(res.timings)) if (k !== "total") t[`· ${k}`] = Math.round(v);
      setPending(res.status === "clarify" && res.clarify ? { ...res.clarify, question: res.question } : null);
      if (res.status === "answered" && res.sql && res.source_id)
        setHistory((h) => [...h, { question: res.question, sql: res.sql!, source_id: res.source_id! }].slice(-3));
      // a failed query still shows what was tried, with its last error, in the SQL tab
      const last = res.attempts[res.attempts.length - 1];
      const sqlResult = res.result ??
        (res.sql && last
          ? last.stage === "guard"
            ? { ok: false as const, blocked: true as const, code: last.code ?? "", reason: last.error }
            : { ok: false as const, blocked: false as const, sql: res.sql, error: last.error }
          : null);
      update(id, { reply: res, sqlResult, timings: { ...t }, loading: false });
      setWork("idle");
      const tSpeak = performance.now();
      const finished = await speaker.speak(res.answer, (firstAudio) =>
        update(id, (cur) => ({
          timings: {
            ...cur.timings,
            "TTS first audio": Math.round(firstAudio - tSpeak),
            ...(opts.speechEndAt ? { "end of question → first audio": Math.round(firstAudio - opts.speechEndAt) } : {}),
          },
        })),
      );
      // a spoken clarifying question: listen for the answer straight away (voice conversations only)
      if (finished && res.status === "clarify" && opts.viaVoice) recorder.start();
    } catch (e) {
      update(id, { loading: false, error: e instanceof Error ? e.message : String(e) });
      setWork("idle");
    }
  }

  const recorder = useRecorder({
    onRecording: async (rec: Recording) => {
      if (!rec.hadSpeech) {
        recorder.done();
        setNotice("I didn't hear anything. Try again, a little louder or closer to the mic.");
        return;
      }
      setWork("transcribing");
      try {
        const t0 = performance.now();
        // No name list as a prompt: on real microphones it made Whisper loop on those names.
        const tr = await api.stt(rec.wav);
        recorder.done();
        if (!tr.text || tr.no_speech || tr.looped) {
          setWork("idle");
          setNotice("I couldn't make out any words. Please try again.");
          return;
        }
        await ask(tr.text, {
          viaVoice: true,
          speechEndAt: rec.speechEndAt,
          timings: {
            "silence wait (end of speech)": Math.round(rec.stoppedAt - rec.speechEndAt),
            "speech-to-text": Math.round(performance.now() - t0),
            "whisper inference": Math.round(tr.ms),
          },
        });
      } catch (e) {
        recorder.done();
        setWork("idle");
        setNotice(e instanceof Error ? e.message : String(e));
      }
    },
  });

  const phase: Phase =
    recorder.state === "listening" ? "listening"
      : work !== "idle" ? work
        : recorder.state === "processing" ? "transcribing"
          : speaker.speaking ? "speaking" : "idle";
  const busy = work !== "idle" || recorder.state === "processing";

  function orbClick() {
    speaker.unlock(); // a click is when browsers allow sound to be enabled
    speaker.stop(); // interrupt the spoken answer
    if (recorder.state === "listening") recorder.stop();
    else {
      setNotice(null);
      recorder.start();
    }
  }

  function sendTyped(q: string) {
    if (!q.trim() || busy) return;
    speaker.unlock();
    speaker.stop();
    recorder.cancel();
    setText("");
    ask(q.trim());
  }

  async function runSql(turnId: number, sql: string) {
    const turn = turns.find((t) => t.id === turnId);
    const sourceId = turn?.reply?.source_id ?? turn?.reply?.retrieval?.source_id;
    if (!sourceId) return;
    setSqlRunning(true);
    try {
      const res = await api.runSql(sourceId, sql);
      update(turnId, { sqlResult: res });
    } finally {
      setSqlRunning(false);
    }
  }

  function newConversation() {
    speaker.stop();
    recorder.cancel();
    setTurns([]);
    setHistory([]);
    setPending(null);
    setNotice(null);
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {turns.length === 0 ? (
          <EmptyState suggestions={suggestions}
            onAsk={(q, sourceId) => { speaker.unlock(); speaker.stop(); recorder.cancel(); ask(q, { pickSource: sourceId }); }} />
        ) : (
          <div className="mx-auto max-w-4xl space-y-6 px-4 py-6">
            {turns.map((t, i) => (
              <TurnView key={t.id} turn={t} isLatest={i === turns.length - 1} phase={phase} userSpeaking={recorder.speaking}
                pending={pending}
                onPickSource={(q, sid) => { speaker.unlock(); speaker.stop(); recorder.cancel(); ask(q, { pickSource: sid }); }}
                onReply={(reply) => sendTyped(reply)}
                onReplay={(answer) => { recorder.cancel(); speaker.speak(answer); }}
                onRunSql={runSql} sqlRunning={sqlRunning} />
            ))}
            <div ref={endRef} />
          </div>
        )}
      </div>

      <div className="border-t border-slate-200 bg-white/90 backdrop-blur">
        <div className="mx-auto max-w-4xl px-4 py-3">
          {(notice || recorder.error) && (
            <p className="mb-2 rounded-lg bg-amber-50 px-3 py-1.5 text-sm text-amber-900" role="status">{recorder.error ?? notice}</p>
          )}
          {speaker.blocked && (
            <p className="mb-2 rounded-lg bg-amber-50 px-3 py-1.5 text-sm text-amber-900" role="status">
              The browser blocked sound for this page. Click <b>Play answer</b> on the answer, or allow sound for
              localhost in the browser's site settings.
            </p>
          )}
          <div className="flex items-center gap-3">
            <VoiceOrb phase={phase} level={recorder.level} onClick={orbClick} />
            <form className="flex min-w-0 flex-1 items-center gap-2 rounded-2xl border border-slate-300 bg-white py-1 pl-4 pr-1 shadow-sm focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100"
              onSubmit={(e) => { e.preventDefault(); sendTyped(text); }}>
              <input value={text} onChange={(e) => setText(e.target.value)} disabled={phase === "listening"}
                placeholder={pending ? "Type your answer, or just say it…" : "Type a question, e.g. which product categories have the worst reviews?"}
                aria-label="Question"
                className="min-w-0 flex-1 bg-transparent py-1.5 text-[15px] text-slate-900 placeholder:text-slate-400 focus:outline-none" />
              {active.length > 1 && (
                <select value={scope} onChange={(e) => { setScope(e.target.value); setHistory([]); setPending(null); }}
                  aria-label="Which data to ask"
                  title="All active: the app picks the database for each question, and asks you when it's unclear"
                  className="hidden max-w-40 rounded-lg border-0 bg-slate-100 px-2 py-1.5 text-xs text-slate-700 focus:outline-none sm:block">
                  <option value="auto">All data ({active.length})</option>
                  {active.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              )}
              <button disabled={busy || !text.trim()} aria-label="Ask"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-indigo-600 text-white transition hover:bg-indigo-700 disabled:bg-slate-200 disabled:text-slate-400">
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M5 12h14M13 6l6 6-6 6" />
                </svg>
              </button>
            </form>
          </div>
          <div className="mt-1.5 flex items-center justify-between pl-[68px] text-xs text-slate-500">
            <span aria-live="polite">{phaseLabel(phase, recorder.speaking)}</span>
            {turns.length > 0 && (
              <button onClick={newConversation} className="hover:text-slate-800"
                title={history.length ? `Follow-ups currently use the last ${history.length} question(s) as context` : undefined}>
                New conversation
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
