"""Source routing decision and clarification-reply matching (app/profiler/routing.py)."""

import pytest

from app.profiler.routing import decide, resolve_choice


def src(sid, score, coverage):
    return {"source_id": sid, "name": sid, "score": score, "coverage": coverage}


def test_single_source_never_asks():
    assert decide([src("a", 0.2, 0.1)]).source_id == "a"


def test_only_one_source_covers_question():
    d = decide([src("music", 0.75, 1.0), src("shop", 0.74, 0.70), src("trade", 0.70, 0.65)])
    assert d.source_id == "music"


def test_coverage_winner_beats_higher_score():
    # the higher raw score belongs to a source that can't account for the question's words
    d = decide([src("trade", 0.72, 0.80), src("shop", 0.69, 1.0)])
    assert d.source_id == "shop"


def test_equal_coverage_close_scores_asks():
    d = decide([src("a", 0.73, 1.0), src("b", 0.70, 1.0), src("c", 0.69, 0.99), src("d", 0.6, 1.0)])
    assert d.source_id is None and [c["source_id"] for c in d.candidates] == ["a", "b", "c"]  # max 3


def test_equal_coverage_clear_score_lead_routes():
    d = decide([src("a", 0.80, 1.0), src("b", 0.62, 1.0)])
    assert d.source_id == "a"


OPTS = [{"source_id": "o", "name": "Olist e-commerce"}, {"source_id": "c", "name": "Chinook music store"},
        {"source_id": "n", "name": "Northwind trading"}]


@pytest.mark.parametrize("reply,expect", [
    ("Olist", "o"),
    ("the olist one", "o"),
    ("e-commerce", "o"),
    ("I mean the music store", "c"),
    ("chinook please", "c"),
    ("Northwind", "n"),
    ("the first one", "o"),
    ("second", "c"),
    ("number 3", "n"),
    ("the last one", "n"),
    ("how many orders were late last month and why", None),  # long: a new question
    ("which one has more customers", None),
    ("which music genre sells best", None),     # contains "music" but is a new question
    ("olist or chinook", None),                 # picks two
    ("one", "o"),
    ("", None),
])
def test_resolve_choice(reply, expect):
    assert resolve_choice(reply, OPTS) == expect


def test_shared_name_words_are_not_distinctive():
    opts = [{"source_id": "a", "name": "Sales store"}, {"source_id": "b", "name": "Music store"}]
    assert resolve_choice("the store", opts) is None
    assert resolve_choice("music", opts) == "b"


@pytest.mark.parametrize("a,b,same", [
    ("delivery", "delivered", True), ("category", "categories", True), ("brazil", "brazilian", True),
    ("product", "production", True), ("order", "ordinal", False), ("price", "priority", False),
    ("seller", "selection", False), ("genre", "genre", True),
])
def test_same_stem(a, b, same):
    from app.profiler.retriever import _same_stem
    assert _same_stem(a, b) == same
