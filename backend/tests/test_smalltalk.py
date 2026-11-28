"""Small talk and 'what data do you have' are recognised; real questions never are."""

import pytest

from app.nl2sql.answer import template_answer, unit_notes
from app.nl2sql.smalltalk import classify


@pytest.mark.parametrize("text,kind", [
    ("thank you", "thanks"), ("Thank you!", "thanks"), ("hello", "greeting"), ("never mind", "cancel"),
    ("Nevermind.", "cancel"), ("help", "help"), ("ok", "ack"),
    ("what tables are in the database", "catalog"), ("what kind of data do you have", "catalog"),
    ("what tables are in chinook", "catalog"), ("list all tables", "catalog"),
])
def test_small_talk_is_recognised(text, kind):
    assert classify(text) == kind


@pytest.mark.parametrize("text", [
    "which customers said thank you", "how many orders were canceled", "stop the count by month",
    "what data shows the most revenue", "which table has the most rows", "how many products are in the database",
    "hello how many orders are there",
])
def test_real_questions_are_not_small_talk(text):
    assert classify(text) is None


def test_percent_column_is_never_multiplied_by_100():
    notes = unit_notes(["cancellation_percentage"], [[0.63]])
    assert notes and "0.63 means 0.63 percent" in notes[0] and "Do not multiply by 100" in notes[0]
    assert "0.25 means 25.0 percent" in unit_notes(["late_share"], [[0.25]])[0]
    assert unit_notes(["orders"], [[12]]) == []
    assert template_answer(["percentage_sp"], [[41.98]], {}) == "Percentage sp: 41.98 percent."
