"""Backend selection and filtering logic (no network)."""
from searchify.search import BACKENDS, search
from searchify.search._common import dedupe_and_filter, host_matches, site_scoped


def test_backends_registered():
    assert set(BACKENDS) == {"duckduckgo", "searxng", "serper", "tavily"}


def test_host_matches_subdomains():
    assert host_matches("https://news.arxiv.org/x", ["arxiv.org"])
    assert not host_matches("https://evil.com", ["arxiv.org"])


def test_dedupe_and_filter():
    hits = [
        {"url": "https://a.com/1"},
        {"url": "https://a.com/1"},   # duplicate
        {"url": "https://b.com/2"},
        {"url": "https://spam.com/3"},
    ]
    out = dedupe_and_filter(hits, exclude_domains=["spam.com"], limit=10)
    assert [h["url"] for h in out] == ["https://a.com/1", "https://b.com/2"]


def test_ad_and_redirect_urls_are_dropped_without_refilling():
    hits = [
        {"url": "https://www.bing.com/aclick?ld=e8abc&u=aHR0cHM6Ly9zaG9w"},
        {"url": "https://www.raspberrypi.com/products/raspberry-pi-5/"},
        {"url": "https://bing.com/aclick?ld=x"},
        {"url": "https://duckduckgo.com/y.js?ad_domain=shop.example&u3=x"},
        {"url": "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fshop.example"},
        {"url": "https://www.googleadservices.com/pagead/aclk?sa=L&ai=x"},
        {"url": "https://ad.googleadservices.com/x"},
        {"url": "https://www.google.com/aclk?sa=l&ai=x"},
        {"url": "https://www.bing.com/search?q=raspberry"},      # not an ad: kept
        {"url": "https://duckduckgo.com/about"},                 # not an ad: kept
        {"url": "https://shop.example/aclick"},                  # aclick on another host: kept
        {"url": "https://www.reichelt.de/raspberry-pi-5"},
    ]
    out = dedupe_and_filter(hits, exclude_domains=[], limit=5)
    assert [h["url"] for h in out] == [
        "https://www.raspberrypi.com/products/raspberry-pi-5/",
        "https://www.bing.com/search?q=raspberry",
        "https://duckduckgo.com/about",
        "https://shop.example/aclick",
        "https://www.reichelt.de/raspberry-pi-5",
    ]
    only_ads = [h for h in hits[:8] if "raspberrypi" not in h["url"]]
    assert dedupe_and_filter(only_ads, exclude_domains=[], limit=5) == []  # fewer hits, no refill


def test_site_scoped():
    assert "site:arxiv.org" in site_scoped("q", ["arxiv.org"])
    assert site_scoped("q", None) == "q"


def test_unknown_backend_returns_empty():
    assert search("anything", backend="does-not-exist") == []
# Stand: 24.10.2026
