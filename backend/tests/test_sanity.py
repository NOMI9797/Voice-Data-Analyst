"""Checks for results that ran without error but look wrong (app/nl2sql/sanity.py)."""

from app.nl2sql.sanity import suspicious


def test_normal_result_is_fine():
    assert suspicious("orders per state", ["state", "orders"], [["SP", 10], ["RJ", 5]], False) is None


def test_empty_result():
    assert "no rows" in suspicious("orders from sao paulo", ["orders"], [], False)


def test_all_null_aggregate():
    assert "NULL" in suspicious("average review score in Atlantis", ["avg_score"], [[None]], False)


def test_null_label_column_in_wide_result_is_not_flagged():
    rows = [[None, "x", 1, 2], [None, "y", 3, 4]]
    assert suspicious("q", ["region", "name", "a", "b"], rows, False) is None


def test_negative_count():
    assert "negative" in suspicious("orders per state", ["state", "order_count"], [["SP", -3]], False)


def test_negative_non_count_is_fine():
    assert suspicious("profit by state", ["state", "profit"], [["SP", -3.5]], False) is None


def test_percentage_over_100():
    assert "above 100" in suspicious("share of late orders", ["late_pct"], [[812.0]], False)


def test_raw_rows_for_aggregate_question():
    rows = [[i] for i in range(1000)]
    assert "row limit" in suspicious("how many orders were late", ["order_id"], rows, True)


def test_truncated_listing_question_is_fine():
    rows = [[i] for i in range(1000)]
    assert suspicious("list the product ids", ["product_id"], rows, True) is None
