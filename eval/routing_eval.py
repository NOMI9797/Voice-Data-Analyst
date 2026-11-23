"""Evaluates database routing and picks the "too close to call" threshold.

Runs every question in eval/datasets/routing.jsonl through /api/schema/retrieve
(no LLM, no quota) with all sources active, then sweeps the clarification
margin: when the best source beats the runner-up by less than the margin, the
app asks the user instead of guessing.

Cost model for choosing the margin (lower is better):
  wrong database picked silently ............ 3   (a confident wrong answer)
  clear question, asked anyway .............. 1   (one extra click / word)
  ambiguous question, not asked ............. 1   (a guess the user didn't choose)

Usage:  python eval/routing_eval.py [--api http://localhost:8000] [--save]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "backend"))
from app.profiler.routing import COVERAGE_DELTA, SCORE_MARGIN, decide  # noqa: E402  (pure python)
COST_WRONG, COST_ASKED, COST_GUESSED = 3, 1, 1


def key(name: str) -> str:
    return name.split()[0].lower()  # "Olist e-commerce" -> "olist"


def route(api: str, q: str) -> list[dict]:
    req = urllib.request.Request(f"{api}/api/schema/retrieve", json.dumps({"question": q}).encode(),
                                 {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))["source_scores"]


def evaluate(rows: list[dict], delta: float, margin: float) -> dict:
    s = {"wrong": 0, "right": 0, "asked_clear": 0, "asked_ambiguous": 0, "guessed_ambiguous": 0,
         "ask_missing_answer": 0}
    for r in rows:
        d = decide(r["scores"], delta, margin)
        ask = d.source_id is None
        if ask and not r.get("ambiguous") and not any(key(c["name"]) in r["expect"] for c in d.candidates):
            s["ask_missing_answer"] += 1  # we asked, but the right source wasn't among the choices
        if r.get("ambiguous"):
            s["asked_ambiguous" if ask else "guessed_ambiguous"] += 1
        elif ask:
            s["asked_clear"] += 1
        elif key(next(x["name"] for x in r["scores"] if x["source_id"] == d.source_id)) in r["expect"]:
            s["right"] += 1
        else:
            s["wrong"] += 1
    s["cost"] = (COST_WRONG * (s["wrong"] + s["ask_missing_answer"]) + COST_ASKED * s["asked_clear"]
                 + COST_GUESSED * s["guessed_ambiguous"])
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--save", action="store_true", help="write eval/results/routing.json")
    args = ap.parse_args()

    rows = [json.loads(line) for line in (HERE / "datasets/routing.jsonl").read_text().splitlines() if line.strip()]
    for r in rows:
        r["scores"] = route(args.api, r["q"])

    clear = [r for r in rows if not r.get("ambiguous")]
    top1 = sum(key(r["scores"][0]["name"]) in r["expect"] for r in clear)
    print(f"{len(rows)} questions ({len(clear)} clear, {len(rows) - len(clear)} ambiguous)")
    print(f"top-1 routing accuracy on clear questions (never asking): {top1}/{len(clear)} = {top1 / len(clear):.0%}\n")

    cur = evaluate(rows, COVERAGE_DELTA, SCORE_MARGIN)
    print("question".ljust(52), "expected".ljust(22), "decision (current thresholds)")
    for r in rows:
        d = decide(r["scores"])
        if d.source_id is None:
            got = "ASK " + "/".join(key(c["name"]) for c in d.candidates)
            mark = "~" if r.get("ambiguous") else "?"
        else:
            got = key(next(x["name"] for x in r["scores"] if x["source_id"] == d.source_id))
            mark = ("g" if r.get("ambiguous") else "ok") if got in r["expect"] or r.get("ambiguous") else "XX"
        cov = " ".join(f"{key(x['name'])[:2]}:{x['coverage']:.2f}" for x in r["scores"])
        print(f"{mark:2} {r['q'][:49].ljust(49)} {'/'.join(r['expect'])[:21].ljust(22)} {got.ljust(26)} {cov}")
    print("\nlegend: ok right  XX wrong  ? asked on a clear question  ~ asked on an ambiguous one  g guessed an ambiguous one")

    print("\ndelta  margin  right wrong asked(clear) asked(ambig) guessed(ambig) missing cost")
    sweep = []
    for delta in [i / 100 for i in range(0, 16)]:
        for m in [i / 100 for i in range(0, 26)]:
            s = evaluate(rows, delta, m)
            sweep.append({"delta": delta, "margin": m, **s})
    sweep.sort(key=lambda s: (s["cost"], s["asked_clear"], -s["delta"]))
    for s in sweep[:12]:
        print(f"{s['delta']:.2f}   {s['margin']:.2f}   {s['right']:5} {s['wrong']:5} {s['asked_clear']:12} "
              f"{s['asked_ambiguous']:12} {s['guessed_ambiguous']:14} {s['ask_missing_answer']:7} {s['cost']:4}")
    best = sweep[0]
    print(f"\ncurrent thresholds delta={COVERAGE_DELTA} margin={SCORE_MARGIN}: cost {cur['cost']} "
          f"(right {cur['right']}, wrong {cur['wrong']}, asked clear {cur['asked_clear']}, "
          f"asked ambiguous {cur['asked_ambiguous']}, guessed ambiguous {cur['guessed_ambiguous']})")

    if args.save:
        out = HERE / "results/routing.json"
        out.write_text(json.dumps({"top1_clear": top1 / len(clear), "best": best, "sweep": sweep,
                                   "questions": [{k: r[k] for k in ("q", "expect", "scores")} for r in rows]},
                                  indent=1))
        print(f"saved {out}")


if __name__ == "__main__":
    main()
