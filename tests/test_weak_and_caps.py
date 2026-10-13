from app import rerank
from app.compat import serper
from app.extract import _plain_url


def test_drop_weak_keeps_strong_and_minimum():
    rs = [{"url": f"u{i}", "score": s} for i, s in enumerate([0.9, 0.5, 0.3, 0.1, 0.05])]
    assert [r["url"] for r in rerank.drop_weak(rs, 0.12, 3)] == ["u0", "u1", "u2"]
    weak = [{"url": f"w{i}", "score": 0.05} for i in range(5)]
    assert len(rerank.drop_weak(weak, 0.12, 3)) == 3  # nie unter keep
    assert rerank.drop_weak(rs, 0.0, 3) == rs  # aus


def test_serper_max_pages_covers_refill_and_window():
    assert serper._max_pages({"page": 1, "num": 10}) == 1 + 1 + serper.REFILL_PAGES
    assert serper._max_pages({"page": 1, "num": 100}) == serper.MAX_UPSTREAM_PAGES
    # Rerank-Fenster: page 3 mit num 10 sammelt ab Platz 1
    assert serper._max_pages({"page": 3, "num": 10}) == 3 + 1 + serper.REFILL_PAGES


def test_plain_url_rejects_parser_differentials():
    assert _plain_url("https://example.com/a?b=c")
    for bad in ("http://evil.com\\@127.0.0.1/", "http://user@127.0.0.1/", "http://a b.com/",
                "ftp://x.com/", "http://x.com:99999/", "http://x.com/\r\nHost: y"):
        assert not _plain_url(bad), bad
