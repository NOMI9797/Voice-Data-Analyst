"""Domain word correction on a small fixed vocabulary (no database needed)."""

import pytest

from app.correction.corrector import correct, vocabulary_from_items

ITEMS = (
    [(c, "customers", "customer_city") for c in
     ["sao paulo", "rio de janeiro", "belo horizonte", "curitiba", "porto alegre", "campinas",
      "horizonte", "taquara", "porto"]
     + [f"town {i}" for i in range(30)]  # long tail of rarer cities
     + ["belo horizont"]]  # a rare misspelled variant that really exists in Olist
    + [(a, "Artist", "Name") for a in ["Led Zeppelin", "Metallica", "AC/DC", "Iron Maiden", "Pink Floyd"]]
    + [(g, "Genre", "Name") for g in ["Rock", "Jazz", "Metal", "Blues"]]
    + [(p, "Products", "ProductName") for p in ["Chai", "Chang", "Ikura", "Tofu"]]
    + [(c, "products", "product_category_name") for c in ["perfumaria", "beleza_saude", "informatica_acessorios"]]
)
VOCAB = vocabulary_from_items(ITEMS)


@pytest.mark.parametrize("heard,expected", [
    ("How many orders came from Sao Paulo and Cartiba?", "How many orders came from Sao Paulo and curitiba?"),
    ("how many orders came from sow paulo", "how many orders came from sao paulo"),
    ("Show revenue for below-horizontal", "Show revenue for belo horizonte"),
    ("orders from porto alegri", "orders from porto alegre"),
    ("orders from porto-allegor.", "orders from porto alegre."),
    ("top tracks by led zepelin and pink floid", "top tracks by Led Zeppelin and Pink Floyd"),
    ("how many tracks are by metalica", "how many tracks are by Metallica"),
    ("compare AC slash DC and Iron Maiden", "compare AC/DC and Iron Maiden"),
    ("sales in the perfumeria category", "sales in the perfumaria category"),
    ("sales of chai and shang", "sales of chai and Chang"),
])
def test_misheard_names_are_fixed(heard, expected):
    assert correct(heard, VOCAB).corrected == expected


@pytest.mark.parametrize("q", [
    "show me the top customers",
    "now break it down by month",
    "how many orders were delivered last month",
    "which genres sell best, rock or jazz",
    "which sellers dropped in sales this year",
    "what is the average order value",
    "same for last year",
])
def test_normal_questions_untouched(q):
    r = correct(q, VOCAB)
    assert r.corrected == q and not r.corrections


def test_entities_and_locations_reported():
    r = correct("sales of chai in curitiba", VOCAB)
    values = {e["value"]: e["locations"][0]["column"] for e in r.entities}
    assert values == {"Chai": "ProductName", "curitiba": "customer_city"}



@pytest.mark.parametrize("text", [
    "How many albums does Led Zeppelin have?",
    "how many songs do pink floid have",
])
def test_words_next_to_a_name_are_kept(text):
    r = correct(text, VOCAB)
    assert r.corrected.rstrip("?").split()[-1] == "have"
