"""Domain word correction: fix misheard names BEFORE any SQL is written.

Speech recognition mishears names it has never seen ("Curitiba" -> "Cartiba",
"Sao Paulo" -> "sow Paulo", "AC/DC" -> "AC slash DC"). The database itself
knows the right spelling, so we compare the transcript with the real values
collected by the profiler (categorical / name-like text columns):

  1. vocabulary  = normalized distinct values of every active source
                   (lowercase, no accents, "_" and "/" -> space)
  2. candidates  = every 1..4-word span of the transcript, compared with
                   vocabulary entries by
                      - spelling  : rapidfuzz ratio on the spaceless strings
                      - sound     : ratio of Metaphone codes (catches "cure it eva")
  3. protection  = frequent English words ("orders", "rock", "top") are never
                   replaced by a different word; spans that already equal a
                   value are kept and reported as recognized entities
  4. selection   = best non-overlapping spans above the threshold; when two
                   different values score almost the same it is reported as
                   ambiguous (for a clarification question) instead of guessing

No database-specific code: everything comes from the profiles.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field

import jellyfish
from rapidfuzz import fuzz, process
from wordfreq import zipf_frequency

from app.profiler.models import SourceProfile
from app.profiler.store import get_store
from app.sources.registry import get_registry

log = logging.getLogger(__name__)

MAX_NGRAM = 4
ACCEPT = 86.0  # includes up to +3 popularity prior  # combined score (0-100) needed to replace a span
COMMON_ZIPF = 4.2  # words at least this frequent in English are protected
AMBIGUOUS_GAP = 2.0  # two different values within this score -> ambiguous
SKIP_COLUMN = re.compile(r"(comment|message|description|desc$|note|review_text|body|title$|url|path|photo|"
                         r"phone|fax|e_?mail|zip|postal|cep|address|street|password|token|hash)", re.I)

_STOPWORDS = set("""a an the of in on for to by with and or is are was were be been what which who whom how
many much show me give list tell top most least per each all any from at as it its this that these those do does
did i we you our my their there please can could would should get find than then same now also only just
break down by month year week day quarter last next first total average sum count number""".split())


# Lowercases text and removes accents and punctuation for fair comparison.
def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[_/\\\-&+.,'’`\"()]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Removes spaces so 'Sao Paulo' and 'saopaulo' compare equal.
def _compact(s: str) -> str:
    return s.replace(" ", "")


# The 'sound' key of a word (Metaphone), to match words that sound alike.
def _phonetic(s: str) -> str:
    try:
        return "".join(jellyfish.metaphone(w) for w in s.split())
    except Exception:
        return s


# Turns words Whisper writes for symbols back into symbols.
def _spoken_symbols(text: str) -> str:
    """Whisper writes some symbols as words."""
    t = re.sub(r"\bslash\b", "/", text, flags=re.I)
    t = re.sub(r"\bampersand\b", "&", t, flags=re.I)
    return t


# One real database value, with its spelling and sound keys.
@dataclass
class VocabEntry:
    value: str  # original spelling from the database
    norm: str
    compact: str
    phon: str
    n_words: int
    locations: list[tuple[str, str, str, str]] = field(default_factory=list)  # (source_id, source, table, column)
    popularity: float = 0.0  # 1.0 = most frequent value of its column, 0.0 = rarest


# All real values of the active databases, ready for matching.
@dataclass
class Vocabulary:
    entries: list[VocabEntry]
    by_norm: dict[str, VocabEntry]
    compacts: list[str]
    phons: list[str]
    built_ms: float


# Picks the name-like and category columns whose values go into the word list.
def _columns_for_vocab(profile: SourceProfile):
    for t in profile.tables:
        if t.is_view:
            continue  # views repeat base-table values
        for c in t.columns:
            if not (c.categorical and c.kind == "text" and c.values):
                continue
            if SKIP_COLUMN.search(c.name):
                continue
            sample = c.values[:200]
            if sum(len(v.split()) for v in sample) / len(sample) > 5:
                continue  # sentences, not names
            letters = sum(bool(re.search(r"[A-Za-z]", v)) for v in sample) / len(sample)
            if letters < 0.8:
                continue  # codes / numbers
            yield t, c


# Builds the word list of real values from the active databases.
def build_vocabulary(source_ids: list[str]) -> Vocabulary:
    t0 = time.perf_counter()
    store, reg = get_store(), get_registry()
    by_norm: dict[str, VocabEntry] = {}
    for sid in source_ids:
        prof = store.get(sid)
        if prof is None:
            continue
        sname = reg.get(sid).name
        for t, c in _columns_for_vocab(prof):
            total = len(c.values)
            for rank, v in enumerate(c.values):  # values are sorted by frequency (profiler)
                n = normalize(v)
                if len(_compact(n)) < 3 or len(n) > 60:
                    continue
                e = by_norm.get(n)
                if e is None:
                    e = VocabEntry(v, n, _compact(n), _phonetic(n), len(n.split()))
                    by_norm[n] = e
                if len(e.locations) < 6:
                    e.locations.append((sid, sname, t.name, c.name))
                e.popularity = max(e.popularity, 1 - rank / total)
    entries = list(by_norm.values())
    return Vocabulary(entries, by_norm, [e.compact for e in entries], [e.phon for e in entries],
                      round((time.perf_counter() - t0) * 1000, 1))


# Builds a word list from given values (used in tests).
def vocabulary_from_items(items: list[tuple[str, str, str]], source: str = "test") -> Vocabulary:
    """Build a vocabulary from (value, table, column) items, in frequency order per column."""
    by_norm: dict[str, VocabEntry] = {}
    per_col: dict[tuple[str, str], int] = {}
    for value, table, column in items:
        per_col[(table, column)] = per_col.get((table, column), 0) + 1
    seen: dict[tuple[str, str], int] = {}
    for value, table, column in items:
        n = normalize(value)
        if len(_compact(n)) < 3:
            continue
        rank = seen.get((table, column), 0)
        seen[(table, column)] = rank + 1
        e = by_norm.setdefault(n, VocabEntry(value, n, _compact(n), _phonetic(n), len(n.split())))
        e.locations.append((source, source, table, column))
        e.popularity = max(e.popularity, 1 - rank / per_col[(table, column)])
    entries = list(by_norm.values())
    return Vocabulary(entries, by_norm, [e.compact for e in entries], [e.phon for e in entries], 0.0)


_vocab: Vocabulary | None = None
_vocab_key: tuple = ()
_lock = threading.Lock()


# Returns the word list for the active databases (cached).
def get_vocabulary() -> Vocabulary:
    """Cached vocabulary for the currently active, profiled sources."""
    global _vocab, _vocab_key
    store = get_store()
    ids = [s.id for s in get_registry().active()]
    key = tuple((sid, getattr(store.get(sid), "profiled_at", None)) for sid in ids)
    with _lock:
        if _vocab is None or key != _vocab_key:
            _vocab = build_vocabulary(ids)
            _vocab_key = key
            log.info("vocabulary: %d entries in %.0f ms", len(_vocab.entries), _vocab.built_ms)
        return _vocab


# Forgets the word list so it is rebuilt when sources change.
def invalidate(_source_id: str | None = None) -> None:
    global _vocab
    with _lock:
        _vocab = None


# ------------------------------------------------------------------ correction

# One fix: the misheard words, the real value, and the scores.
@dataclass
class Correction:
    original: str
    corrected: str
    score: float
    spelling: float
    sound: float
    locations: list[dict]
    alternatives: list[str] = field(default_factory=list)
    start: int = 0  # word index span in the transcript
    end: int = 0


# The corrected sentence, the fixes made, and the names recognised.
@dataclass
class CorrectionResult:
    original: str
    corrected: str
    corrections: list[Correction]
    entities: list[dict]  # spans that already exactly match a database value
    ambiguous: list[Correction]
    ms: float


# Removes punctuation around a word.
def _strip_punct(s: str) -> str:
    return re.sub(r"[?.!,;:]+$", "", s)


# True for ordinary English words, which are never 'corrected'.
def _is_common(word: str) -> bool:
    return word in _STOPWORDS or zipf_frequency(word, "en") >= COMMON_ZIPF


# Where a value lives (source, table, column).
def _locs(e: VocabEntry) -> list[dict]:
    return [{"source_id": s, "source": n, "table": t, "column": c} for s, n, t, c in e.locations]


# MAIN: fixes misheard names by comparing the sentence with real database values (spelling + sound).
def correct(text: str, vocab: Vocabulary | None = None) -> CorrectionResult:
    t0 = time.perf_counter()
    vocab = vocab or get_vocabulary()
    raw_words = _spoken_symbols(text).split()
    words = [normalize(_strip_punct(w)) for w in raw_words]
    if not vocab.entries or not words:
        return CorrectionResult(text, text, [], [], [], 0.0)

    found: list[Correction] = []
    exact: list[tuple[int, int, VocabEntry]] = []  # spans that already equal a database value

    # context: columns of names that are already spelled correctly in this question
    context_cols = set()
    for n in range(MAX_NGRAM, 0, -1):
        for i in range(len(words) - n + 1):
            hit = vocab.by_norm.get(" ".join(w for w in words[i:i + n] if w))
            if hit and not all(_is_common(w) for w in words[i:i + n] if w):
                context_cols |= {(loc[0], loc[2], loc[3]) for loc in hit.locations}

    for n in range(MAX_NGRAM, 0, -1):
        for i in range(len(words) - n + 1):
            span_words = [w for w in words[i:i + n] if w]
            if not span_words:
                continue
            span = " ".join(span_words)
            compact = _compact(span)
            if len(compact) < 3:
                continue
            if any(s <= i and i + n <= e for s, e, _ in exact):
                continue  # inside a longer exact match

            hit = vocab.by_norm.get(span)
            if hit:
                exact.append((i, i + n, hit))
                continue
            # a span made only of common words / stopwords is left alone
            if all(_is_common(w) for w in span_words):
                continue
            # spans must not start or end with a stopword ("of curitiba")
            if span_words[0] in _STOPWORDS or span_words[-1] in _STOPWORDS:
                continue

            phon = _phonetic(span)
            cands: dict[int, None] = {}
            for _, _, idx in process.extract(compact, vocab.compacts, scorer=fuzz.ratio, limit=6, score_cutoff=60):
                cands[idx] = None
            for _, _, idx in process.extract(phon, vocab.phons, scorer=fuzz.ratio, limit=6, score_cutoff=70):
                cands[idx] = None
            scored = []
            for idx in cands:
                e = vocab.entries[idx]
                if abs(e.n_words - n) > 2:
                    continue
                # a span longer than the value must not swallow an ordinary word at its edge:
                # "led zeppelin have" -> "Led Zeppelin" would delete "have"
                if n > e.n_words and (_is_common(span_words[0]) or _is_common(span_words[-1])):
                    continue
                spelling = fuzz.ratio(compact, e.compact)
                sound = fuzz.ratio(phon, e.phon) if phon and e.phon else 0.0
                score = 0.55 * spelling + 0.45 * sound
                # priors: frequent values are likelier, and so are values from the same
                # column as other names in the question ("chai, chang and ikura")
                score += 3.0 * e.popularity
                if context_cols and any((l[0], l[2], l[3]) in context_cols for l in e.locations):
                    score += 6.0
                # short strings need near-exact matches
                if len(e.compact) <= 4 and spelling < 85:
                    continue
                # one word changed on sound alone is too risky ("aussie" -> the city "assai"):
                # it must also be spelled fairly alike
                if n == 1 and spelling < 75:
                    continue
                # a single common English word may only become a value that sounds identical
                if n == 1 and _is_common(span_words[0]) and (sound < 100 or spelling < 75):
                    continue
                scored.append((score, spelling, sound, e))
            if not scored:
                continue
            scored.sort(key=lambda x: x[0], reverse=True)
            best = scored[0]
            if best[0] < ACCEPT:
                continue
            alts = [x[3].value for x in scored[1:4]
                    if best[0] - x[0] <= AMBIGUOUS_GAP and x[3].norm != best[3].norm]
            found.append(Correction(
                original=" ".join(raw_words[i:i + n]), corrected=best[3].value,
                score=round(best[0], 1), spelling=round(best[1], 1), sound=round(best[2], 1),
                locations=_locs(best[3]), alternatives=alts, start=i, end=i + n))

    # Selection. Exact matches win, except that a longer, high-confidence correction
    # beats a shorter exact match inside it ("bella horizonte" -> "belo horizonte",
    # even though "horizonte" alone is also a city).
    # longer spans get a small bonus: "porto alegri" -> "porto alegre" beats "alegri" -> "alegria"
    found.sort(key=lambda c: (c.score + 2.5 * (c.end - c.start - 1), c.end - c.start), reverse=True)
    chosen: list[Correction] = []
    taken: set[int] = set()
    dropped_exact: set[int] = set()
    for c in found:
        span = set(range(c.start, c.end))
        if span & taken:
            continue
        inner = [k for k, (s, e, _) in enumerate(exact) if s < c.end and c.start < e]
        if inner:
            if c.score < 90 or any(not (c.start <= exact[k][0] and exact[k][1] <= c.end
                                        and exact[k][1] - exact[k][0] < c.end - c.start) for k in inner):
                continue
            if any(exact[k][2].norm == normalize(c.corrected) for k in inner):
                continue  # same value as the exact match inside: it would only swallow extra words
            dropped_exact.update(inner)
        chosen.append(c)
        taken |= span

    # exact matches are rewritten to the database spelling when only the formatting differs
    # ("AC / DC" -> "AC/DC"), and reported as recognized entities
    edits: list[tuple[int, int, str]] = [(c.start, c.end, c.corrected) for c in chosen]
    entities: list[dict] = []
    for k, (s, e, hit) in enumerate(exact):
        if k in dropped_exact:
            continue
        said = " ".join(raw_words[s:e])
        entities.append({"text": said, "value": hit.value, "locations": _locs(hit)})
        if _strip_punct(said).lower() != hit.value.lower():
            edits.append((s, e, hit.value))
    edits.sort()

    out, i = [], 0
    for s, e, value in edits:
        out += raw_words[i:s]
        trail = re.search(r"[?.!,;:]+$", raw_words[e - 1])
        out.append(value + (trail.group(0) if trail else ""))
        i = e
    out += raw_words[i:]
    corrected = " ".join(out)
    chosen.sort(key=lambda c: c.start)

    return CorrectionResult(
        original=text,
        corrected=corrected,
        corrections=[c for c in chosen if not c.alternatives],
        entities=entities,
        ambiguous=[c for c in chosen if c.alternatives],
        ms=round((time.perf_counter() - t0) * 1000, 1),
    )


# List of real names for Whisper's prompt (built, but not used in the app any more).
def whisper_hint(max_chars: int = 500) -> str:
    """Short list of real database names to bias Whisper toward them.
    Prefers values from small lookup columns (categories, genres, states, cities)."""
    vocab = get_vocabulary()
    ranked = sorted(vocab.entries, key=lambda e: (len(e.locations) == 0, -len(e.locations), len(e.value)))
    words, size = [], 0
    for e in ranked:
        v = e.value.replace("_", " ")
        if zipf_frequency(normalize(v).split()[0], "en") >= COMMON_ZIPF:
            continue  # the model already knows common words
        if size + len(v) + 2 > max_chars:
            break
        words.append(v)
        size += len(v) + 2
    return ", ".join(words)
