"""Offline-Tests fuer serp_clone (SearXNG gemockt, kein Netz).

    .venv/bin/python -m pytest experiments/serp_clone -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import serp_clone  # noqa: E402


def _fake_page(pageno: int) -> dict:
    results = [
        {"url": f"https://example.org/{pageno}/{i}", "title": f"T{pageno}.{i}", "content": "s"}
        for i in range(10)
    ]
    return {
        "results": results,
        "infoboxes": [{"infobox": "Berlin", "id": "https://de.wikipedia.org/wiki/Berlin", "content": "Hauptstadt"}],
        "answers": [],
        "suggestions": ["berlin wetter"],
    }


@pytest.fixture(autouse=True)
def fake_upstream(monkeypatch):
    calls = []

    def upstream(p, pageno):
        calls.append((p, pageno))
        return _fake_page(pageno)

    monkeypatch.setattr(serp_clone, "_upstream", upstream)
    serp_clone._cache.clear()
    return calls


def test_serper_shape():
    out = serp_clone.search({"q": "Berlin", "hl": "de", "gl": "de", "num": 3})
    assert out["searchParameters"]["q"] == "Berlin"
    assert [o["position"] for o in out["organic"]] == [1, 2, 3]
    assert set(out["organic"][0]) == {"title", "link", "snippet", "position"}
    assert out["knowledgeGraph"]["descriptionLink"].endswith("/Berlin")
    assert out["relatedSearches"] == [{"query": "berlin wetter"}]
    assert out["peopleAlsoAsk"] == [] and out["credits"] == 1


def test_paging_continues_positions_and_fetches_more(fake_upstream):
    out = serp_clone.search({"q": "x", "num": 20, "page": 2})
    assert [o["position"] for o in out["organic"]] == list(range(21, 41))
    assert [pageno for _, pageno in fake_upstream] == [3, 4]


def test_cache_hit_skips_upstream(fake_upstream):
    serp_clone.search({"q": "x"})
    serp_clone.search({"q": "x"})
    assert len(fake_upstream) == 1


@pytest.mark.parametrize("body", [{}, {"q": "x", "num": 0}, {"q": "x", "page": "a"}, {"q": "x", "tbs": "qdr:h"}])
def test_bad_requests(body):
    with pytest.raises(serp_clone.BadRequest):
        serp_clone.search(body)
