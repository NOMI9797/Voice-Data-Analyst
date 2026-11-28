import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Microphone recorder with client-side voice activity detection (VAD).
 *
 * - Captures raw PCM with an AudioWorklet (no codec, no server-side decoding).
 * - Calibrates the room noise level for the first ~300 ms, then detects speech
 *   by energy (RMS) above an adaptive threshold.
 * - Stops automatically after `silenceMs` of silence following speech
 *   (or when stop() is called = push-to-talk).
 * - Trims leading/trailing silence, resamples to 16 kHz mono, encodes 16-bit WAV.
 */

export type RecorderState = "idle" | "listening" | "processing";

export type Recording = {
  wav: Blob;
  durationMs: number;
  speechEndAt: number; // performance.now() of the last voiced audio = "end of question"
  stoppedAt: number; // performance.now() when recording stopped
  hadSpeech: boolean;
};

type Options = {
  silenceMs?: number;
  maxMs?: number;
  noSpeechTimeoutMs?: number;
  onRecording: (rec: Recording) => void;
};

const WORKLET = `
class Capture extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      for (let i = 0; i < ch.length; i++) {
        this.buf[this.n++] = ch[i];
        if (this.n === this.buf.length) { this.port.postMessage(this.buf.slice(0)); this.n = 0; }
      }
    }
    return true;
  }
}
registerProcessor("capture", Capture);
`;

// Loudness of a chunk of audio (used to tell speech from silence).
function rms(x: Float32Array): number {
  let s = 0;
  for (let i = 0; i < x.length; i++) s += x[i] * x[i];
  return Math.sqrt(s / x.length);
}

// Converts the microphone's audio to 16 kHz for Whisper.
function resample(input: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return input;
  const ratio = from / to;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    // average the source samples covered by this output sample (cheap low-pass)
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let s = 0;
    for (let j = start; j < end; j++) s += input[j];
    out[i] = s / Math.max(1, end - start);
  }
  return out;
}

// Packs the audio into a WAV file to send to the backend.
function encodeWav(samples: Float32Array, rate: number): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const w = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  w(8, "WAVE");
  w(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  w(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

// MAIN: records the microphone and stops by itself after 1.1 s of silence.
export function useRecorder({ silenceMs = 1100, maxMs = 20000, noSpeechTimeoutMs = 7000, onRecording }: Options) {
  const [state, setStateRaw] = useState<RecorderState>("idle");
  // the ref mirrors the state so start() works from stale closures (e.g. auto-listen after speech)
  const stateRef = useRef<RecorderState>("idle");
  const setState = useCallback((s: RecorderState) => {
    stateRef.current = s;
    setStateRaw(s);
  }, []);
  const [level, setLevel] = useState(0); // 0..1 for the UI meter
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const vadRef = useRef({ floor: 0, calib: [] as number[], speechChunk: -1, lastVoiceChunk: -1, lastVoiceAt: 0, voicedRun: 0, startedAt: 0 });
  const stoppingRef = useRef(false);
  const cbRef = useRef(onRecording);
  cbRef.current = onRecording;

  // Turns the microphone off and frees it.
  const cleanup = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    ctxRef.current?.close().catch(() => {});
    ctxRef.current = null;
    setLevel(0);
    setSpeaking(false);
  }, []);

  // Ends the recording: cuts out the speech, makes the WAV, hands it over.
  const finish = useCallback(() => {
    if (stoppingRef.current) return;
    stoppingRef.current = true;
    const stoppedAt = performance.now();
    const ctx = ctxRef.current;
    const rate = ctx?.sampleRate ?? 48000;
    const chunks = chunksRef.current;
    const vad = vadRef.current;
    cleanup();
    const hadSpeech = vad.speechChunk >= 0;
    if (!hadSpeech) {
      setState("idle");
      cbRef.current({ wav: new Blob(), durationMs: 0, speechEndAt: stoppedAt, stoppedAt, hadSpeech: false });
      return;
    }
    setState("processing");
    const chunkMs = (2048 / rate) * 1000;
    const pad = Math.ceil(300 / chunkMs); // keep 300 ms around the speech
    const from = Math.max(0, vad.speechChunk - pad);
    const to = Math.min(chunks.length, vad.lastVoiceChunk + 1 + pad);
    const total = chunks.slice(from, to).reduce((n, c) => n + c.length, 0);
    const pcm = new Float32Array(total);
    let o = 0;
    for (const c of chunks.slice(from, to)) {
      pcm.set(c, o);
      o += c.length;
    }
    const pcm16k = resample(pcm, rate, 16000);
    cbRef.current({
      wav: encodeWav(pcm16k, 16000),
      durationMs: (pcm16k.length / 16000) * 1000,
      speechEndAt: vad.lastVoiceAt,
      stoppedAt,
      hadSpeech: true,
    });
  }, [cleanup, setState]);

  // Starts listening: measures room noise, then detects speech and silence.
  const start = useCallback(async () => {
    if (stateRef.current !== "idle") return;
    stateRef.current = "listening"; // claim it now: a second start() during getUserMedia is ignored
    setError(null);
    stoppingRef.current = false;
    chunksRef.current = [];
    vadRef.current = { floor: 0, calib: [], speechChunk: -1, lastVoiceChunk: -1, lastVoiceAt: 0, voicedRun: 0, startedAt: performance.now() };
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      streamRef.current = stream;
      const ctx = new AudioContext();
      ctxRef.current = ctx;
      const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
      await ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      const src = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "capture");
      src.connect(node);
      setState("listening");

      node.port.onmessage = (e: MessageEvent<Float32Array>) => {
        if (stoppingRef.current) return;
        const chunk = e.data;
        const idx = chunksRef.current.push(chunk) - 1;
        const vad = vadRef.current;
        const r = rms(chunk);
        const now = performance.now();
        setLevel(Math.min(1, r * 12));

        if (vad.calib.length < 6) {
          vad.calib.push(r); // ~250 ms of room noise
          vad.floor = vad.calib.reduce((a, b) => a + b, 0) / vad.calib.length;
          return;
        }
        const threshold = Math.max(0.012, vad.floor * 3);
        if (r > threshold) {
          vad.voicedRun++;
          if (vad.voicedRun >= 2) {
            if (vad.speechChunk < 0) vad.speechChunk = idx - 1;
            vad.lastVoiceChunk = idx;
            vad.lastVoiceAt = now;
            setSpeaking(true);
          }
        } else {
          vad.voicedRun = 0;
          if (vad.speechChunk < 0) vad.floor = 0.95 * vad.floor + 0.05 * r; // keep adapting before speech
        }

        const elapsed = now - vad.startedAt;
        if (vad.speechChunk >= 0 && now - vad.lastVoiceAt > silenceMs) finish();
        else if (vad.speechChunk < 0 && elapsed > noSpeechTimeoutMs) finish();
        else if (elapsed > maxMs) finish();
        if (vad.speechChunk >= 0 && now - vad.lastVoiceAt > 250) setSpeaking(false);
      };
    } catch (e) {
      cleanup();
      setState("idle");
      setError(
        e instanceof DOMException && e.name === "NotAllowedError"
          ? "Microphone permission denied. Allow it in the browser's address bar."
          : `Microphone error: ${e instanceof Error ? e.message : String(e)}`,
      );
    }
  }, [silenceMs, maxMs, noSpeechTimeoutMs, finish, cleanup, setState]);

  // Stops recording now and sends what was said.
  const stop = useCallback(() => finish(), [finish]);
  // Back to idle after the audio was handled.
  const done = useCallback(() => setState("idle"), [setState]);
  /** Stop listening and throw the recording away (e.g. the user clicked an option instead). */
  // Stops recording and throws the audio away (e.g. a button was clicked instead).
  const cancel = useCallback(() => {
    if (stateRef.current !== "listening") return;
    stoppingRef.current = true;
    cleanup();
    setState("idle");
  }, [cleanup, setState]);

  useEffect(() => cleanup, [cleanup]);

  return { state, level, speaking, error, start, stop, done, cancel };
}
