"""Shared offline pipeline for the ladder tests: pages, browser stages and Tavily are mocked."""
import json
from concurrent.futures import ThreadPoolExecutor

from searchify.extract import htmltext, pages, reader

ARTICLE = ("<html><head><title>Artikel</title></head><body><article><p>"
           + " ".join(f"wort{i}" for i in range(200)) + "</p></article></body></html>")
WALL = "<html><body><p>Just a moment... verifying you are human.</p></body></html>"
RICH = ("<html><head><title>Gerendert</title></head><body><article><p>"
        + " ".join(f"echt{i}" for i in range(120)) + "</p></article></body></html>")

PAGES = {
    "https://article.example/": ARTICLE,
    "https://wall.example/": WALL,
    "https://blocked.example/": None,
    "https://stuck.example/": WALL,
    "https://cloud.example/": None,
}


def install(monkeypatch, *, browser=True, stealth=False, tavily=False, calls=None):
    """Mock everything; `calls` collects what each rung was asked to do."""
    calls = calls if calls is not None else []
    cache = htmltext.PageCache(50, 60)
    monkeypatch.setattr(pages, "_docs", lambda: cache)
    monkeypatch.setattr(htmltext, "_extract_pool", lambda: ThreadPoolExecutor(1))
    monkeypatch.setattr(reader, "RENDER_MAX", 10)  # one request, many rescue cases
    monkeypatch.setattr(reader.render_mod, "enabled", lambda: browser)
    monkeypatch.setattr(reader.stealth_mod, "enabled", lambda: stealth)

    def fetch(url, timeout, max_bytes=None, plain=False):
        calls.append(("fetch", url, round(timeout, 3), plain))
        html = PAGES.get(url)
        if html is None:
            htmltext._note_failure("HTTP 403")
        return html
    monkeypatch.setattr(htmltext, "fetch_url", fetch)

    def render(url, timeout=0):
        calls.append(("render", url, round(timeout, 3)))
        return ("tried", RICH if "wall" in url else None)

    def direct(url, timeout=0):
        calls.append(("direct", url, round(timeout, 3)))
        return ("tried", None)

    def stealth_fn(url, timeout=0):
        calls.append(("stealth", url, round(timeout, 3)))
        return ("tried", RICH if "blocked" in url else None)
    monkeypatch.setattr(reader.render_mod, "try_render", render)
    monkeypatch.setattr(reader.render_mod, "try_render_direct", direct)
    monkeypatch.setattr(reader.stealth_mod, "try_stealth", stealth_fn)

    if tavily:
        monkeypatch.setenv("TAVILY_API_KEY", "test-key-not-real")

        def post(path, body, timeout):
            calls.append(("tavily", tuple(body["urls"]), round(timeout, 3)))
            return {"results": [{"url": u, "raw_content": "cloud text " * 30} for u in body["urls"]]}
        monkeypatch.setattr(reader, "tavily_post", post)
    else:
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    return calls


def snapshot(urls=None, **kw):
    """The full return value of read_pages_ex as plain JSON-able data."""
    docs, failed, thin = reader.read_pages_ex(urls or list(PAGES), "text", **kw)
    return json.loads(json.dumps({"docs": docs, "failed": failed, "thin": thin}, sort_keys=True))
