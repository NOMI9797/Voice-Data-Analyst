"""Which data source should answer a question, or should we ask the user?

Inputs are the per-source scores from retrieval:
  score     hybrid table-match score (dense + BM25 + values)
  coverage  how much of the question the source accounts for: each content
            word is 1.0 if it names something in the schema or a real value,
            otherwise its best semantic match

Rule (thresholds chosen on eval/datasets/routing.jsonl, see eval/routing_eval.py):
  1. candidates = sources whose coverage is within COVERAGE_DELTA of the best
  2. one candidate                         -> route to it
  3. several, and the best score leads the
     next candidate by at least SCORE_MARGIN -> route to the best
  4. otherwise                             -> ask, offering up to 3 candidates
Pure Python (no model imports) so the eval script can run it too.
"""

from __future__ import annotations

from dataclasses import dataclass

COVERAGE_DELTA = 0.05
SCORE_MARGIN = 0.15
MAX_CHOICES = 3


# Which source was chosen (or none = ask the user), and why.
@dataclass
class Decision:
    source_id: str | None      # chosen source; None means "ask"
    candidates: list[dict]     # the sources the question could be about (best first)
    reason: str


# Chooses the database: one clear fit -> use it; a genuine tie -> ask which one.
def decide(source_scores: list[dict], coverage_delta: float = COVERAGE_DELTA,
           score_margin: float = SCORE_MARGIN) -> Decision:
    if not source_scores:
        return Decision(None, [], "no source")
    if len(source_scores) == 1:
        return Decision(source_scores[0]["source_id"], source_scores, "only one source is active")
    best_cov = max(s.get("coverage", 0) for s in source_scores)
    cands = sorted((s for s in source_scores if s.get("coverage", 0) >= best_cov - coverage_delta),
                   key=lambda s: s["score"], reverse=True)
    if len(cands) == 1:
        return Decision(cands[0]["source_id"], cands, "only this source covers the question")
    lead = cands[0]["score"] - cands[1]["score"]
    if lead >= score_margin:
        return Decision(cands[0]["source_id"], cands, f"clear lead ({lead:.3f}) among sources that cover it")
    return Decision(None, cands[:MAX_CHOICES], "several sources fit about equally well")


# ---------------------------------------------------------------- the user's reply

_ORDINALS = {
    0: {"first", "1", "1st", "one", "former"},
    1: {"second", "2", "2nd", "two"},
    2: {"third", "3", "3rd", "three"},
}
_FILLER = {"the", "a", "an", "one", "data", "database", "db", "source", "please", "i", "mean", "meant",
           "use", "in", "from", "for", "about", "it", "its", "that", "this", "is", "go", "with", "let's", "lets"}


# Splits text into lowercase words.
def _words(s: str) -> list[str]:
    import re
    return re.findall(r"[a-z0-9']+", s.lower().replace("-", " "))


# Understands the answer to 'which database?' ('Olist', 'the second one').
def resolve_choice(reply: str, options: list[dict]) -> str | None:
    """Matches a reply like "Olist", "the music store", "second one" or "the last
    one" to one of the offered sources. Returns its source_id, or None when the
    reply doesn't clearly pick exactly one option; then it is treated as a new
    question ("which music genre sells best" must not count as picking "music store")."""
    words = _words(reply)
    if not words or not options or len(words) > 6:
        return None
    all_name_words = set().union(*(set(_words(o["name"])) for o in options))
    position_words = set().union(*_ORDINALS.values()) | {"last", "latter", "number", "option", "choice"}
    other = [w for w in words if w not in _FILLER and w not in all_name_words and w.rstrip("s") not in all_name_words
             and w not in position_words]
    if other:
        # words that are neither a source name nor a position: usually a new question, unless
        # it is a short reply where speech recognition mangled the name ("or list" for "Olist")
        if len(other) > 2 or words[0] in _QUESTION_START:
            return None
        return _sounds_like_option(words, options)
    # by name: distinctive words of one source name ("olist", "music", "northwind" ...)
    scores = []
    for o in options:
        distinctive = set(_words(o["name"])) - _FILLER - set().union(
            *(set(_words(x["name"])) for x in options if x is not o))
        scores.append(sum(1 for w in words if w in distinctive or w.rstrip("s") in distinctive))
    if max(scores) > 0:
        return options[scores.index(max(scores))]["source_id"] if scores.count(max(scores)) == 1 else None
    # by position
    if "last" in words or "latter" in words:
        return options[-1]["source_id"]
    hits = {i for i, ws in _ORDINALS.items() if i < len(options) and any(w in ws for w in words)}
    if len(hits) == 1:
        return options[hits.pop()]["source_id"]
    return _sounds_like_option(words, options)


# Understands database names Whisper mangled ('or list' -> Olist).
def _sounds_like_option(words: list[str], options: list[dict]) -> str | None:
    """Speech recognition mangles short brand names: "Olist" comes back as "or list".
    Compare the reply, with spaces removed, to each option's distinctive name words and
    pick the option with the most of them matched ("or list e-commerce store": olist and
    commerce match Olist e-commerce, only store matches Chinook music store)."""
    from difflib import SequenceMatcher

    spans = {"".join(words[i:i + n]) for n in (1, 2, 3) for i in range(len(words) - n + 1)}
    scored: list[tuple[int, float, str]] = []
    for o in options:
        distinctive = {d for d in set(_words(o["name"])) - _FILLER - set().union(
            *(set(_words(x["name"])) for x in options if x is not o)) if len(d) >= 4}
        ratios = [max((SequenceMatcher(None, sp, d).ratio() for sp in spans), default=0.0) for d in distinctive]
        matched = [r for r in ratios if r >= 0.8]
        scored.append((len(matched), max(ratios, default=0.0), o["source_id"]))
    scored.sort(reverse=True)
    if not scored or scored[0][0] == 0:
        return None
    if len(scored) > 1 and scored[0][:2] == scored[1][:2]:
        return None  # a tie: don't guess
    if len(scored) > 1 and scored[0][0] == scored[1][0] and scored[0][1] - scored[1][1] < 0.1:
        return None
    return scored[0][2]


_QUESTION_START = {"how", "what", "which", "who", "whom", "when", "where", "why", "show", "list", "give",
                   "tell", "compare", "find", "count", "is", "are", "does", "do", "can"}


# Turns 'first', 'second', 'the last one' into a position.
def _position(words: list[str], n: int) -> int | None:
    """"second", "the 2nd one", "number 3", "the last one" -> index; "one" alone means the first."""
    if "last" in words or "latter" in words:
        return n - 1
    hits = {i for i, ws in _ORDINALS.items() if i < n and any(w in ws - {"one"} for w in words)}
    if not hits and "one" in words and len(words) <= 2:
        hits = {0}
    return hits.pop() if len(hits) == 1 else None


# Understands the answer to a vague-question clarification ('by spending').
def resolve_intent_reply(reply: str, options: list[str]) -> str | None:
    """Answer to a clarification like "Top customers by total spending or by number
    of orders?". Returns the chosen option text, the reply itself when it is a
    free-form answer ("by how much they spent"), or None when the reply is clearly
    a new question ("how many orders were late last month")."""
    words = _words(reply)
    if not words:
        return None
    if words[0] in _QUESTION_START and len(words) >= 4:
        return None  # a new question, not an answer
    position_words = set().union(*_ORDINALS.values()) | {"last", "latter", "number", "option", "choice"}
    if options and len(words) <= 4 and all(w in _FILLER or w in position_words for w in words):
        i = _position(words, len(options))
        if i is not None:
            return options[i]
    # by content: words that belong to exactly one option ("spending", "orders")
    reply_set = set(words) | {w.rstrip("s") for w in words}
    scores = []
    for o in options:
        distinctive = set(_words(o)) - _FILLER - {"by", "of", "number", "total"} - set().union(
            *(set(_words(x)) for x in options if x is not o))
        distinctive |= {w.rstrip("s") for w in distinctive}
        scores.append(len(reply_set & distinctive))
    if options and max(scores) > 0 and scores.count(max(scores)) == 1 and len(words) <= 8:
        return options[scores.index(max(scores))]
    return reply.strip() if len(words) <= 12 else None
