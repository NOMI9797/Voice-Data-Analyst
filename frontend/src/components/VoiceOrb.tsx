/** The voice button. One look per state, as voice assistants do:
 *  idle (calm), listening (grows with your voice), thinking (spinning ring), speaking (bars).
 *  The state is always written next to it too (see `phaseLabel`), not shown by color alone. */

export type Phase = "idle" | "listening" | "transcribing" | "correcting" | "thinking" | "speaking";

// The text under the mic button ('Listening...', 'Writing and checking the query...').
export function phaseLabel(phase: Phase, userSpeaking: boolean): string {
  switch (phase) {
    case "listening":
      return userSpeaking ? "Listening…" : "Go ahead, I'm listening";
    case "transcribing":
      return "Transcribing on this computer…";
    case "correcting":
      return "Checking names against your data…";
    case "thinking":
      return "Writing and checking the query…";
    case "speaking":
      return "Speaking · tap to interrupt";
    default:
      return "Tap to ask by voice";
  }
}

const MIC = "M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z";

// The animated mic button: idle, listening, thinking, speaking.
export default function VoiceOrb({ phase, level, onClick, size = 56 }: {
  phase: Phase;
  level: number; // 0..1 microphone level while listening
  onClick: () => void;
  size?: number;
}) {
  const busy = phase === "transcribing" || phase === "correcting" || phase === "thinking";
  const listening = phase === "listening";
  const speaking = phase === "speaking";
  const ring = listening ? 4 + level * 26 : 0;

  const bg = listening
    ? "bg-gradient-to-br from-rose-500 to-rose-700"
    : busy
      ? "bg-gradient-to-br from-indigo-500 to-violet-700"
      : speaking
        ? "bg-gradient-to-br from-sky-500 to-teal-600"
        : "bg-gradient-to-br from-indigo-500 via-violet-500 to-fuchsia-500 hover:brightness-110";

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-label={listening ? "Stop listening" : speaking ? "Interrupt and ask again" : "Ask by voice"}
      className={`relative flex shrink-0 items-center justify-center rounded-full text-white shadow-lg transition-[box-shadow,background] duration-150 disabled:cursor-wait ${bg} ${phase === "idle" ? "orb-breathe" : ""}`}
      style={{
        width: size,
        height: size,
        boxShadow: listening ? `0 0 0 ${ring}px rgba(244,63,94,0.18), 0 8px 20px rgba(0,0,0,0.15)` : undefined,
      }}
    >
      {busy && (
        <span className="orb-spin absolute inset-[-4px] rounded-full border-[3px] border-transparent border-t-violet-300 border-r-violet-300/60" />
      )}
      {speaking ? (
        <span className="flex items-center gap-[3px]" style={{ height: size * 0.36 }} aria-hidden>
          {[0, 0.15, 0.3, 0.45].map((d) => (
            <span key={d} className="orb-bar h-full w-[3px] rounded-full bg-white" style={{ animationDelay: `${d}s` }} />
          ))}
        </span>
      ) : listening ? (
        <span className="rounded-[4px] bg-white" style={{ width: size * 0.28, height: size * 0.28 }} aria-hidden />
      ) : (
        <svg viewBox="0 0 24 24" style={{ width: size * 0.42, height: size * 0.42 }} fill="currentColor" aria-hidden>
          <path d={MIC} />
        </svg>
      )}
    </button>
  );
}
