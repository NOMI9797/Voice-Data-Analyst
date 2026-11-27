"""Starter questions come from the profile and read naturally (app/profiler/suggest.py)."""

from app.profiler.models import ColumnProfile, Relationship, SourceProfile, TableProfile
from app.profiler.suggest import suggestions_for


def col(name, kind, categorical=False, distinct=None, values=(), pk=False, nulls=0.0):
    return ColumnProfile(name=name, type="x", kind=kind, categorical=categorical, distinct_count=distinct,
                         values=list(values), primary_key=pk, null_fraction=nulls)


def profile(tables, rels=()):
    return SourceProfile(source_id="s", source_name="Shop", engine="sqlite", dialect="sqlite", tables=tables,
                         relationships=list(rels), profiled_at="now", seconds=0.1)


def test_trend_breakdown_and_measure():
    orders = TableProfile(name="orders", row_count=1000, columns=[
        col("order_id", "id", pk=True), col("order_status", "text", True, 5, ["delivered", "shipped"]),
        col("purchased_at", "date")])
    payments = TableProfile(name="order_payments", row_count=1200, columns=[
        col("order_id", "id"), col("payment_installments", "number"), col("payment_value", "number"),
        col("payment_type", "text", True, 4, ["card", "boleto"])])
    q = suggestions_for(profile([orders, payments]))
    assert q == ["How many orders were there each month?", "How many orders are there per order status?",
                 "What is the total payment value per payment type?"]


def test_flags_and_join_columns_are_skipped_and_prices_are_averaged():
    products = TableProfile(name="Products", row_count=77, columns=[
        col("ProductID", "id", pk=True), col("SupplierID", "number"), col("UnitPrice", "number"),
        col("Discontinued", "text", True, 2, ["0", "1"]), col("Category", "text", True, 8, ["Tea", "Dairy"])])
    orders = TableProfile(name="Orders", row_count=800, columns=[
        col("OrderID", "id", pk=True), col("OrderDate", "date"), col("ShipCountry", "text", True, 21, ["USA", "France"])])
    rels = [Relationship(from_table="Products", from_column="SupplierID", to_table="Suppliers",
                         to_column="SupplierID", source="declared")]
    q = suggestions_for(profile([orders, products], rels))
    assert "How many orders were there each month?" in q
    assert "What is the average unit price per category?" in q
    assert not any("discontinued" in x or "supplier" in x for x in q)
