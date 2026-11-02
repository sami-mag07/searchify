"""Reason codes, thin pages, status output and the worker-crash pool reset (no network)."""
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

from searchify.extract import htmltext, pages

HTML = "<html><body><article><p>" + " ".join(["wort"] * 80) + "</p></article></body></html>"


class _DeadPool:
    """A pool whose worker died: every job comes back as BrokenProcessPool."""

    def submit(self, fn, *args, **kwargs):
        fut = Future()
        fut.set_exception(BrokenProcessPool("worker died"))
        return fut


def test_read_one_replaces_a_broken_extract_pool(monkeypatch):
    dead = _DeadPool()
    monkeypatch.setattr(htmltext, "_EXTRACT_POOL", dead)
    monkeypatch.setattr(htmltext, "fetch_url", lambda url, timeout, max_bytes=None: HTML)
    monkeypatch.setattr(pages, "_docs", lambda: htmltext.PageCache(10, 60))
    doc, err = pages._read_one("https://a.example/", "text", 5.0)
    assert err is None and doc["content"]  # the page is still read, in a thread
    assert htmltext._EXTRACT_POOL is not dead  # the next call starts a fresh pool
