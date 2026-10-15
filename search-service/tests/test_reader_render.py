"""Reader: MediaWiki-Seiten ganz lesen, JS-Seiten als Reserve rendern."""

from app.compat import reader as reader_mod

_WIKI = """<html><head><title>Berlin - Wikipedia</title></head><body>
<nav id="mw-navigation"><a href="/">Hauptseite</a> Zufaelliger Artikel</nav>
<header class="mw-body-header"><h1 id="firstHeading">Berlin</h1></header>
<div class="mw-parser-output">
<p>Berlin ist die Hauptstadt der Bundesrepublik Deutschland und zugleich ein Land.</p>
<h2>Geschichte<span class="mw-editsection">[Quelltext bearbeiten]</span></h2>
<p>Die Stadt wurde im Mittelalter erstmals urkundlich erwaehnt.</p>
<div class="navbox">Bezirke Mitte Pankow Spandau Reinickendorf Lichtenberg</div>
<ol class="references"><li>Statistisches Landesamt Einwohnerzahlen Dezember</li></ol>
</div><footer id="footer">Datenschutz Impressum</footer></body></html>"""


def test_wiki_keeps_references_drops_chrome():
    text = reader_mod.reader_content(_WIKI, "https://de.wikipedia.org/wiki/Berlin")
    assert "# Berlin" in text and "## Geschichte" in text
    assert "Statistisches Landesamt" in text and "Spandau" in text
    assert "Quelltext bearbeiten" not in text
    assert "Zufaelliger" not in text and "Impressum" not in text


def test_render_fallback_used_only_for_failures(monkeypatch):
    page = "<html><body><main>" + "<p>" + " ".join(["Programm Anmeldung Hackathon"] * 30) + "</p></main></body></html>"
    calls = []
    monkeypatch.setattr(reader_mod.render_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader_mod.render_mod, "try_render",
                        lambda url, timeout=12.0, wait=None: ("tried", calls.append(url) or page))
    monkeypatch.setattr(reader_mod.pages_mod, "read_pages", lambda *a, **k: (
        [], [{"url": "https://spa.example/", "error": "Kein Haupttext gefunden"}]))
    docs, failed = reader_mod.read_pages(["https://spa.example/"], "markdown")
    assert calls == ["https://spa.example/"] and not failed
    assert "Hackathon" in docs[0]["content"]
    reader_mod.forget(["https://spa.example/"], "markdown")


def test_render_challenge_page_stays_failed(monkeypatch):
    page = "<html><body><p>Just a moment... verifying your browser before access</p></body></html>"
    monkeypatch.setattr(reader_mod.render_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader_mod.render_mod, "try_render",
                        lambda url, timeout=12.0, wait=None: ("tried", page))
    monkeypatch.setattr(reader_mod.pages_mod, "read_pages", lambda *a, **k: (
        [], [{"url": "https://blocked.example/", "error": "Seite nicht ladbar"}]))
    docs, failed = reader_mod.read_pages(["https://blocked.example/"], "markdown")
    assert not docs and failed[0]["url"] == "https://blocked.example/"


def test_reader_date_from_meta_without_text_search():
    """Reader: Datum nur aus Meta-Angaben (wie publishedTime des Originals); die
    Freitext-Datumssuche (dateparser) kostete auf Forenseiten mehrere Sekunden."""
    meta_html = ('<html><head><title>Post</title><meta property="article:published_time" '
                 'content="2024-03-05T10:00:00Z"></head><body><p>' + "Text " * 60 + "</p></body></html>")
    assert reader_mod.reader_document(meta_html, "https://example.org/a")["published"] == "2024-03-05"
    text_html = ("<html><head><title>Forum</title></head><body><p>Beitrag vom 5. März 2024, "
                 + "Text " * 60 + "</p></body></html>")
    assert reader_mod.reader_document(text_html, "https://example.org/b")["published"] is None


def _fails(*urls, error="Kein Haupttext gefunden"):
    return [{"url": u, "error": error} for u in urls]


def test_render_respects_caller_deadline(monkeypatch):
    """x-timeout: der Render bekommt nur die Restzeit, ohne Rest wird nicht gerendert."""
    seen = []
    monkeypatch.setattr(reader_mod.render_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader_mod.render_mod, "try_render",
                        lambda url, timeout=12.0, wait=None: (seen.append(timeout) or ("tried", None)))
    monkeypatch.setattr(reader_mod.pages_mod, "read_pages", lambda *a, **k: ([], _fails("https://a.example/")))
    reader_mod.read_pages(["https://a.example/"], "markdown", timeout=3.0)
    assert seen and seen[0] <= 3.0
    seen.clear()
    monkeypatch.setattr(reader_mod.time, "monotonic", lambda: 1000.0)
    _docs, failed = reader_mod._render_failed([], _fails("https://a.example/"), "markdown",
                                              ["https://a.example/"], deadline=1000.5)
    assert not seen and len(failed) == 1


def test_render_counts_only_real_attempts(monkeypatch):
    """Zeitlimit-Fehler verbrauchen keinen der RENDER_MAX Versuche; belegter Platz
    laesst die uebrigen URLs sofort aus (kein Warten je URL)."""
    calls = []
    monkeypatch.setattr(reader_mod.render_mod, "try_render",
                        lambda url, timeout=12.0, wait=None: (calls.append(url) or ("tried", None)))
    failed = (_fails("https://t1.example/", "https://t2.example/", error="Zeitlimit")
              + _fails("https://a.example/", "https://b.example/", "https://c.example/"))
    _docs, still = reader_mod._render_failed([], failed, "markdown", [f["url"] for f in failed])
    assert calls == ["https://a.example/", "https://b.example/"] and len(still) == 5
    calls.clear()
    monkeypatch.setattr(reader_mod.render_mod, "try_render",
                        lambda url, timeout=12.0, wait=None: (calls.append(url) or ("busy", None)))
    _docs, still = reader_mod._render_failed([], _fails("https://a.example/", "https://b.example/"),
                                             "markdown", [])
    assert calls == ["https://a.example/"] and len(still) == 2


def test_try_render_waits_briefly_for_busy_slot(monkeypatch):
    import time as _time
    r = reader_mod.render_mod
    monkeypatch.setattr(r, "enabled", lambda: True)
    monkeypatch.setattr(r, "url_allowed", lambda url: True)
    monkeypatch.setattr(r, "SEM_WAIT_S", 0.05)
    for _ in range(r.SLOTS):
        assert r._SEM.acquire(timeout=1)
    try:
        t0 = _time.monotonic()
        assert r.try_render("https://a.example/", timeout=10.0) == ("busy", None)
        assert _time.monotonic() - t0 < 1.0
    finally:
        for _ in range(r.SLOTS):
            r._SEM.release()
    assert r.try_render("https://a.example/", timeout=0) == ("off", None)


_GOOD = "<html><body><main><p>" + " ".join(["Programm Anmeldung Hackathon"] * 30) + "</p></main></body></html>"


def _stages(monkeypatch, stage2, stage3, calls):
    monkeypatch.setattr(reader_mod.render_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader_mod.render_mod, "try_render",
                        lambda url, timeout=12.0, wait=None: (calls.append(("2", url)) or ("tried", stage2)))
    monkeypatch.setattr(reader_mod.render_mod, "try_render_direct",
                        lambda url, timeout=15.0, wait=None: (calls.append(("3", url)) or ("tried", stage3)))


def test_fast_path_untouched(monkeypatch):
    """Gute statische Seiten loesen keine Stufe aus."""
    calls = []
    _stages(monkeypatch, _GOOD, _GOOD, calls)
    doc = reader_mod.reader_document(_GOOD, "https://ok.example/", "markdown")
    monkeypatch.setattr(reader_mod.pages_mod, "read_pages", lambda *a, **k: ([doc], []))
    docs, failed = reader_mod.read_pages(["https://ok.example/"], "markdown")
    assert calls == [] and docs == [doc] and not failed


def test_thin_static_page_escalates_and_keeps_text(monkeypatch):
    """"Skip to main content" zaehlt fuer die Stufen als Fehlschlag; scheitern alle,
    kommt der statische Text trotzdem zurueck."""
    calls = []
    _stages(monkeypatch, None, None, calls)
    thin = {"url": "https://www.reddit.example/r/x", "content": "Skip to main content"}
    monkeypatch.setattr(reader_mod.pages_mod, "read_pages", lambda *a, **k: ([dict(thin)], []))
    docs, failed = reader_mod.read_pages([thin["url"]], "markdown")
    assert calls == [("2", thin["url"]), ("3", thin["url"])]
    assert docs == [thin] and not failed


def test_stage3_only_when_stage2_fails(monkeypatch):
    calls = []
    _stages(monkeypatch, _GOOD, _GOOD, calls)
    monkeypatch.setattr(reader_mod.pages_mod, "read_pages", lambda *a, **k: ([], _fails("https://a.example/")))
    docs, _ = reader_mod.read_pages(["https://a.example/"], "markdown")
    assert calls == [("2", "https://a.example/")] and "Hackathon" in docs[0]["content"]
    reader_mod.forget(["https://a.example/"], "markdown")
    calls.clear()
    challenge = "<html><body><p>Just a moment... verifying your browser</p></body></html>"
    _stages(monkeypatch, challenge, _GOOD, calls)
    docs, failed = reader_mod.read_pages(["https://a.example/"], "markdown")
    assert [c[0] for c in calls] == ["2", "3"] and docs and not failed
    reader_mod.forget(["https://a.example/"], "markdown")


def test_github_takes_readme_not_whole_page():
    readme = " ".join(["Grafana visualisiert Metriken aus vielen Datenquellen"] * 12)
    html = ("<html><body><nav>" + " ".join(["Navigation Produkt Loesungen Preise"] * 60) + "</nav>"
            "<table><tr><td>" + " ".join(["dateiname commitnachricht"] * 80) + "</td></tr></table>"
            '<article class="markdown-body entry-content"><div class="markdown-heading">'
            '<h1 class="heading-element">Grafana</h1><a class="anchor" href="#g"><svg></svg></a></div>'
            f"<p>{readme}</p></article></body></html>")
    text = reader_mod.reader_content(html, "https://github.com/grafana/grafana")
    assert text.startswith("# Grafana") and "Datenquellen" in text
    assert "Navigation" not in text and "commitnachricht" not in text
