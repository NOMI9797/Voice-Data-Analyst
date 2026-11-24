import { useCallback, useRef, useState } from "react";

/**
 * Plays text through the local Piper TTS endpoint, sentence by sentence:
 * sentence 1 is synthesized and starts playing while sentence 2+ are fetched,
 * so the first sound comes out after ~one sentence of synthesis (~100-200 ms).
 */

function splitSentences(text: string): string[] {
  return text
    .trim()
    .split(/(?<=[.!?])\s+(?=[A-Z0-9])/)
    .map((s) => s.trim())
    .filter(Boolean);
}

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

export function useSpeaker() {
  const [speaking, setSpeaking] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    audioRef.current?.pause();
    audioRef.current = null;
    setSpeaking(false);
  }, []);

  /** Speak text. Resolves true when it played to the end, false if it was stopped
   *  (barge-in, new question) or failed. onFirstAudio fires when sound starts. */
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
            audio.play().catch(reject);
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

  return { speak, stop, speaking };
}
