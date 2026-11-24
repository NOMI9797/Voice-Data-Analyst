"""End-to-end evaluation: question -> SQL -> result, scored by EXECUTION ACCURACY.

For every question in eval/datasets/nl2sql.jsonl:
  * ask it through /api/ask, restricted to its source (routing has its own
    eval: eval/routing_eval.py), with speak=false so each question costs one
    LLM call plus any repairs instead of two
  * run the hand-written gold SQL through /api/sql/run
  * compare the two RESULTS (app/evaluation/compare.py): strict, plus a
    lenient score for "which X has the most Y" answered with a ranked list
Safety items instead check the status (refused / cannot_answer).

Results are appended per question to eval/results/run-<time>.jsonl, so a run
stopped by the Gemini quota can be continued with --resume <that file>.
A Markdown report is written next to it.

Usage:
  python eval/run_eval.py --check-gold            # validate the dataset (no LLM)
  python eval/run_eval.py                         # full run
  python eval/run_eval.py --source chinook --limit 5 --delay 5
  python eval/run_eval.py --resume eval/results/run-20261124-2350.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "backend"))
from app.evaluation.compare import compare, norm  # noqa: E402  (pure python)

QUOTA_WORDS = ("couldn't reach the language model", "key is not set")


def post(api: str, path: str, body: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(f"{api}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def source_ids(api: str) -> dict[str, str]:
    with urllib.request.urlopen(f"{api}/api/sources", timeout=30) as r:
        return {s["name"].split()[0].lower(): s["id"] for s in json.load(r)}


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ------------------------------------------------------------------ gold check

def _gold_items(items: list[dict]) -> list[dict]:
    """Single questions plus every turn of a conversation that has a gold query."""
    out = []
    for it in items:
        if "turns" in it:
            out += [{**t, "id": f"{it['id']}.{n}", "source": it["source"]} for n, t in enumerate(it["turns"], 1)]
        else:
            out.append(it)
    return [i for i in out if "gold" in i]


def check_gold(api: str, items: list[dict], ids: dict[str, str]) -> int:
    problems = 0
    for it in _gold_items(items):
        res = post(api, "/api/sql/run", {"source_id": ids[it["source"]], "sql": it["gold"]})
        if not res.get("ok"):
            problems += 1
            print(f"FAIL {it['id']}: {res.get('reason') or res.get('error')}")
            continue
        preview = "; ".join(" | ".join(str(v) for v in r) for r in res["rows"][:3])
        print(f"ok   {it['id']}  {res['row_count']:4} rows  {preview[:90]}")
        m = re.search(r"ORDER BY .+ LIMIT (\d+)\s*$", it["gold"], re.I)
        if m:  # is there a tie at the cut-off? then the expected top-N is arbitrary
            n = int(m.group(1))
            wider = post(api, "/api/sql/run", {"source_id": ids[it["source"]],
                                               "sql": it["gold"][:m.start(1)] + str(n + 1)})
            rows = wider.get("rows") or []
            if len(rows) > n and norm(rows[n - 1][-1]) == norm(rows[n][-1]):
                problems += 1
                print(f"     TIE at position {n}: {rows[n - 1]} vs {rows[n]}")
    print(f"\n{problems} problem(s)")
    return problems


# ------------------------------------------------------------------ run

def run_conversation(api: str, it: dict, ids: dict[str, str], gold_cache: dict, delay: float) -> dict:
    """Asks the turns in order, carrying history (and a pending clarification) like the UI does.
    The conversation is correct only if every turn is."""
    history, pending, turns = [], None, []
    for n, turn in enumerate(it["turns"], 1):
        sub = {**turn, "id": f"{it['id']}.{n}", "source": it["source"], "level": it["level"]}
        r = run_one(api, sub, ids, gold_cache, history=history, pending=pending)
        turns.append(r)
        if "error" in r:
            r.pop("_raw", None)
            return {"id": it["id"], "source": it["source"], "level": it["level"], "q": it["turns"][0]["q"],
                    "turns": turns, "error": r["error"]}
        res = r.pop("_raw")
        pending = ({**res["clarify"], "question": res["question"]} if res["status"] == "clarify" and res.get("clarify")
                   else None)
        if res["status"] == "answered" and res.get("sql") and res.get("source_id"):
            history = (history + [{"question": res["question"], "sql": res["sql"], "source_id": res["source_id"]}])[-3:]
        if n < len(it["turns"]):
            time.sleep(delay)
    ok = all(t["correct"] for t in turns)
    return {"id": it["id"], "source": it["source"], "level": it["level"],
            "q": " → ".join(t["q"] for t in it["turns"]), "status": turns[-1].get("status"),
            "correct": ok, "lenient": all(t["lenient"] for t in turns), "turns": turns,
            "reason": "; ".join(f"turn {i}: {t['reason']}" for i, t in enumerate(turns, 1) if not t["correct"])
                      or "all turns correct",
            "repairs": sum(t.get("repairs", 0) for t in turns), "timings": turns[-1].get("timings", {}),
            "sql": turns[-1].get("sql"), "gold_sql": turns[-1].get("gold_sql")}


def run_one(api: str, it: dict, ids: dict[str, str], gold_cache: dict, history: list | None = None,
            pending: dict | None = None) -> dict:
    t0 = time.perf_counter()
    body = {"question": it["q"], "source_ids": [ids[it["source"]]], "speak": False,
            "history": history or [], "pending": pending}
    try:
        res = post(api, "/api/ask", body)
    except urllib.error.URLError as exc:
        return {"id": it["id"], "error": f"request failed: {exc}"}
    out = {"_raw": res, "id": it["id"], "source": it["source"], "level": it["level"], "q": it["q"], "status": res["status"],
           "answer": res["answer"], "sql": res.get("sql"), "repairs": len(res.get("attempts") or []),
           "timings": res.get("timings", {}), "wall_ms": round((time.perf_counter() - t0) * 1000)}
    if res["status"] == "failed" and any(w in res["answer"] for w in QUOTA_WORDS):
        # an LLM outage or quota stop says nothing about accuracy: re-run it later
        return {**out, "error": "language model unavailable"}
    if "expect_status" in it:
        out["correct"] = out["lenient"] = res["status"] in it["expect_status"]
        out["reason"] = f"status {res['status']}"
        return out
    if res["status"] != "answered" or not res.get("result"):
        out["correct"] = out["lenient"] = False
        out["reason"] = f"status {res['status']}"
        return out
    if it["id"] not in gold_cache:
        gold_cache[it["id"]] = post(api, "/api/sql/run", {"source_id": ids[it["source"]], "sql": it["gold"]})
    gold = gold_cache[it["id"]]
    m = compare(gold["columns"], gold["rows"], res["result"]["columns"], res["result"]["rows"])
    out.update(correct=m.strict, lenient=m.lenient, reason=m.reason, gold_sql=it["gold"])
    return out


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({n / d:.0%})" if d else "-"


def report(rows: list[dict], path: Path) -> str:
    sql_rows = [r for r in rows if r.get("level") not in ("safety", "followup", "clarify") and "correct" in r]
    safety = [r for r in rows if r.get("level") == "safety" and "correct" in r]
    follow = [r for r in rows if r.get("level") == "followup" and "correct" in r]
    clar = [r for r in rows if r.get("level") == "clarify" and "correct" in r]
    asked_clear = [r for r in sql_rows if r.get("status") == "clarify"]
    lines = [f"# Evaluation report", f"", f"Run file: `{path.name}` · {len(rows)} questions", ""]
    lines += ["## Execution accuracy", "",
              "| Group | Strict | Lenient |", "|---|---|---|",
              f"| **All SQL questions** | {pct(sum(r['correct'] for r in sql_rows), len(sql_rows))} "
              f"| {pct(sum(r['lenient'] for r in sql_rows), len(sql_rows))} |"]
    for key in ("source", "level"):
        groups = defaultdict(list)
        for r in sql_rows:
            groups[r[key]].append(r)
        for g in sorted(groups):
            rs = groups[g]
            lines.append(f"| {key}: {g} | {pct(sum(r['correct'] for r in rs), len(rs))} "
                         f"| {pct(sum(r['lenient'] for r in rs), len(rs))} |")
    lines += ["", "Strict: same rows as the gold query. Lenient: also accepts a ranked list whose first row is "
              "the one-row gold answer.", ""]
    if safety:
        lines += ["## Safety", "", f"Correctly refused or declined: {pct(sum(r['correct'] for r in safety), len(safety))}", ""]
    if follow:
        turns = [t for r in follow for t in r["turns"]]
        lines += ["## Voice follow-ups", "",
                  f"Conversations fully correct: {pct(sum(r['correct'] for r in follow), len(follow))}; "
                  f"individual turns correct: {pct(sum(t['correct'] for t in turns), len(turns))}", ""]
        lines += [f"- {r['id']}: {r['q']} → {'ok' if r['correct'] else r['reason']}" for r in follow] + [""]
    if clar or sql_rows:
        lines += ["## Clarification", ""]
        if clar:
            lines.append(f"Vague questions handled as expected (asked, and answered correctly after the reply): "
                         f"{pct(sum(r['correct'] for r in clar), len(clar))}")
        lines.append(f"Clear questions that were asked about unnecessarily: {pct(len(asked_clear), len(sql_rows))}")
        lines.append("")
    answered = [r for r in sql_rows if r.get("status") == "answered" and r.get("timings")]
    if answered:
        def stat(vals):
            vals = sorted(v for v in vals if v is not None)
            if not vals:
                return "-"
            p90 = vals[min(len(vals) - 1, int(round(0.9 * (len(vals) - 1))))]
            return f"{statistics.median(vals):,.0f} / {p90:,.0f}"
        lines += ["## Latency (ms, median / p90, answered questions, spoken answer skipped)", "",
                  "| Stage | ms |", "|---|---|"]
        for stage in ("retrieval", "sql generation", "sql repair", "safety guard", "query", "total"):
            vals = [r["timings"].get(stage) for r in answered if stage in r["timings"]]
            if vals:
                lines.append(f"| {stage} | {stat(vals)} |")
        rep = [r for r in answered if r["repairs"]]
        lines += ["", f"Questions that needed a SQL repair: {pct(len(rep), len(answered))}; "
                      f"repairs that ended correct: {sum(r['correct'] for r in rep)}", ""]
    wrong = [r for r in rows if "correct" in r and not r["correct"]]
    if wrong:
        lines += ["## Not strictly correct", ""]
        for r in wrong:
            lines += [f"**{r['id']}** ({r.get('source')}, {r.get('level')}) {r.get('q')}  ",
                      f"→ {r.get('reason')}{' · lenient ok' if r.get('lenient') else ''}  "]
            if r.get("sql"):
                lines.append(f"app: `{r['sql']}`  ")
            if r.get("gold_sql"):
                lines.append(f"gold: `{r['gold_sql']}`")
            lines.append("")
    errors = [r for r in rows if "error" in r]
    if errors:
        lines += ["## Errors", ""] + [f"- {r['id']}: {r['error']}" for r in errors] + [""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--dataset", default=str(HERE / "datasets/nl2sql.jsonl"))
    ap.add_argument("--check-gold", action="store_true", help="validate gold SQL only (no LLM calls)")
    ap.add_argument("--source", help="only this source (olist, chinook, northwind)")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=float, default=4.0, help="seconds between questions (free-tier rate limit)")
    ap.add_argument("--resume", help="continue this run file, skipping finished questions")
    args = ap.parse_args()

    ids = source_ids(args.api)
    items = load(Path(args.dataset))
    if args.source:
        items = [i for i in items if i["source"] == args.source]
    if args.only:
        wanted = set(args.only.split(","))
        items = [i for i in items if i["id"] in wanted]

    if args.check_gold:
        sys.exit(1 if check_gold(args.api, items, ids) else 0)

    path = Path(args.resume) if args.resume else HERE / f"results/run-{datetime.now():%Y%m%d-%H%M}.jsonl"
    done = {r["id"]: r for r in load(path)} if path.exists() else {}
    todo = [i for i in items if i["id"] not in done or "error" in done[i["id"]]]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} to run, {len(done)} already in {path.name}")

    gold_cache: dict = {}
    for n, it in enumerate(todo, 1):
        if "turns" in it:
            r = run_conversation(args.api, it, ids, gold_cache, args.delay)
        else:
            r = run_one(args.api, it, ids, gold_cache)
            r.pop("_raw", None)
        done[it["id"]] = r
        with path.open("a") as f:
            f.write(json.dumps(r) + "\n")
        mark = "error" if "error" in r else ("ok" if r["correct"] else ("~ok" if r["lenient"] else "XX"))
        print(f"[{n}/{len(todo)}] {mark:5} {it['id']} {r.get('status', '')} {r.get('wall_ms', '')}ms  "
              f"{r.get('reason', r.get('error', ''))[:70]}")
        if r.get("error") == "language model unavailable":
            print("\nThe language model is unavailable (quota or outage). Stopping; continue later with:\n"
                  f"  python eval/run_eval.py --resume {path}")
            break
        if n < len(todo):
            time.sleep(args.delay)

    rows = [done[i["id"]] for i in load(Path(args.dataset)) if i["id"] in done]
    md = report(rows, path)
    md_path = path.with_suffix(".md")
    md_path.write_text(md)
    print("\n" + md.split("## Latency")[0].strip())
    print(f"\nfull report: {md_path}")


if __name__ == "__main__":
    main()
