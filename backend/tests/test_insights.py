"""Facts for the spoken explanation (app/nl2sql/insights.py)."""

from app.nl2sql.insights import facts, say_number, say_time
from app.viz.chart import choose_chart


def run(columns, rows):
    return facts(columns, rows, choose_chart(columns, rows))


def test_say_number_and_time():
    assert say_number(1297) == "1,297" and say_number(4.0867) == "4.09"
    assert say_number(12_542_084.19) == "about 12.5 million" and say_number(41746) == "about 41,700"
    assert say_time("2017-11-01") == "November 2017" and say_time("2017-03") == "March 2017"
    assert say_time(2018) == "2018"


def test_monthly_trend_peak_vs_average():
    rows = [["2017-01", 100], ["2017-02", 100], ["2017-03", 120], ["2017-04", 80]]
    f = run(["month", "sales"], rows)
    assert f[0] == "Sales peaked in March 2017 at 120, about 20% above the average of 100."
    assert f[1] == "From January 2017 to April 2017 it fell from 100 to 80."
    assert len(f) == 2  # the lowest month is the last one, already said


def test_incomplete_last_period_is_flagged():
    rows = [[f"2018-0{m}-01", 6000 + m] for m in range(1, 9)] + [["2018-09-01", 16]]
    assert any("may be incomplete" in x for x in run(["month", "orders"], rows))


def test_category_share_and_gap():
    rows = [["Rock", 1297], ["Latin", 579], ["Metal", 374], ["Jazz", 130]]
    f = run(["genre", "tracks"], rows)
    assert f[0] == "Rock is highest with 1,297 tracks, 54% of the total across the 4 shown."
    assert f[1] == "That is 2.2 times the next one, Latin (579)."
    assert f[2] == "Lowest is Jazz with 130 tracks."


def test_kpi_with_label():
    assert run(["genre", "total_tracks"], [["Rock", 1297]]) == ["Rock, with 1,297 total tracks."]


def test_kpi_numbers():
    assert run(["orders", "revenue"], [[99441, 16008872.12]]) == ["Orders: about 99,400.", "Revenue: about 16.0 million."]


def test_series_leader():
    rows = [[y, c, v] for y in (2021, 2022, 2023) for c, v in (("USA", 100), ("Canada", 60))]
    assert run(["year", "country", "revenue"], rows)[0] == "USA leads overall with 300 revenue, ahead of Canada (180)."


def test_scatter_correlation():
    rows = [[i, 2 * i + 1] for i in range(10)]
    assert "strongly together" in run(["price", "freight_value"], rows)[0]


def test_empty():
    assert facts(["a"], [], {"type": "empty"}) == ["The query returned no rows."]


def test_partial_first_and_last_periods_are_left_out_of_the_trend():
    rows = [["2016-09-01", 4], ["2016-10-01", 324], ["2016-11-01", 500], ["2017-11-01", 7544],
            ["2018-08-01", 6512], ["2018-09-01", 16], ["2018-10-01", 4]]
    f = run(["month", "orders"], rows)
    # average over the 4 complete months (324, 500, 7544, 6512) = 3,720
    assert f[0] == "Orders peaked in November 2017 at 7,544, about 103% above the average of 3,720."
    assert f[1] == "From October 2016 to August 2018 it rose from 324 to 6,512."
    assert f[-1] == ("The last periods, September 2018 to October 2018, are far below the rest, "
                     "so the data there may be incomplete.")
