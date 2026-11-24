"""Result comparison used for execution accuracy (app/evaluation/compare.py)."""

from app.evaluation.compare import compare, norm


def test_norm():
    assert norm(1) == norm(1.0) == norm("1.00") == 1.0
    assert norm(4.0849) == 4.08 and norm(-0.001) == 0.0
    assert norm(" SP ") == "SP" and norm(None) is None and norm(True) == 1


def test_identical():
    assert compare(["n"], [[3]], ["n"], [[3]]).strict


def test_names_and_order_ignored():
    gold = [["SP", 41746], ["RJ", 12852]]
    pred = [[12852, "RJ"], [41746, "SP"]]
    assert compare(["state", "n"], gold, ["orders", "customer_state"], pred).strict


def test_extra_label_column_allowed():
    assert compare(["n"], [[59]], ["country", "n"], [["all", 59]]).strict


def test_rounding_tolerance():
    # the app rounds averages to 2 decimals; the gold query may not
    assert compare(["avg"], [[4.08671]], ["avg_score"], [[4.09]]).strict
    assert compare(["n"], [[99441]], ["n"], [[99441.0]]).strict
    assert not compare(["avg"], [[4.08671]], ["avg_score"], [[4.1]]).strict


def test_wrong_value():
    m = compare(["n"], [[59]], ["n"], [[58]])
    assert not m.strict and not m.lenient and "no column" in m.reason


def test_wrong_row_count():
    m = compare(["g", "n"], [["Rock", 1297]], ["g", "n"], [["Rock", 1297], ["Latin", 579], ["Metal", 374]])
    assert not m.strict and m.lenient and "first row" in m.reason


def test_ranked_list_not_lenient_when_first_row_differs():
    m = compare(["g", "n"], [["Rock", 1297]], ["g", "n"], [["Latin", 579], ["Rock", 1297]])
    assert not m.strict and not m.lenient


def test_rows_must_pair_up():
    # same values per column, but the pairs are swapped: a different answer
    gold = [["SP", 10], ["RJ", 5]]
    pred = [["SP", 5], ["RJ", 10]]
    m = compare(["s", "n"], gold, ["s", "n"], pred)
    assert not m.strict and "paired differently" in m.reason


def test_duplicate_values_handled():
    gold = [["a", 1], ["b", 1], ["c", 2]]
    pred = [[1, "b"], [2, "c"], [1, "a"]]
    assert compare(["k", "v"], gold, ["v", "k"], pred).strict


def test_both_empty():
    assert compare(["n"], [], ["x", "y"], []).strict
