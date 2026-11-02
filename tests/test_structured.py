"""Product data from JSON-LD, in stages. Offline, no network.

Run one stage plus all earlier ones with lernen/pruefen.sh <stage>.
"""
import json

import pytest

from searchify.extract.structured import extract_product


def _page(*blocks) -> str:
    """HTML page with one <script type="application/ld+json"> per block.
    Strings go in verbatim (for broken JSON), everything else via json.dumps."""
    scripts = "".join(
        '<script type="application/ld+json">'
        + (b if isinstance(b, str) else json.dumps(b))
        + "</script>"
        for b in blocks
    )
    return f"<html><head>{scripts}</head><body><p>Shop</p></body></html>"


SHOE = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Laufschuh X",
    "offers": {"@type": "Offer", "price": 89.95, "priceCurrency": "EUR"},
}

ARTICLE = {"@context": "https://schema.org", "@type": "NewsArticle", "headline": "Nachrichten"}


# ---------------------------------------------------------------- Stage 1: first product


def test_stage1_simple_product():
    assert extract_product(_page(SHOE)) == {
        "name": "Laufschuh X",
        "price": "89.95",
        "priceCurrency": "EUR",
    }


# ---------------------------------------------------------------- Stage 2: never crash


def test_stage2_no_jsonld():
    assert extract_product("<html><body><p>Kein JSON-LD</p></body></html>") is None


def test_stage2_article_is_not_a_product():
    assert extract_product(_page(ARTICLE)) is None


def test_stage2_broken_json():
    assert extract_product(_page('{"@type": "Product", "name": ')) is None


def test_stage2_broken_block_does_not_hide_next_one():
    assert extract_product(_page("{kaputt", SHOE))["name"] == "Laufschuh X"


def test_stage2_empty_html():
    assert extract_product("") is None


# ---------------------------------------------------------------- Stage 3: JSON-LD shapes


def test_stage3_top_level_list():
    org = {"@type": "Organization", "name": "Shop GmbH"}
    assert extract_product(_page([org, SHOE]))["name"] == "Laufschuh X"


def test_stage3_graph():
    page = _page({"@context": "https://schema.org", "@graph": [ARTICLE, SHOE]})
    assert extract_product(page)["name"] == "Laufschuh X"


def test_stage3_type_as_list():
    shoe = dict(SHOE, **{"@type": ["Product", "Thing"]})
    assert extract_product(_page(shoe))["name"] == "Laufschuh X"


def test_stage3_whitespace_in_name():
    shoe = dict(SHOE, name="  Laufschuh\n   X ")
    assert extract_product(_page(shoe))["name"] == "Laufschuh X"


def test_stage3_cdata():
    page = _page("<![CDATA[" + json.dumps(SHOE) + "]]>")
    assert extract_product(page)["name"] == "Laufschuh X"


# ---------------------------------------------------------------- Stage 4: offers and price


def test_stage4_offers_as_list_takes_first_with_price():
    shoe = dict(SHOE, offers=[{"@type": "Offer"}, {"@type": "Offer", "price": 79, "priceCurrency": "EUR"}])
    product = extract_product(_page(shoe))
    assert (product["price"], product["priceCurrency"]) == ("79", "EUR")


def test_stage4_aggregate_offer_uses_low_price():
    shoe = dict(SHOE, offers={"@type": "AggregateOffer", "lowPrice": "59.00", "highPrice": "99.00",
                              "priceCurrency": "EUR"})
    assert extract_product(_page(shoe))["price"] == "59.00"


def test_stage4_price_as_string():
    shoe = dict(SHOE, offers={"@type": "Offer", "price": "89.95", "priceCurrency": "EUR"})
    assert extract_product(_page(shoe))["price"] == "89.95"


def test_stage4_price_with_comma():
    shoe = dict(SHOE, offers={"@type": "Offer", "price": "89,95", "priceCurrency": "EUR"})
    assert extract_product(_page(shoe))["price"] == "89.95"


def test_stage4_no_offers_keeps_name_only():
    shoe = {"@type": "Product", "name": "Laufschuh X"}
    assert extract_product(_page(shoe)) == {"name": "Laufschuh X"}


# ---------------------------------------------------------------- Stage 5: details


@pytest.mark.parametrize("raw, expected", [
    ("https://schema.org/InStock", "InStock"),
    ("http://schema.org/OutOfStock", "OutOfStock"),
    ("InStock", "InStock"),
])
def test_stage5_availability_short_name(raw, expected):
    shoe = dict(SHOE, offers=dict(SHOE["offers"], availability=raw))
    assert extract_product(_page(shoe))["availability"] == expected


@pytest.mark.parametrize("brand", [{"@type": "Brand", "name": "Sprint"}, "Sprint"])
def test_stage5_brand_dict_or_string(brand):
    assert extract_product(_page(dict(SHOE, brand=brand)))["brand"] == "Sprint"


def test_stage5_sku_as_string():
    assert extract_product(_page(dict(SHOE, sku=12345)))["sku"] == "12345"


def test_stage5_gtins_collected_without_duplicates():
    shoe = dict(SHOE, gtin13="4006381333931", gtin=4006381333931, gtin8="96385074")
    assert extract_product(_page(shoe))["gtin"] == ["4006381333931", "96385074"]


# ---------------------------------------------------------------- Stage 6: into the reader


def test_stage6_reader_document_has_product():
    from searchify.extract.reader import reader_document

    doc = reader_document(_page(SHOE), "https://shop.example/schuh")
    assert doc["product"]["price"] == "89.95"


def test_stage6_article_page_has_no_product_field():
    from searchify.extract.reader import reader_document

    doc = reader_document(_page(ARTICLE), "https://news.example/artikel")
    assert "product" not in doc


def test_stage6_reader_graph_and_aggregate_offer():
    from searchify.extract.reader import reader_document

    shoe = dict(SHOE, offers={"@type": "AggregateOffer", "lowPrice": "59.00", "priceCurrency": "EUR"},
                gtin13="4006381333931", sku="A1", brand="Sprint")
    doc = reader_document(_page({"@graph": [ARTICLE, shoe]}), "https://shop.example/schuh")
    assert doc["product"] == {"name": "Laufschuh X", "price": "59.00", "priceCurrency": "EUR",
                              "brand": "Sprint", "sku": "A1", "gtin": ["4006381333931"]}


def test_stage6_broken_jsonld_gives_no_product():
    from searchify.extract.reader import reader_document

    doc = reader_document(_page('{"@type": "Product", "name": '), "https://shop.example/x")
    assert "product" not in doc


def test_stage6_plain_page_has_no_structured_key():
    from searchify.extract.reader import reader_document

    doc = reader_document("<html><body><p>Just a paragraph of text here.</p></body></html>", "", "text")
    assert "structured" not in doc and "product" not in doc
