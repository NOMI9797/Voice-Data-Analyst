"""Voice evaluation: domain-word accuracy and end-of-question -> first-audio latency.

1. Domain words (eval/datasets/domain_words.jsonl)
   * round trip: each sentence is SPOKEN by the local Piper voice and TRANSCRIBED by
     the local Whisper model, so the mistakes are real speech-recognition mistakes on
     real database names (no microphone needed, reproducible)
   * hand-written mishearings ("cartiba", "led zepelin")
   * controls without names: the corrector must leave them alone
   Accuracy = share of expected names present in the text (case, accents and
   punctuation ignored), before and after correction.

2. Latency: for each question in --latency-questions, its spoken audio goes
   through every stage the app runs between the user going quiet and the first
   sound of the answer:
     silence wait (browser VAD, fixed setting) -> upload + speech-to-text ->
     word correction -> ask (screen, retrieval, SQL generation, guard, query,
     answer writing) -> text-to-speech of the first sentence
   reported per stage (median / p90).

Usage:  python eval/voice_eval.py [--skip-latency] [--skip-words] [--delay 4]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import unicodedata
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
SILENCE_WAIT_MS = 1100  # frontend/src/hooks/useRecorder.ts: silenceMs
LATENCY_QUESTIONS = [
    "How many orders were placed each month?",
    "Which music genre has the most tracks?",
    "What is the average review score?",
    "Which shipping company handled the most orders?",
    "How many customers live in Curitiba?",
    "What are the total sales per billing country?",
    "Which five product categories have the most items sold?",
    "How many products are discontinued?",
    "How many albums does Led Zeppelin have?",
    "What is the average freight cost of an order?",
]


# ------------------------------------------------------------------ http

def post_json(api: str, path: str, body: dict, timeout: int = 180):
    req = urllib.request.Request(f"{api}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), dict(r.headers)


def tts(api: str, text: str) -> tuple[bytes, float]:
    t = time.perf_counter()
    wav, _ = post_json(api, "/api/tts", {"text": text})
    return wav, (time.perf_counter() - t) * 1000


HINT = ""  # set by --hint: database names passed to Whisper as its prompt


def stt(api: str, wav: bytes) -> tuple[dict, float]:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"q.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode() + wav + \
           f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"hint\"\r\n\r\n".encode() + \
           HINT.encode() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{api}/api/stt", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.load(r)
    return out, (time.perf_counter() - t) * 1000


def correct(api: str, text: str) -> tuple[dict, float]:
    t = time.perf_counter()
    raw, _ = post_json(api, "/api/correct", {"text": text})
    return json.loads(raw), (time.perf_counter() - t) * 1000


# ------------------------------------------------------------------ matching

def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower().replace("_", " "))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"['’]", "", s)
    return " ".join(re.findall(r"[a-z0-9]+", s))


def present(entity: str, text: str) -> bool:
    return f" {normalize(entity)} " in f" {normalize(text)} "


# ------------------------------------------------------------------ 1. domain words

def eval_words(api: str) -> tuple[list[str], dict]:
    items = [json.loads(line) for line in (HERE / "datasets/domain_words.jsonl").read_text().splitlines() if line.strip()]
    rows = []
    for it in items:
        if "heard" in it:
            heard, kind = it["heard"], "hand-written"
        else:
            wav, _ = tts(api, it["text"])
            tr, _ = stt(api, wav)
            heard, kind = tr["text"], ("control" if it.get("control") else "spoken round trip")
        fix, _ = correct(api, heard)
        after = fix["corrected"]
        rows.append({**it, "kind": kind, "heard": heard, "after": after,
                     "before_ok": [present(e, heard) for e in it["entities"]],
                     "after_ok": [present(e, after) for e in it["entities"]],
                     "changed": normalize(after) != normalize(heard),
                     "corrections": [(c["original"], c["corrected"]) for c in fix["corrections"]]})
        mark = "".join("✓" if a else "·" for a in rows[-1]["after_ok"]) or ("changed!" if rows[-1]["changed"] else "-")
        print(f"  {it['id']:6} {kind:17} {mark:4}  heard: {heard[:55]!r} -> {after[:55]!r}")

    def acc(kind, key):
        vals = [v for r in rows if r["kind"] == kind for v in r[key]]
        return sum(vals), len(vals)

    lines = ["## Domain words: accuracy before and after correction", "",
             "| Test set | Names correct before correction | After correction |", "|---|---|---|"]
    summary = {}
    for kind in ("spoken round trip", "hand-written"):
        (b, n), (a, _) = acc(kind, "before_ok"), acc(kind, "after_ok")
        summary[kind] = {"before": b / n if n else 0, "after": a / n if n else 0, "n": n}
        lines.append(f"| {kind} ({n} names) | {b}/{n} ({b / n:.0%}) | **{a}/{n} ({a / n:.0%})** |")
    controls = [r for r in rows if r["kind"] == "control"]
    changed = [r for r in controls if r["changed"]]
    harmful = [r for r in rows if any(b and not a for b, a in zip(r["before_ok"], r["after_ok"]))]
    lines += ["", f"Control questions without names changed by the corrector: {len(changed)}/{len(controls)}  ",
              f"Names that were right before and wrong after correction: {len(harmful)}", "",
              "Spoken round trip: the local Piper voice speaks the sentence and the local Whisper model "
              "transcribes it, so the errors are real speech-recognition errors.", "",
              "| Id | Heard (speech recognition) | After correction | Names |", "|---|---|---|---|"]
    for r in rows:
        if r["kind"] == "control" and not r["changed"]:
            continue
        names = " ".join(f"{e}: {'✓' if a else '✗'}" for e, a in zip(r["entities"], r["after_ok"])) or \
            ("changed" if r["changed"] else "unchanged")
        lines.append(f"| {r['id']} | {r['heard']} | {r['after']} | {names} |")
    summary["controls_changed"] = len(changed)
    summary["harmful"] = len(harmful)
    return lines, summary


# ------------------------------------------------------------------ 2. latency

def pctl(vals: list[float], p: float) -> float:
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p * (len(vals) - 1))))]


def eval_latency(api: str, questions: list[str], delay: float) -> tuple[list[str], dict]:
    stages: dict[str, list[float]] = {}  # answered questions only
    clarify_ms: list[float] = []         # questions answered with a question ("which database?")
    statuses = []
    for n, q in enumerate(questions, 1):
        wav, _ = tts(api, q)  # the "user" speaking
        tr, stt_ms = stt(api, wav)
        fix, corr_ms = correct(api, tr["text"])
        t = time.perf_counter()
        raw, _ = post_json(api, "/api/ask", {"question": fix["corrected"], "speak": True})
        ask_ms = (time.perf_counter() - t) * 1000
        res = json.loads(raw)
        first = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", res["answer"].strip())[0]
        _, tts_ms = tts(api, first)
        timings = res.get("timings", {})
        row = {
            "silence wait (browser, fixed)": SILENCE_WAIT_MS,
            "upload + speech-to-text": stt_ms,
            "  of which Whisper inference": tr.get("ms", 0),
            "word correction": corr_ms,
            "  retrieval (schema + routing)": timings.get("retrieval", 0),
            "  SQL generation (LLM)": timings.get("sql generation", 0),
            "  SQL repair (LLM)": timings.get("sql repair", 0),
            "  safety guard": timings.get("safety guard", 0),
            "  query": timings.get("query", 0),
            "  spoken answer writing (LLM)": timings.get("answer", 0),
            "ask (server total + network)": ask_ms,
            "text-to-speech, first sentence": tts_ms,
        }
        row["END OF QUESTION -> FIRST AUDIO"] = (SILENCE_WAIT_MS + stt_ms + corr_ms + ask_ms + tts_ms)
        row["same, without the silence wait"] = row["END OF QUESTION -> FIRST AUDIO"] - SILENCE_WAIT_MS
        if res["status"] == "answered":
            for k, v in row.items():
                stages.setdefault(k, []).append(v)
        else:
            clarify_ms.append(row["END OF QUESTION -> FIRST AUDIO"])
        statuses.append(res["status"])
        print(f"  [{n}/{len(questions)}] {res['status']:9} {row['END OF QUESTION -> FIRST AUDIO']:7.0f} ms  "
              f"heard {tr['text']!r}")
        if n < len(questions):
            time.sleep(delay)
    lines = ["## Latency: end of question to start of spoken answer", "",
             f"{len(questions)} spoken questions (Piper voice → full pipeline, all sources active, spoken answer on). "
             f"Statuses: " + ", ".join(f"{s} {statuses.count(s)}" for s in sorted(set(statuses))) + ".", "",
             f"Per-stage numbers are for the {len(stages.get('word correction', []))} answered questions.", "",
             "| Stage | Median ms | p90 ms |", "|---|---|---|"]
    for k, vals in stages.items():
        bold = "**" if k.isupper() else ""
        lines.append(f"| {bold}{k.strip() if not k.startswith('  ') else '↳ ' + k.strip()}{bold} | "
                     f"{bold}{statistics.median(vals):,.0f}{bold} | {bold}{pctl(vals, 0.9):,.0f}{bold} |")
    if clarify_ms:
        lines += ["", f"Questions answered with a clarifying question ({len(clarify_ms)}): first audio after a median "
                      f"of {statistics.median(clarify_ms):,.0f} ms (no SQL is written or run)."]
    lines += ["", "Stages marked ↳ run inside \"ask\". The silence wait is the browser's end-of-speech "
              f"detection ({SILENCE_WAIT_MS} ms of quiet). Browser audio playback start (a few ms) is not included."]
    return lines, {k: statistics.median(v) for k, v in stages.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--skip-words", action="store_true")
    ap.add_argument("--skip-latency", action="store_true")
    ap.add_argument("--delay", type=float, default=4.0, help="seconds between latency questions (LLM rate limit)")
    ap.add_argument("--hint", action="store_true", help="pass database names to Whisper as its prompt")
    args = ap.parse_args()
    global HINT
    if args.hint:
        with urllib.request.urlopen(f"{args.api}/api/correct/hint", timeout=30) as r:
            HINT = json.load(r)["hint"]
        print(f"Whisper hint ({len(HINT)} chars): {HINT[:120]}...")
    lines = ["# Voice evaluation report", "", f"Whisper prompt with database names: {'on' if HINT else 'off'}", ""]
    summary = {}
    if not args.skip_words:
        print("domain words:")
        part, summary["words"] = eval_words(args.api)
        lines += part + [""]
    if not args.skip_latency:
        print("latency:")
        part, summary["latency_median_ms"] = eval_latency(args.api, LATENCY_QUESTIONS, args.delay)
        lines += part + [""]
    out = HERE / f"results/voice-{datetime.now():%Y%m%d-%H%M}.md"
    out.write_text("\n".join(lines) + "\n")
    out.with_suffix(".json").write_text(json.dumps(summary, indent=1))
    print("\n" + "\n".join(l for l in lines if l.startswith("|") and ("Test set" in l or "names)" in l
                                                                        or "Stage" in l or "**" in l)))
    print(f"\nfull report: {out}")


if __name__ == "__main__":
    main()
