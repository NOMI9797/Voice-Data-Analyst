"""Chart selection rules (app/viz/chart.py)."""

import pytest

from app.viz.chart import choose_chart, classify_column


@pytest.mark.parametrize("name,values,kind", [
    ("order_month", ["2017-01", "2017-02"], "temporal"),
    ("purchased_at", ["2017-01-05 10:22:01", "2018-03-01 00:00:00+00:00"], "temporal"),
    ("day", ["2017-01-05", "2017-01-06"], "temporal"),
    ("year", [2016, 2017, 2018], "temporal"),
    ("quarter", ["2017-Q1", "2017-Q2"], "temporal"),
    ("revenue", [10.5, 20, 30], "measure"),
    ("year_count", [5, 7], "measure"),               # not a year, just a count
    ("customer_id", [101, 102], "identifier"),
    ("zip_code_prefix", [1001, 2002], "identifier"),
    ("state", ["SP", "RJ"], "category"),
    ("genre", ["Rock", None, "Jazz"], "category"),
    ("empty", [None, None], "category"),
])
def test_classify(name, values, kind):
    assert classify_column(name, values) == kind


def test_empty_result():
    assert choose_chart(["a"], [])["type"] == "empty"


def test_single_value_is_kpi():
    c = choose_chart(["total_orders"], [[99441]])
    assert c["type"] == "kpi" and c["y"] == ["total_orders"]


def test_single_row_several_numbers_is_kpi():
    c = choose_chart(["orders", "revenue", "avg_review"], [[100, 5000.5, 4.1]])
    assert c["type"] == "kpi" and len(c["y"]) == 3


def test_monthly_trend_is_line():
    rows = [[f"2017-{m:02d}", m * 1000.0] for m in range(1, 13)]
    c = choose_chart(["month", "revenue"], rows)
    assert (c["type"], c["x"], c["y"]) == ("line", "month", ["revenue"])
    assert "bar" in c["alternatives"] and "table" in c["alternatives"]


def test_two_time_points_is_bar():
    c = choose_chart(["year", "orders"], [[2017, 10], [2018, 20]])
    assert c["type"] == "bar"


def test_time_with_small_category_is_multi_line():
    rows = [[f"2017-{m:02d}", s, m * 10] for m in range(1, 7) for s in ("SP", "RJ", "MG")]
    c = choose_chart(["month", "state", "orders"], rows)
    assert (c["type"], c["x"], c["series"]) == ("line", "month", "state")


def test_category_bar_vertical_when_short():
    rows = [["Rock", 120], ["Jazz", 40], ["Pop", 60]]
    c = choose_chart(["genre", "tracks"], rows)
    assert (c["type"], c["x"], c["orientation"]) == ("bar", "genre", "vertical")


def test_long_labels_go_horizontal():
    rows = [["health_beauty_products", 10], ["computers_accessories", 8]]
    assert choose_chart(["category", "n"], rows)["orientation"] == "horizontal"


def test_many_categories_are_capped_with_note():
    rows = [[f"city {i}", 1000 - i] for i in range(120)]
    c = choose_chart(["city", "customers"], rows)
    assert c["type"] == "bar" and c["limit"] == 30 and "120" in c["note"]


def test_measures_on_different_scales_are_not_mixed():
    rows = [["SP", 41746, 5_998_226.96], ["RJ", 12852, 2_144_379.69], ["MG", 11635, 1_872_257.26]]
    c = choose_chart(["state", "orders", "revenue"], rows)
    assert c["y"] == ["orders"]


def test_measures_on_similar_scales_are_combined():
    rows = [["SP", 120.5, 135.2], ["RJ", 125.0, 140.1]]
    c = choose_chart(["state", "avg_price", "avg_paid"], rows)
    assert c["y"] == ["avg_price", "avg_paid"]


def test_two_categories_one_measure_is_grouped_bar():
    rows = [[s, p, 5] for s in ("SP", "RJ", "MG", "PR") for p in ("credit_card", "boleto")]
    c = choose_chart(["state", "payment_type", "orders"], rows)
    assert (c["type"], c["x"], c["series"]) == ("bar", "state", "payment_type")


def test_ids_are_not_measures():
    c = choose_chart(["customer_id", "orders"], [[1, 5], [2, 3], [3, 9]])
    assert c["type"] == "bar" and c["x"] == "customer_id" and c["y"] == ["orders"]


def test_two_measures_without_dimension_is_scatter():
    rows = [[i * 1.5, i * 2.0 + 1] for i in range(20)]
    c = choose_chart(["price", "freight_value"], rows)
    assert (c["type"], c["x"], c["y"]) == ("scatter", "price", ["freight_value"])


def test_text_only_is_table():
    c = choose_chart(["name", "email"], [["a", "a@x"], ["b", "b@x"]])
    assert c["type"] == "table" and c["alternatives"] == []
