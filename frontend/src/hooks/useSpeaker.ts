import { useCallback, useRef, useState } from "react";

/**
 * Plays text through the local Piper TTS endpoint, sentence by sentence:
 * sentence 1 is synthesized and starts playing while sentence 2+ are fetched,
 * so the first sound comes out after ~one sentence of synthesis (~100-200 ms).
 */

// Splits the answer into sentences so the first can play sooner.
function splitSentences(text: string): string[] {
  return text
    .trim()
    .split(/(?<=[.!?])\s+(?=[A-Z0-9])/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// Asks the backend (Piper) to speak one sentence; returns the audio.
async function fetchSpeech(text: string, signal: AbortSignal): Promise<string> {
  const res = await fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
    signal,
  });
  if (!res.ok) throw new Error(`TTS failed: HTTP ${res.status}`);
  return URL.createObjectURL(await res.blob());
}

// 0.1 s of silence. Played inside a click so that browsers with strict autoplay rules
// (Safari, some Chrome settings) allow the answer's audio that arrives seconds later.
const SILENCE =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAIlYAAESsAAACABAAZGF0YQAAAAA=";

// MAIN: plays the spoken answer sentence by sentence.
export function useSpeaker() {
  const [speaking, setSpeaking] = useState(false);
  const [blocked, setBlocked] = useState(false); // the browser refused to play sound
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Stops speaking (when the user clicks the mic to interrupt).
  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    audioRef.current?.pause();
    audioRef.current = null;
    setSpeaking(false);
  }, []);

  /** Call from a click handler (mic, Ask, a chip): unlocks audio for later playback. */
  // Plays a tiny silence on a click so the browser allows sound later.
  const unlock = useCallback(() => {
    const a = new Audio(SILENCE);
    a.play().then(() => setBlocked(false)).catch(() => {});
  }, []);

  /** Speak text. Resolves true when it played to the end, false if it was stopped
   *  (barge-in, new question) or failed. onFirstAudio fires when sound starts. */
  // Speaks a text: first sentence plays while the rest are being made.
  const speak = useCallback(
    async (text: string, onFirstAudio?: (t: number) => void): Promise<boolean> => {
      stop();
      const sentences = splitSentences(text);
      if (!sentences.length) return false;
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setSpeaking(true);
      // start all syntheses now; they resolve in order of need
      const urls = sentences.map((s) => fetchSpeech(s, ctrl.signal));
      try {
        for (let i = 0; i < urls.length; i++) {
          const url = await urls[i];
          if (ctrl.signal.aborted) return false;
          await new Promise<void>((resolve, reject) => {
            const audio = new Audio(url);
            audioRef.current = audio;
            audio.onended = () => {
              URL.revokeObjectURL(url);
              resolve();
            };
            audio.onerror = () => reject(new Error("audio playback failed"));
            audio.onplaying = () => {
              if (i === 0) onFirstAudio?.(performance.now());
            };
            ctrl.signal.addEventListener("abort", () => resolve(), { once: true });
            audio.play().catch((e) => {
              if (e instanceof DOMException && e.name === "NotAllowedError") setBlocked(true);
              reject(e);
            });
          });
        }
        return !ctrl.signal.aborted;
      } catch (e) {
        if (!ctrl.signal.aborted) console.error(e);
        return false;
      } finally {
        if (abortRef.current === ctrl) {
          abortRef.current = null;
          setSpeaking(false);
        }
      }
    },
    [stop],
  );

  return { speak, stop, unlock, speaking, blocked };
}
