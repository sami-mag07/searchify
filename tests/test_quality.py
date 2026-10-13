from app import quality


def test_answer_farm_slug_is_flagged():
    url = "https://berginstinct.de/wie-hoch-ist-die-zugspitze/"
    f = quality.features(url, "Zugspitze Höhe - Wie hoch ist die Zugspitze?", "Wie hoch ist die Zugspitze?")
    assert f["slug_question"] == 1 and f["root_slug"] == 1
    assert quality.seo_score(url) >= 2.0


def test_reference_pages_are_neutral():
    from app import rerank
    # Wikipedia traegt eine Sprach-Subdomain, ist aber ueber den festen Quellen-Prior
    # von allen Qualitaets-Abzuegen ausgenommen (rerank, demote_farms, serper).
    assert rerank.source_prior("https://de.wikipedia.org/wiki/Zugspitze", "de") > 0
    for url in (
        "https://www.gesetze-im-internet.de/ozg/BJNR313800017.html",
        "https://developer.mozilla.org/en-US/docs/Glossary/Port",
        "https://www.uni-heidelberg.de/de/universitaet/geschichte",
    ):
        assert quality.seo_score(url, "", "") == 0.0, url


def test_seo_path_digit_domain_and_clickbait():
    assert quality.features("https://bundesland24.de/fluesse/der-rhein")["digit_domain"] == 1
    assert quality.features("https://elementor.com/blog/https-port-guide")["seo_path"] == 1
    t = "Boiling Point of Water in Celsius: 100°C (Exact Guide)"
    assert quality.features("https://example.org/x", t)["title_clickbait"] == 1


def test_penalty_is_capped_and_off_by_default():
    url = "https://wissen-24.org/blog/wie-heisst-die-hauptstadt-von-australien/"
    assert quality.seo_penalty(url, "", "Wie heisst die Hauptstadt?", 0.0) == 0.0
    assert quality.seo_penalty(url, "", "Wie heisst die Hauptstadt?", 0.5) == -1.0
    assert quality.seo_penalty(url, "", "Hackathon Berlin 2026", 0.5) == 0.0


def _fake_scores(monkeypatch):
    from app import rerank
    monkeypatch.setattr(rerank, "_cross_scores", lambda q, texts, max_length, batch=32: [5.0] * len(texts))


FARM = "https://wissen-24.org/blog/wie-heisst-die-hauptstadt-von-australien/"
REF = "https://www.australia-facts.example/canberra"


def test_rerank_quality_prior_switch(monkeypatch):
    from app import rerank
    _fake_scores(monkeypatch)
    monkeypatch.setenv("SOURCE_PRIOR", "0")
    chunks = [{"url": FARM, "title": "", "content": "a"}, {"url": REF, "title": "", "content": "b"}]
    q = "Wie heisst die Hauptstadt von Australien?"
    monkeypatch.setenv("QUALITY_PRIOR", "0")
    off = {c["url"]: c["raw_score"] for c in rerank.rerank(q, chunks)}
    assert off[FARM] == off[REF] == 5.0
    monkeypatch.setenv("QUALITY_PRIOR", "1")
    on = rerank.rerank(q, chunks)
    assert on[0]["url"] == REF and on[1]["raw_score"] == 3.0
    with rerank.quality_disabled():  # Exa-Kompat
        assert {c["url"]: c["raw_score"] for c in rerank.rerank(q, chunks)} == off


def test_serper_quality_adjust_switch(monkeypatch):
    from app.compat import serper
    results = [{"url": FARM, "title": "x"}, {"url": "https://de.wikipedia.org/wiki/Canberra", "title": "y"}]
    q = "Wie heisst die Hauptstadt von Australien?"
    monkeypatch.setenv("QUALITY_PRIOR", "0")
    assert serper._quality_adjust(q, results, [5.0, 5.0]) == [5.0, 5.0]
    monkeypatch.setenv("QUALITY_PRIOR", "true")
    # Standard: nur /search, Serper bleibt unveraendert
    assert serper._quality_adjust(q, results, [5.0, 5.0]) == [5.0, 5.0]
    monkeypatch.setenv("QUALITY_PRIOR_COMPAT", "1")
    assert serper._quality_adjust(q, results, [5.0, 5.0]) == [3.0, 5.0]
    assert serper._quality_adjust("Hackathon Berlin 2026", results, [5.0, 5.0]) == [5.0, 5.0]


def test_new_features_lang_subdomain_symbol_tld():
    f = quality.features("https://de.emitain.com/australiens-hauptstadt/", "", "")
    assert f["lang_subdomain"] == 1
    assert quality.features("https://example.com/de/x")["lang_subdomain"] == 0
    assert quality.features("https://x.org/a", "≡ Liste: Alle 9 Bundesländer")["title_symbol"] == 1
    assert quality.features("https://x.org/a", "Studium » Bewerbung")["title_symbol"] == 0
    assert quality.features("https://chatai.guide/models/context-window-comparison/")["new_tld"] == 1


def _r(url, title=""):
    return {"url": url, "title": title}


def test_demote_farms_keeps_only_first_place():
    q = "Wie lang ist der Rhein?"
    res = [_r("https://farm1.example/a"), _r("https://farm2.example/b"), _r("https://ref.example/c"),
           _r("https://ok.example/d"), _r("https://offtopic.example/e")]
    flagged = lambda r: "farm" in r["url"]  # noqa: E731
    ce = {"https://ref.example/c": 5.0, "https://ok.example/d": 2.0, "https://offtopic.example/e": -5.0}
    out = quality.demote_farms(res, q, flagged, lambda r: ce.get(r["url"], 8.0))
    # Farm auf Platz 1 bleibt, die zweite rutscht hinter die passenden Treffer,
    # aber nicht hinter den themenfremden (Logit < 0)
    assert [r["url"] for r in out] == [
        "https://farm1.example/a", "https://ref.example/c", "https://ok.example/d",
        "https://farm2.example/b", "https://offtopic.example/e",
    ]
    # Stichwort-Suche: unveraendert
    assert quality.demote_farms(res, "Rhein Laenge", flagged, lambda r: 0.0) == res


def test_merge_by_source_demotes_second_farm(monkeypatch):
    from app import rerank
    q = "Wie heisst die Hauptstadt von Australien?"
    farm2 = "https://wissen-24.org/blog/wie-heisst-die-hauptstadt/"
    ranked = [
        {"url": FARM, "title": "", "content": "a", "score": 0.99, "raw_score": 9.0},
        {"url": farm2, "title": "", "content": "b", "score": 0.98, "raw_score": 8.5},
        {"url": REF, "title": "", "content": "c", "score": 0.9, "raw_score": 6.0},
    ]
    monkeypatch.setenv("RESULTS_PER_DOMAIN", "0")
    monkeypatch.setenv("QUALITY_PRIOR", "0")
    off = rerank.merge_by_source(ranked, 1, 3, query=q)
    assert [r["url"] for r in off] == [FARM, farm2, REF]
    monkeypatch.setenv("QUALITY_PRIOR", "1")
    on = rerank.merge_by_source(ranked, 1, 2, query=q)
    assert [r["url"] for r in on] == [FARM, REF]
