"""passages(): search, read, split, rank, with the network and the model replaced."""
import sys

import pytest

from searchify import passages
from searchify.search import SearchBackendError

core = sys.modules["searchify.passages"]
rerank_mod = sys.modules["searchify.rerank"]

LONG = "word " * 12
HITS = [
    {"title": "A", "url": "https://a.com/1", "content": "snippet a", "engine": "x"},
    {"title": "B", "url": "https://b.com/1", "content": "snippet b", "engine": "x"},
]
PAGES = [
    {"url": "https://a.com/1", "title": "A",
     "content": f"short line\nalpha {LONG}\nbeta {LONG}\nalpha {LONG}"},
    {"url": "https://b.com/1", "title": "B", "content": f"gamma {LONG}"},
]


class FakeModel:
    """Scores a paragraph by how often the query's first word appears in it."""

    def __init__(self):
        self.kwargs = {}

    def predict(self, pairs, **kwargs):
        self.kwargs = kwargs
        return [text.count(query.split()[0]) for query, text in pairs]


@pytest.fixture
def web(monkeypatch):
    calls = {}

    def fake_search(query, max_results, backend=None, **filters):
        calls["search"] = (query, max_results, backend, filters)
        return HITS

    def fake_extract(urls):
        calls["extract"] = urls
        return PAGES

    monkeypatch.setattr(core, "web_search", fake_search)
    monkeypatch.setattr(core, "extract_urls", fake_extract)
    monkeypatch.setattr(rerank_mod, "_model", FakeModel())
    monkeypatch.setattr(core, "rerank_available", lambda: True)
    return calls


def test_without_rerank_paragraphs_keep_page_order_without_short_lines_and_repeats(web):
    out = passages("q", rerank=False)
    assert [p["content"].split()[0] for p in out] == ["alpha", "beta", "gamma"]
    assert all("rerank_score" not in p for p in out)
    assert out[0] == {"title": "A", "url": "https://a.com/1", "content": f"alpha {LONG}".strip(),
                      "offset": len("short line\n")}


def test_rerank_orders_best_first_and_adds_a_score(web):
    out = passages("gamma please", rerank=True)
    assert out[0]["content"].startswith("gamma") and out[0]["rerank_score"] == 1


def test_top_k_and_per_site_limit_the_result(web):
    assert len(passages("q", rerank=False, top_k=2)) == 2
    assert [p["url"] for p in passages("q", rerank=False, per_site=1)] == [
        "https://a.com/1", "https://b.com/1"]


def test_long_paragraphs_are_cut_at_a_word_boundary(web):
    out = passages("q", rerank=False, max_chars=22)  # the cut falls inside the 5th word
    assert out[0]["content"] == "alpha word word word …"


def test_long_pages_are_capped_before_ranking(web, monkeypatch):
    monkeypatch.setattr(core, "MAX_PER_PAGE", 1)
    assert [p["content"].split()[0] for p in passages("q", rerank=False)] == ["alpha", "gamma"]


def test_www_and_bare_domain_count_as_one_site(web, monkeypatch):
    pages = [{"url": "https://www.a.com/1", "title": "A", "content": f"alpha {LONG}"},
             {"url": "https://a.com/2", "title": "A", "content": f"beta {LONG}"}]
    monkeypatch.setattr(core, "extract_urls", lambda urls: pages)
    assert len(passages("q", rerank=False, per_site=1)) == 1


def test_search_options_are_passed_through(web):
    passages("q", 3, rerank=False, backend="searxng", language="de")
    assert web["search"] == ("q", 3, "searxng", {"language": "de"})
    assert web["extract"] == ["https://a.com/1", "https://b.com/1"]


def test_unreadable_pages_fall_back_to_search_snippets(web, monkeypatch):
    monkeypatch.setattr(core, "extract_urls", lambda urls: [])
    out = passages("q")
    assert [p["content"] for p in out] == ["snippet a", "snippet b"]


def test_no_hits_means_no_passages(web, monkeypatch):
    monkeypatch.setattr(core, "web_search", lambda *a, **k: [])
    assert passages("q") == []


def test_search_failure_is_raised(web, monkeypatch):
    def boom(*a, **k):
        raise SearchBackendError("down")
    monkeypatch.setattr(core, "web_search", boom)
    with pytest.raises(SearchBackendError):
        passages("q", rerank=False)


def test_rerank_default_follows_the_installed_extra(web, monkeypatch):
    monkeypatch.setattr(core, "rerank_available", lambda: False)
    assert all("rerank_score" not in p for p in passages("q"))
    monkeypatch.setattr(core, "rerank_available", lambda: True)
    assert all("rerank_score" in p for p in passages("q"))


def test_ranking_draws_no_progress_bar_on_stderr(web):
    passages("alpha")
    assert rerank_mod._model.kwargs.get("show_progress_bar") is False


def test_missing_rerank_extra_warns_once_per_process(web, monkeypatch, caplog):
    monkeypatch.setattr(core, "rerank_available", lambda: False)
    monkeypatch.setattr(core, "_warned_no_rerank", False)
    with caplog.at_level("WARNING", logger="searchify"):
        passages("q")
        passages("q")
        passages("q", rerank=False)  # asked for page order: nothing to warn about
    warnings = [r for r in caplog.records if r.name == "searchify"]
    assert len(warnings) == 1
    assert "searchify[rerank]" in warnings[0].getMessage()
    assert "page order" in warnings[0].getMessage()


def test_forced_rerank_without_the_extra_fails_before_searching(web, monkeypatch):
    monkeypatch.setattr(core, "rerank_available", lambda: False)
    with pytest.raises(ImportError, match="searchify\\[rerank\\]"):
        passages("q", rerank=True)
    assert "search" not in web
# Stand: 02.11.2026


# ------------------------------------------------------------ link-heavy lines and offsets
NAV = "Laptops Gaming Zubehör Angebote Smartphones Fernseher Haushalt Kundenservice Mein Konto Warenkorb Hilfe"
ARTICLE_LINE = ("The DGX Spark has 128 GB of unified memory, shared by the CPU and the GPU, "
                "which is enough for models up to 200 billion parameters.")
LINKED_PAGE = {
    "url": "https://shop.example/p", "title": "P",
    "content": f"{NAV}\n\n  {ARTICLE_LINE}\n{ARTICLE_LINE} More.",
    "links": [{"text": t, "url": "https://shop.example/c"} for t in NAV.replace("Mein Konto", "Mein_Konto").split()]
    + [{"text": "Mein Konto", "url": "https://shop.example/k"}, {"text": "unified memory", "url": "https://x"}],
}


def test_link_share_counts_whole_words_in_link_texts_ignoring_case():
    index = core.anchor_index(["Mein Konto", "konto", "GPU"])
    assert core.link_share("mein konto mein konto", index) == 1.0
    assert core.link_share("the gpu and the cpu", index) == 0.2
    assert core.link_share("gpus are not a gpu", index) == 0.2  # "gpus" is another word
    assert core.link_share("anything", {}) == 0.0


def test_lines_that_are_mostly_link_text_are_dropped_before_ranking():
    kept = core.split_paragraphs([LINKED_PAGE], 5)
    assert [p["content"] for p in kept] == [ARTICLE_LINE, f"{ARTICLE_LINE} More."]
    everything = core.split_paragraphs([LINKED_PAGE], 5, max_link_share=1.0)
    assert everything[0]["content"] == NAV


def test_each_passage_knows_where_it_starts_in_the_page_text():
    for p in core.split_paragraphs([LINKED_PAGE], 5, max_link_share=1.0):
        assert LINKED_PAGE["content"][p["offset"]:].startswith(p["content"])


def test_max_link_share_outside_zero_to_one_is_refused(web):
    with pytest.raises(ValueError):
        passages("q", rerank=False, max_link_share=1.5)


def test_search_snippets_have_no_offset(web, monkeypatch):
    monkeypatch.setattr(core, "extract_urls", lambda urls: [])
    assert all("offset" not in p for p in passages("q", rerank=False))


def test_table_rows_stay_even_when_their_cells_are_links():
    row = "| ICE 11 | Berlin Gesundbrunnen – Berlin Hbf – Leipzig – Erfurt – Fulda – Frankfurt |"
    page = {"url": "https://w.example/", "title": "W", "content": row,
            "links": [{"text": t} for t in ["ICE 11", "Berlin Gesundbrunnen", "Berlin Hbf", "Leipzig",
                                             "Erfurt", "Fulda", "Frankfurt"]]}
    assert core.link_share(row, core.anchor_index(link["text"] for link in page["links"])) > 0.9
    assert [p["content"] for p in core.split_paragraphs([page], 5)] == [row]
