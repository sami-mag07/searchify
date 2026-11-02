from __future__ import annotations

import json

GTIN_KEYS = ["gtin", "gtin8", "gtin12", "gtin13", "gtin14"]


def extract_product(html: str) -> dict | None:
    # meistens gibts eh kein json-ld also garnicht erst parsen
    if not html or "application/ld+json" not in html:
        return None

    try:
        from lxml import html as lxml_html  # noqa

        tree = lxml_html.fromstring(html)
    except Exception:  # noqa
        return None

    return product_from_items(jsonld_items(tree))


def product_from_items(items) -> dict | None:
    """Erstes Product aus schon geparsten JSON-LD-Objekten (auch @graph darin)."""
    for item in items:
        if not isinstance(item, dict):
            continue
        graph = item.get("@graph")
        candidates = [g for g in graph if isinstance(g, dict)] if isinstance(graph, list) else []
        for cand in candidates + [item]:
            types = cand.get("@type")
            if not isinstance(types, list):
                types = [types]
            if "Product" in types:
                return read_product(cand) or None
    return None


def jsonld_items(tree):
    for script in tree.xpath('//script[@type="application/ld+json"]'):
        raw = (script.text or "").strip()
        raw = raw.replace("<![CDATA[", "").replace("]]>", "")
        try:
            data = json.loads(raw)
        except ValueError:
            continue  # kaputer block einfach weiter

        if not isinstance(data, list):
            data = [data]
        for item in data:
            if not isinstance(item, dict):
                continue
            # manche shosp packen alles in nen @graph
            graph = item.get("@graph")
            if isinstance(graph, list):
                for g in graph:
                    if isinstance(g, dict):
                        yield g
            yield item


def read_product(item: dict) -> dict:
    product = {}

    name = item.get("name")
    if isinstance(name, str) and name.strip():
        product["name"] = " ".join(name.split())

    product.update(read_offer(item.get("offers")))

    brand = item.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    if isinstance(brand, str) and brand.strip():
        product["brand"] = brand.strip()

    if item.get("sku") is not None:
        product["sku"] = str(item["sku"])

    gtins = []
    for key in GTIN_KEYS:
        value = item.get(key)
        if value is not None and str(value) not in gtins:
            gtins.append(str(value))
    if gtins:
        product["gtin"] = gtins

    return product


def read_offer(offers) -> dict:
    if not isinstance(offers, list):
        offers = [offers]

    for offer in offers:
        if not isinstance(offer, dict):
            continue

        # AggregateOffer hat nur lowPrice
        price = offer.get("price", offer.get("lowPrice"))
        if price is None or isinstance(price, bool):
            continue
        price = str(price).strip().replace(",", ".")
        if not price:
            continue

        result = {"price": price}
        if offer.get("priceCurrency"):
            result["priceCurrency"] = str(offer["priceCurrency"])
        if offer.get("availability"):
            # https://schema.org/InStock -> InStock
            result["availability"] = str(offer["availability"]).split("/")[-1]
        return result

    return {}
