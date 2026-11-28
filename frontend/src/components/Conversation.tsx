import { useEffect, useRef, useState } from "react";
import { api, type Pending, type Source, type Suggestion, type Turn } from "../api/client";
import { useRecorder, type Recording } from "../hooks/useRecorder";
import { useSpeaker } from "../hooks/useSpeaker";
import TurnView, { type Timings, type TurnData } from "./TurnView";
import VoiceOrb, { phaseLabel, type Phase } from "./VoiceOrb";

let nextId = 1;

// The welcome screen: a big mic, and starter questions per database.
function EmptyState({ suggestions, onAsk, orb, hint }: {
  suggestions: Suggestion[];
  onAsk: (q: string, sourceId: string) => void;
  orb: React.ReactNode;
  hint: string;
}) {
  // two per database, so every source is represented
  const picked = Object.values(suggestions.reduce<Record<string, Suggestion[]>>((acc, s) => {
    (acc[s.source_name] ??= []).push(s);
    return acc;
  }, {})).flatMap((list) => list.slice(0, 2));
  return (
    <div className="mx-auto flex max-w-3xl flex-col items-center px-6 pb-10 pt-[8vh] text-center">
      <div className="relative">
        <div className="absolute inset-[-28px] rounded-full bg-gradient-to-br from-indigo-400/25 via-violet-400/20 to-fuchsia-400/20 blur-2xl" aria-hidden />
        <div className="relative">{orb}</div>
      </div>
      <p className="mt-4 text-sm font-medium text-indigo-600" aria-live="polite">{hint}</p>
      <h2 className="mt-6 text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl">What would you like to know?</h2>
      <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-slate-500">
        Ask in plain English, by voice or by typing. You get a spoken answer, a chart and a table,
        and the exact SQL that was run.
      </p>

      {picked.length > 0 && (
        <div className="mt-10 w-full text-left">
          <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-400">Try one of these</p>
          <div className="grid gap-3 sm:grid-cols-2">
            {picked.map((s) => (
              <button key={s.source_id + s.question} onClick={() => onAsk(s.question, s.source_id)}
                className="group flex flex-col gap-2 rounded-2xl border border-slate-200/80 bg-white p-4 text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-[0_8px_24px_rgba(79,70,229,0.08)]">
                <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500">
                  <span className="h-1.5 w-1.5 rounded-full bg-indigo-400" /> {s.source_name}
                </span>
                <span className="flex items-start justify-between gap-3 text-[15px] leading-snug text-slate-800">
                  {s.question}
                  <span className="mt-0.5 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-indigo-500" aria-hidden>→</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mt-10 flex flex-wrap justify-center gap-x-6 gap-y-2 text-xs text-slate-400">
        <span>🔒 Your voice never leaves this computer</span>
        <span>🛡️ Read-only, checked by a safety guard</span>
        <span>↻ Say “now break it down by month” to follow up</span>
      </div>
    </div>
  );
}

// MAIN: the conversation: questions, answers, voice, memory of the last 3 questions.
export default function Conversation({ sourcesVersion, onOpenData }: { sourcesVersion: number; onOpenData: () => void }) {
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

  // Updates one question card on screen.
  const update = (id: number, patch: Partial<TurnData> | ((t: TurnData) => Partial<TurnData>)) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...(typeof patch === "function" ? patch(t) : patch) } : t)));

  // Runs one question: fix names -> /api/ask -> show answer -> speak it -> listen again if it asked a question.
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
        source_ids: opts.pickSource === "*all*" && pending?.kind === "source"
          ? ["*all*", ...pending.options.map((o) => o.source_id)]
          : opts.pickSource ? [opts.pickSource] : scope !== "auto" ? [scope] : undefined,
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

  // When recording ends: send audio to Whisper, then ask the question.
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

  // The mic button: start listening, stop, or interrupt the spoken answer.
  function orbClick() {
    speaker.unlock(); // a click is when browsers allow sound to be enabled
    speaker.stop(); // interrupt the spoken answer
    if (recorder.state === "listening") recorder.stop();
    else {
      setNotice(null);
      recorder.start();
    }
  }

  // Sends a typed question.
  function sendTyped(q: string) {
    if (!q.trim() || busy) return;
    speaker.unlock();
    speaker.stop();
    recorder.cancel();
    setText("");
    ask(q.trim());
  }

  // Re-runs SQL edited in the SQL tab.
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

  // Clears the conversation and its memory.
  function newConversation() {
    speaker.stop();
    recorder.cancel();
    setTurns([]);
    setHistory([]);
    setPending(null);
    setNotice(null);
  }

  const title = turns.length ? (turns[0].reply?.question ?? turns[0].asked) : "New analysis";

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <header className="flex shrink-0 items-center gap-3 border-b border-slate-200/70 bg-white/70 px-4 py-3 backdrop-blur sm:px-6">
        <button onClick={onOpenData} className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 lg:hidden">
          Data
        </button>
        <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-slate-700" title={title}>{title}</h1>
        {active.length > 1 && (
          <label className="hidden items-center gap-2 text-xs text-slate-500 sm:flex">
            Ask
            <select value={scope} onChange={(e) => { setScope(e.target.value); setHistory([]); setPending(null); }}
              title="All data: the app picks the database for each question, and asks you when it's unclear"
              className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs font-medium text-slate-800 focus:border-indigo-400 focus:outline-none">
              <option value="auto">All data ({active.length})</option>
              {active.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
        )}
        {turns.length > 0 && (
          <button onClick={newConversation}
            title={history.length ? `Follow-ups currently use the last ${history.length} question(s) as context` : undefined}
            className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-slate-700">
            <span aria-hidden>+</span> New conversation
          </button>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {turns.length === 0 ? (
          <EmptyState suggestions={suggestions} hint={phaseLabel(phase, recorder.speaking)}
            orb={<VoiceOrb phase={phase} level={recorder.level} onClick={orbClick} size={96} />}
            onAsk={(q, sourceId) => { speaker.unlock(); speaker.stop(); recorder.cancel(); ask(q, { pickSource: sourceId }); }} />
        ) : (
          <div className="mx-auto max-w-3xl space-y-8 px-4 py-8 sm:px-6">
            {turns.map((t, i) => (
              <TurnView key={t.id} turn={t} isLatest={i === turns.length - 1} phase={phase} userSpeaking={recorder.speaking}
                pending={pending}
                onPickSource={(q, sid) => { speaker.unlock(); speaker.stop(); recorder.cancel(); ask(q, { pickSource: sid }); }}
                onReply={(reply) => sendTyped(reply)}
                onReplay={(answer) => { recorder.cancel(); speaker.speak(answer); }}
                onRunSql={runSql} sqlRunning={sqlRunning}
                onEnableSource={async (sid, q) => {
                  speaker.unlock(); speaker.stop(); recorder.cancel();
                  await api.setActive(sid, true);
                  window.dispatchEvent(new Event("sources-changed")); // the sidebar refreshes its switches
                  ask(q, { pickSource: sid });
                }} />
            ))}
            <div ref={endRef} />
          </div>
        )}
      </div>

      <div className="shrink-0 px-4 pb-4 pt-2 sm:px-6">
        <div className="mx-auto max-w-3xl">
          {(notice || recorder.error) && (
            <p className="mb-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900" role="status">{recorder.error ?? notice}</p>
          )}
          {speaker.blocked && (
            <p className="mb-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900" role="status">
              The browser blocked sound for this page. Click <b>Play answer</b> on the answer, or allow sound for
              localhost in the browser's site settings.
            </p>
          )}
          <div className={`flex items-center gap-2 rounded-[28px] border bg-white p-2 shadow-[0_10px_40px_rgba(15,23,42,0.08)] transition ${
            phase === "listening" ? "border-rose-300 ring-4 ring-rose-100" : "border-slate-200 focus-within:border-indigo-300 focus-within:ring-4 focus-within:ring-indigo-100"}`}>
            {turns.length > 0 && <VoiceOrb phase={phase} level={recorder.level} onClick={orbClick} size={44} />}
            <form className="flex min-w-0 flex-1 items-center gap-2 pl-2" onSubmit={(e) => { e.preventDefault(); sendTyped(text); }}>
              <input value={text} onChange={(e) => setText(e.target.value)} disabled={phase === "listening"}
                placeholder={pending ? "Type your answer, or just say it…" : "Ask about your data…"}
                aria-label="Question"
                className="min-w-0 flex-1 bg-transparent py-2 text-[15px] text-slate-900 placeholder:text-slate-400 focus:outline-none" />
              <button disabled={busy || !text.trim()} aria-label="Ask"
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-white transition hover:bg-indigo-500 disabled:bg-slate-100 disabled:text-slate-300">
                <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                  <path d="M12 19V5M6 11l6-6 6 6" />
                </svg>
              </button>
            </form>
          </div>
          <p className="mt-2 text-center text-xs text-slate-400" aria-live="polite">
            {turns.length > 0 ? phaseLabel(phase, recorder.speaking) : "Speech is recognised and spoken on this computer"}
          </p>
        </div>
      </div>
    </section>
  );
}
