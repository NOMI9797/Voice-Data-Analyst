"""Schema retrieval: which source, and which tables, does a question need?

A large schema does not fit in a prompt, so for every question we score every
table of every active source with a hybrid of:
  * dense similarity  (local embedding model, fastembed / bge-small, ~20 ms per query)
      - question vs. table document  (name + description + column names)
      - question vs. best single column document
  * BM25 keyword match on the same table documents
  * value hits: words in the question that are real values in a column
      ("sao paulo", "delivered", "Rock") point straight at that table
The best source wins; inside it we keep the top tables and add any "bridge"
tables needed to join them (shortest path over the relationship graph).
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from app.profiler.models import SourceProfile, TableProfile
from app.profiler.routing import decide
from app.profiler.profile import _split_words
from app.profiler.store import get_store
from app.sources.registry import get_registry

log = logging.getLogger(__name__)

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
MODEL_CACHE = "/models/fastembed"
MAX_TABLES = 6
MAX_WITH_BRIDGES = 8

_STOP = set("""a an the of in on for to by with and or is are was were be what which who whom how many much
show me give list tell top most least per each all any from at as it this that these those do does did
i we you our my their there please can could would should get find""".split())


# words that say HOW to aggregate, not WHAT data: every source "covers" them equally
_ANALYTIC = set("""average avg mean total sum count number amount best worst highest lowest largest smallest
biggest longest shortest common often usually share percent percentage rate ratio trend compare compared
there have has had our us value time times duration period month months monthly year years yearly
day days daily week weeks weekly quarter quarters hour hours minute minutes
now break broken breakdown down split group grouped same again instead also then compare show display
give list sort sorted only just about okay ok let let's look see tell want need could would""".split())


_vocab_cache: dict[int, set[str]] = {}


def _vocabulary(idx: "SourceIndex") -> set[str]:
    """Every word in the source's table/column names, descriptions and listed values."""
    key = id(idx)
    if key not in _vocab_cache:
        words = set()
        for doc in idx.table_docs:
            words.update(_tokens(doc))
        words.update(_tokens(idx.profile.description or ""))
        _vocab_cache[key] = words
    return _vocab_cache[key]


def _same_stem(a: str, b: str) -> bool:
    """delivery ~ delivered, category ~ categories, brazil ~ brazilian (not order ~ ordinal)."""
    if a == b:
        return True
    if min(len(a), len(b)) < 5:
        return False
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= max(5, min(len(a), len(b)) - 3)


def _in_vocabulary(word: str, vocab: set[str]) -> bool:
    return word in vocab or any(_same_stem(word, v) for v in vocab if v[:4] == word[:4])


def _content_terms(question: str) -> list[str]:
    seen, out = set(), []
    for w in _tokens(question):
        if w in _ANALYTIC or w.isdigit() or len(w) < 3 or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:8]


def _tokens(s: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", _split_words(s)):
        if w in _STOP:
            continue
        out.append(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


# ------------------------------------------------------------------ embeddings

_model = None
_model_lock = threading.Lock()


def _embedder():
    global _model
    with _model_lock:
        if _model is None:
            from fastembed import TextEmbedding

            Path(MODEL_CACHE).mkdir(parents=True, exist_ok=True)
            _model = TextEmbedding(EMBED_MODEL, cache_dir=MODEL_CACHE)
        return _model


def embed(texts: list[str], query: bool = False) -> np.ndarray:
    m = _embedder()
    vecs = list(m.query_embed(texts) if query else m.embed(texts))
    arr = np.array(vecs, dtype=np.float32)
    return arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)


def warm_up() -> None:
    try:
        embed(["warm up"], query=True)
    except Exception:
        log.exception("embedding warm-up failed")


# ------------------------------------------------------------------ per-source index

def table_document(t: TableProfile) -> str:
    cols = []
    for c in t.columns:
        part = _split_words(c.name)
        if c.description:
            part += f" ({c.description})"
        cols.append(part)
    vals = []
    for c in t.columns:
        if c.categorical and c.kind == "text" and (c.distinct_count or 0) <= 30:
            vals += c.values[:8]
    doc = f"{_split_words(t.name)}. {t.description} Columns: {'; '.join(cols)}."
    if vals:
        doc += f" Values: {', '.join(vals[:30])}."
    return doc


def column_document(t: TableProfile, c) -> str:
    doc = f"{_split_words(t.name)} {_split_words(c.name)}"
    if c.description:
        doc += f": {c.description}"
    return doc


@dataclass
class SourceIndex:
    profile: SourceProfile
    table_names: list[str]
    table_docs: list[str]
    table_vecs: np.ndarray
    col_owner: list[int]  # column doc -> table index
    col_vecs: np.ndarray
    values: dict[str, list[tuple[str, str]]]  # lower value -> [(table, column)]
    graph: dict[str, set[str]] = field(default_factory=dict)
    schema_words: set[str] = field(default_factory=set)


def _build_index(profile: SourceProfile) -> SourceIndex:
    names = [t.name for t in profile.tables]
    tdocs = [table_document(t) for t in profile.tables]
    cdocs, owner = [], []
    for i, t in enumerate(profile.tables):
        for c in t.columns:
            cdocs.append(column_document(t, c))
            owner.append(i)

    # cache vectors on disk, keyed by the exact documents
    key = hashlib.sha1(("\n".join(tdocs) + "\n--\n" + "\n".join(cdocs)).encode()).hexdigest()[:16]
    cache = get_store().dir / f"{profile.source_id}.{key}.npz"
    if cache.exists():
        z = np.load(cache)
        tvecs, cvecs = z["t"], z["c"]
    else:
        tvecs = embed(tdocs) if tdocs else np.zeros((0, 384), np.float32)
        cvecs = embed(cdocs) if cdocs else np.zeros((0, 384), np.float32)
        for old in get_store().dir.glob(f"{profile.source_id}.*.npz"):
            old.unlink(missing_ok=True)
        np.savez(cache, t=tvecs, c=cvecs)

    values: dict[str, list[tuple[str, str]]] = {}
    for t in profile.tables:
        for c in t.columns:
            if not (c.categorical and c.kind == "text"):
                continue
            for v in c.values:
                lv = v.strip().lower()
                if 3 <= len(lv) <= 40 and re.search(r"[a-z]", lv) and lv not in _STOP:
                    values.setdefault(lv, []).append((t.name, c.name))

    graph: dict[str, set[str]] = {n: set() for n in names}
    for r in profile.relationships:
        if r.from_table in graph and r.to_table in graph:
            graph[r.from_table].add(r.to_table)
            graph[r.to_table].add(r.from_table)

    schema_words = {w for t in profile.tables for n in [t.name, *[c.name for c in t.columns]]
                    for w in _split_words(n).split()}
    schema_words |= {w[:-1] for w in schema_words if w.endswith("s")}
    return SourceIndex(profile, names, tdocs, tvecs, owner, cvecs, values, graph, schema_words)


_indexes: dict[str, SourceIndex] = {}
_index_lock = threading.Lock()


def invalidate(source_id: str) -> None:
    _vocab_cache.clear()
    with _index_lock:
        _indexes.pop(source_id, None)


def get_index(source_id: str) -> SourceIndex | None:
    with _index_lock:
        idx = _indexes.get(source_id)
        prof = get_store().get(source_id)
        if prof is None:
            return None
        if idx is None or idx.profile is not prof:
            idx = _build_index(prof)
            _indexes[source_id] = idx
        return idx


# ------------------------------------------------------------------ retrieval

@dataclass
class TableHit:
    source_id: str
    table: str
    score: float
    dense: float
    column: float
    bm25: float
    value_hits: list[str]
    bridge: bool = False


@dataclass
class Retrieval:
    question: str
    source_id: str | None
    source_name: str | None
    dialect: str | None
    tables: list[TableHit]
    source_scores: list[dict]
    ambiguous_source: bool
    value_hits: list[dict]
    schema_text: str
    ms: float
    choices: list[dict] = field(default_factory=list)  # set when the user should pick a source
    route_reason: str = ""


def _value_hits(question: str, idx: SourceIndex) -> dict[str, list[str]]:
    words = re.findall(r"[a-z0-9']+", question.lower())
    found: dict[str, list[str]] = {}
    for n in (4, 3, 2, 1):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            for table, col in idx.values.get(phrase, []):
                found.setdefault(table, []).append(f"{col}='{phrase}'")
    return found


def _value_weight(match: str, idx: SourceIndex) -> float:
    """A question phrase equal to a real DB value is strong evidence, unless it is a
    generic word that also appears in the schema's own names (e.g. 'music', 'order')."""
    phrase = match.split("=", 1)[1].strip("'")
    if phrase in idx.schema_words:
        return 0.03
    return 0.12 if (" " in phrase or len(phrase) >= 5) else 0.08


def _connect(selected: list[str], graph: dict[str, set[str]]) -> list[str]:
    """Add the fewest bridge tables so all selected tables are joinable."""
    if len(selected) < 2:
        return []
    connected = {selected[0]}
    bridges: list[str] = []
    for target in selected[1:]:
        if target in connected:
            continue
        # BFS from the connected component to target
        prev = {n: None for n in connected}
        dq = deque(connected)
        while dq:
            n = dq.popleft()
            if n == target:
                break
            for m in graph.get(n, ()):
                if m not in prev:
                    prev[m] = n
                    dq.append(m)
        if target not in prev:
            connected.add(target)  # not joinable; keep it anyway
            continue
        n = prev[target]
        while n is not None and n not in connected:
            if n not in selected:
                bridges.append(n)
            connected.add(n)
            n = prev[n]
        connected.add(target)
    return bridges


# a follow-up stays on the previous question's source unless another source
# scores clearly higher (follow-ups like "and in 2018?" carry almost no signal)
STICKY_MARGIN = 0.08
STICKY_COVERAGE = 0.1


def retrieve(question: str, source_ids: list[str] | None = None, prefer_source: str | None = None,
             context: str = "") -> Retrieval:
    """context: the previous question, mixed into the search so a follow-up
    finds the tables it is talking about. The returned `question` is unchanged."""
    t0 = time.perf_counter()
    asked = question
    if context:
        question = f"{context} {question}"
    reg = get_registry()
    ids = source_ids or [s.id for s in reg.active()]
    indexes = [i for i in (get_index(sid) for sid in ids) if i is not None]
    if not indexes:
        return Retrieval(asked, None, None, None, [], [], False, [], "", 0.0)

    qv = embed([question], query=True)[0]
    qtok = _tokens(question)

    # one BM25 over all candidate tables so scores are comparable across sources
    corpus, owners = [], []
    for si, idx in enumerate(indexes):
        for ti, doc in enumerate(idx.table_docs):
            corpus.append(_tokens(doc) or ["_"])
            owners.append((si, ti))
    bm = BM25Okapi(corpus).get_scores(qtok) if qtok else np.zeros(len(corpus))
    bm_max = float(max(bm.max(), 1e-9))

    hits_by_source: list[list[TableHit]] = [[] for _ in indexes]
    value_words: list[set[str]] = [set() for _ in indexes]
    k = 0
    for si, idx in enumerate(indexes):
        dense = idx.table_vecs @ qv if len(idx.table_names) else np.zeros(0)
        col_best = np.zeros(len(idx.table_names), np.float32)
        if len(idx.col_owner):
            cs = idx.col_vecs @ qv
            np.maximum.at(col_best, np.array(idx.col_owner), cs)
        vhits = _value_hits(question, idx)
        value_words[si] = {w for ms in vhits.values() for m in ms for w in _tokens(m.split("=", 1)[-1])}
        for ti, name in enumerate(idx.table_names):
            b = float(bm[k]) / bm_max
            k += 1
            vh = sorted(set(vhits.get(name, [])))
            vboost = min(sum(_value_weight(m, idx) for m in vh), 0.2)
            score = 0.45 * float(dense[ti]) + 0.35 * float(col_best[ti]) + 0.20 * b + vboost
            if idx.profile.tables[ti].is_view:
                score -= 0.03  # prefer base tables; views often duplicate them
            hits_by_source[si].append(TableHit(idx.profile.source_id, name, round(score, 4),
                                               round(float(dense[ti]), 4), round(float(col_best[ti]), 4),
                                               round(b, 4), vh))

    # coverage: how much of the question each source can account for (best match
    # of every content word among its table/column documents; a real data value = 1)
    terms = _content_terms(asked)
    tvecs = embed(terms, query=True) if terms else None
    source_scores = []
    for si, idx in enumerate(indexes):
        top = sorted((h.score for h in hits_by_source[si]), reverse=True)[:3]
        cov, per_term = 0.0, {}
        if terms:
            docs = np.vstack([v for v in (idx.table_vecs, idx.col_vecs) if len(v)])
            best_sim = (tvecs @ docs.T).max(axis=1)
            vocab = _vocabulary(idx)
            for ti, w in enumerate(terms):
                # named in the schema or a real data value -> covered; otherwise semantic closeness
                per_term[w] = 1.0 if (w in value_words[si] or _in_vocabulary(w, vocab)) \
                    else round(float(best_sim[ti]), 3)
            cov = sum(per_term.values()) / len(terms)
        source_scores.append({"source_id": idx.profile.source_id, "name": idx.profile.source_name,
                              "score": round(sum(top) / max(len(top), 1), 4),
                              "coverage": round(cov, 4), "term_coverage": per_term})
    order = sorted(range(len(indexes)), key=lambda i: source_scores[i]["score"], reverse=True)
    best = order[0]
    pos = {s["source_id"]: i for i, s in enumerate(source_scores)}
    choices: list[dict] = []
    pref = next((i for i in order if source_scores[i]["source_id"] == prefer_source), None)
    best_cov = max(sc.get("coverage", 0) for sc in source_scores)
    # stay on the conversation's source only if it can account for the new question too:
    # "now break it down by month" has no subject words, so every source covers it equally,
    # but "how many orders were delivered late" names things a music store doesn't have
    if pref is not None and source_scores[best]["score"] - source_scores[pref]["score"] <= STICKY_MARGIN \
            and source_scores[pref].get("coverage", 0) >= best_cov - STICKY_COVERAGE:
        best, reason = pref, "follow-up: stayed on the previous question's source"
    else:
        d = decide(source_scores)
        reason = d.reason
        if d.source_id:
            best = pos[d.source_id]
        elif prefer_source and any(c["source_id"] == prefer_source for c in d.candidates):
            # unclear on its own, but the conversation already picked one of the candidates
            best, reason = pos[prefer_source], "follow-up: the previous question's source is a candidate"
        else:  # too close to call: the caller asks the user (schema below is for the leading candidate)
            best = pos[d.candidates[0]["source_id"]]
            choices = [{"source_id": c["source_id"], "name": c["name"]} for c in d.candidates]
    ambiguous = bool(choices)

    idx = indexes[best]
    ranked = sorted(hits_by_source[best], key=lambda h: h.score, reverse=True)
    top_score = ranked[0].score if ranked else 0
    chosen = [h for h in ranked if h.score >= top_score - 0.08][:MAX_TABLES]
    if len(chosen) < 2 and len(ranked) > 1:
        chosen = ranked[:2]
    by_name = {h.table: h for h in ranked}
    for b in _connect([h.table for h in chosen], idx.graph)[: MAX_WITH_BRIDGES - len(chosen)]:
        h = by_name[b]
        h.bridge = True
        chosen.append(h)
    # neighbours of the two best tables (lookup / translation / fact tables), best first
    have = {h.table for h in chosen}
    neighbours = {n for h in chosen[:2] for n in idx.graph.get(h.table, ())} - have
    for h in sorted((by_name[n] for n in neighbours), key=lambda h: h.score, reverse=True):
        if len(chosen) >= MAX_WITH_BRIDGES:
            break
        if h.score >= top_score - 0.15:
            h.bridge = True
            chosen.append(h)

    value_hits = [{"table": h.table, "match": m} for h in chosen for m in h.value_hits]
    schema = render_schema(idx.profile, [h.table for h in chosen])
    return Retrieval(
        question=asked,
        source_id=idx.profile.source_id,
        source_name=idx.profile.source_name,
        dialect=idx.profile.dialect,
        tables=chosen,
        source_scores=sorted(source_scores, key=lambda s: s["score"], reverse=True),
        ambiguous_source=ambiguous,
        value_hits=value_hits,
        schema_text=schema,
        ms=round((time.perf_counter() - t0) * 1000, 1),
        choices=choices,
        route_reason=reason,
    )


# ------------------------------------------------------------------ prompt rendering

def render_schema(profile: SourceProfile, tables: list[str]) -> str:
    """Compact, LLM-friendly schema for the selected tables only."""
    keep = set(tables)
    fks: dict[tuple[str, str], str] = {}
    for r in profile.relationships:
        if r.from_table in keep and r.to_table in keep:
            fks[(r.from_table, r.from_column)] = f"{r.to_table}.{r.to_column}"

    out = [f"-- Source: {profile.source_name} | engine: {profile.engine} | SQL dialect: {profile.dialect}"]
    if profile.description:
        out.append(f"-- {profile.description}")
    for name in tables:
        t = profile.table(name)
        if t is None:
            continue
        head = f"TABLE {name}" + (" (view)" if t.is_view else "") + f"  -- {t.row_count:,} rows" \
            if t.row_count is not None else f"TABLE {name}"
        if t.description:
            head += f"; {t.description}"
        out.append("")
        out.append(head)
        for c in t.columns:
            line = f"  {c.name} {c.type}"
            if c.primary_key:
                line += " PK"
            if (name, c.name) in fks:
                line += f" -> {fks[(name, c.name)]}"
            notes = []
            if c.description:
                notes.append(c.description)
            if c.kind in ("number", "date") and c.min is not None:
                notes.append(f"range {c.min} .. {c.max}")
            if c.categorical and c.kind == "text":
                if (c.distinct_count or 0) <= 15:
                    notes.append("values: " + ", ".join(c.values))
                else:
                    notes.append(f"{c.distinct_count} distinct, e.g. " + ", ".join(c.values[:5]))
            elif c.kind == "text" and c.samples:
                notes.append("e.g. " + ", ".join(s[:40] for s in c.samples[:3]))
            if c.null_fraction:
                notes.append(f"{c.null_fraction:.0%} null")
            if notes:
                line += "  -- " + "; ".join(notes)
            out.append(line)
    joins = [f"{a}.{b} = {v}" for (a, b), v in fks.items()]
    if joins:
        out.append("")
        out.append("JOINS: " + " | ".join(joins))
    return "\n".join(out)


def widen(r: Retrieval, extra_question: str, n: int = 2) -> Retrieval:
    """Adds the best tables for `extra_question` (searched on its own, same source) to a
    retrieval. Used when the user answers a clarification ("by money spent"): searched
    together with the vague question, those words are drowned out by it."""
    if not r.source_id or not extra_question.strip():
        return r
    idx = get_index(r.source_id)
    if idx is None:
        return r
    extra = retrieve(extra_question, [r.source_id])
    have = [t.table for t in r.tables]
    add = [t for t in extra.tables if not t.bridge and t.table not in have][:n]
    if not add:
        return r
    tables = list(r.tables) + add
    names = [t.table for t in tables]
    by_name = {t.table: t for t in extra.tables}
    for b in _connect(names, idx.graph):
        if b not in names and len(tables) < MAX_WITH_BRIDGES + n:
            h = by_name.get(b) or TableHit(r.source_id, b, 0.0, 0.0, 0.0, 0.0, [], True)
            h.bridge = True
            tables.append(h)
            names.append(b)
    return Retrieval(**{**asdict(r), "tables": tables,
                        "value_hits": r.value_hits + [v for v in extra.value_hits if v not in r.value_hits],
                        "schema_text": render_schema(idx.profile, names)})


def add_tables(r: Retrieval, names: list[str]) -> Retrieval:
    """Adds named tables of the same source (plus join paths) to a retrieval. Used by
    self repair: the SQL writer referred to a real table that retrieval had left out."""
    idx = get_index(r.source_id) if r.source_id else None
    if idx is None:
        return r
    have = [t.table for t in r.tables]
    add = [n for n in names if n not in have and idx.profile.table(n) is not None]
    if not add:
        return r
    tables = list(r.tables) + [TableHit(r.source_id, n, 0.0, 0.0, 0.0, 0.0, []) for n in add]
    names_all = [t.table for t in tables]
    for b in _connect(names_all, idx.graph):
        if b not in names_all:
            tables.append(TableHit(r.source_id, b, 0.0, 0.0, 0.0, 0.0, [], True))
            names_all.append(b)
    return Retrieval(**{**asdict(r), "tables": tables, "schema_text": render_schema(idx.profile, names_all)})


def tables_named_in(sql: str, source_id: str) -> list[str]:
    """Real tables of the source that the SQL mentions, matched case-insensitively and
    ignoring underscores/spaces ("order_details" -> "Order Details")."""
    idx = get_index(source_id)
    if idx is None:
        return []
    flat = re.sub(r"[\s_\"`\[\]]+", "", sql.lower())
    words = set(re.findall(r"[a-z0-9]+", re.sub(r"[_\"`]", " ", sql.lower())))
    out = []
    for t in idx.profile.tables:
        key = re.sub(r"[\s_]+", "", t.name.lower())
        short = key.rsplit(".", 1)[-1]
        if short in words or (len(short) > 5 and short in flat):
            out.append(t.name)
    return out
