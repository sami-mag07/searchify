"""PDF reading: no network except a local fake server. Needs the extra `pdf`."""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from searchify.extract import htmltext as h
from searchify.extract import pages as pages_mod
from searchify.extract import pdf as pdf_mod
from searchify.extract import reader

pytest.importorskip("pypdfium2")


# ---------------------------------------------------------------- tiny PDF builder


def make_pdf(pages, title=None, encrypted=False):
    """pages: list of strings (empty string = page without text). Valid PDF 1.4."""
    objs = {}
    font = 3 + 2 * len(pages)  # 1 catalog, 2 pages, then page+content pairs, then font, info
    info = font + 1 if title else None
    kids = []
    for i, text in enumerate(pages):
        page_id, cont_id = 3 + 2 * i, 4 + 2 * i
        kids.append(f"{page_id} 0 R")
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines = safe.split("\n")
        stream = ("BT /F1 12 Tf 14 TL 40 760 Td " + " T* ".join(f"({ln}) Tj" for ln in lines) + " ET") if text else ""
        objs[page_id] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {cont_id} 0 R "
                         f"/Resources << /Font << /F1 {font} 0 R >> >> >>")
        objs[cont_id] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    objs[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>"
    objs[font] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    if title:
        objs[info] = f"<< /Title ({title}) >>"
    top = max(objs)
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{objs[num]}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {top + 1}\n0000000000 65535 f \n".encode()
    for num in range(1, top + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    trailer = f"/Size {top + 1} /Root 1 0 R"
    if title:
        trailer += f" /Info {info} 0 R"
    if encrypted:
        # Standard handler, RC4 40 bit, with a user password we do not give: the O and U
        # entries need not be genuine, pdfium refuses to open it without the password.
        trailer += (f" /Encrypt << /Filter /Standard /V 1 /R 2 /P -4 /O <{'ab' * 32}> /U <{'cd' * 32}> >>"
                    " /ID [<00112233445566778899aabbccddeeff> <00112233445566778899aabbccddeeff>]")
    out += f"trailer\n<< {trailer} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# ---------------------------------------------------------------- fake server

ROUTES = {}  # path -> (content_type or None, body bytes or generator factory)
HITS = []


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        HITS.append(self.path)
        ctype, body = ROUTES.get(self.path, ("text/html", b"<html><body>nope</body></html>"))
        try:
            self.send_response(200)
            if ctype:
                self.send_header("Content-Type", ctype)
            if isinstance(body, bytes):
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:  # endless-ish stream without Content-Length
                self.end_headers()
                for chunk in body():
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def site(monkeypatch):
    ROUTES.clear()
    HITS.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(h, "_public_ips", lambda host: ["127.0.0.1"])
    yield f"http://localhost:{server.server_address[1]}"
    server.shutdown()


def _fresh_cache(monkeypatch):
    monkeypatch.setattr(pages_mod, "_docs", lambda c=h.PageCache(50, 60): c)


# ---------------------------------------------------------------- slow / crashing children
# module level, so the spawned child can import them


def _sleepy_child(conn, data, max_pages, max_chars):
    time.sleep(60)


def _crashing_child(conn, data, max_pages, max_chars):
    os._exit(1)


def _chatty_child(conn, data, max_pages, max_chars):
    # far more than a pipe buffer holds: only works if the parent reads before it joins
    pdf_mod._answer(conn, "ok", {"title": "T", "page_count": 1, "content": "x" * 600_000, "truncated": False})
    conn.close()


def _huge_child(conn, data, max_pages, max_chars):
    # a child gone wrong: an answer far above anything the real worker sends
    try:
        pdf_mod._answer(conn, "ok", {"title": "T", "page_count": 1, "content": "x" * 3_000_000,
                                     "truncated": False})
    except OSError:
        pass  # the parent refused it and closed the pipe


UNPICKLED = []  # filled in the PARENT if it ever unpickles what a child sent


def _mark_unpickled():
    UNPICKLED.append(1)
    return ("ok", {"title": "T", "page_count": 1, "content": "taken over", "truncated": False})


class _Evil:
    def __reduce__(self):
        return (_mark_unpickled, ())


def _pickling_child(conn, data, max_pages, max_chars):
    # a child taken over by a crafted PDF: its answer runs code in whoever unpickles it
    conn.send(_Evil())
    conn.close()


def _env_probe_child(conn, data, max_pages, max_chars):
    # the real worker, with a spy on what pdfium's caller sees of the environment
    import pypdfium2  # noqa: PLC0415

    out = os.environ["SEARCHIFY_PROBE_FILE"]
    seen = []
    real = pypdfium2.PdfDocument

    def spy(*args, **kwargs):
        seen.append(sorted(k for k in os.environ if k.startswith("SEARCHIFY_PROBE")))
        return real(*args, **kwargs)

    pypdfium2.PdfDocument = spy
    pdf_mod._worker(conn, data, max_pages, max_chars)
    with open(out, "w") as f:
        json.dump(seen, f)


def _greedy_child(conn, data, max_pages, max_chars):
    # stands for pdfium inflating a crafted stream: grows by 16 MB every 20 ms (640 MB at most)
    hoard = []
    for _ in range(40):
        hoard.append(b"x" * (16 << 20))
        time.sleep(0.02)
    time.sleep(60)


# ---------------------------------------------------------------- tests


def test_reads_a_pdf_as_a_normal_document(site, monkeypatch):
    _fresh_cache(monkeypatch)
    ROUTES["/a.pdf"] = ("application/pdf", make_pdf(["Erste Seite Haupttext", "Zweite Seite mehr Text"], title="Mein Titel"))
    t = time.monotonic()
    docs, failed = reader.read_pages([site + "/a.pdf"], "text")
    assert failed == []
    d = docs[0]
    assert d["url"] == site + "/a.pdf" and d["title"] == "Mein Titel" and d["page_count"] == 2
    assert "[Seite 1]\nErste Seite Haupttext" in d["content"]
    assert "[Seite 2]\nZweite Seite mehr Text" in d["content"]
    assert d["links"] == [] and "truncated" not in d
    assert time.monotonic() - t < 15


def test_soft_hyphens_join_the_word(site, monkeypatch):
    _fresh_cache(monkeypatch)
    # pdfium hands back a hyphen it took for a soft one as U+FFFE
    ROUTES["/h.pdf"] = ("application/pdf", make_pdf(["Die Trans-\nformer Architektur ist gut"]))
    docs, failed = reader.read_pages([site + "/h.pdf"], "text")
    assert failed == []
    assert "Die Transformer Architektur" in docs[0]["content"] and "\ufffe" not in docs[0]["content"]


@pytest.mark.parametrize("raw, clean", [
    ("Trans\ufffe\nformer", "Transformer"),            # at the line end: joined over the break
    ("Trans\ufffe  \n  former ist", "Transformer ist"),
    ("Trans\ufffeformer", "Transformer"),               # inside a line: just removed
    ("ein\ufffe Wort\nund mehr", "ein Wort\nund mehr"),
    ("ohne Trennung\nZeile", "ohne Trennung\nZeile"),
])
def test_soft_hyphen_cleanup(raw, clean):
    assert pdf_mod.join_soft_hyphens(raw) == clean


def test_title_falls_back_to_the_file_name(site, monkeypatch):
    _fresh_cache(monkeypatch)
    ROUTES["/Jahres_bericht-2025.pdf"] = ("application/pdf", make_pdf(["Ein bisschen Text hier"]))
    docs, _ = pages_mod.read_pages([site + "/Jahres_bericht-2025.pdf"])
    assert docs[0]["title"] == "Jahres bericht 2025"


def test_detects_a_pdf_by_its_first_bytes(site, monkeypatch):
    _fresh_cache(monkeypatch)
    ROUTES["/dl"] = ("application/octet-stream", make_pdf(["Versteckt hinter falschem Typ"]))
    docs, failed = pages_mod.read_pages([site + "/dl"])
    assert failed == [] and "Versteckt" in docs[0]["content"]


def test_an_octet_stream_that_is_no_pdf_keeps_the_old_failure(site, monkeypatch):
    _fresh_cache(monkeypatch)
    ROUTES["/zip"] = ("application/octet-stream", b"PK\x03\x04" + b"0" * 100)
    docs, failed = pages_mod.read_pages([site + "/zip"])
    assert docs == [] and failed[0]["error"] == "not HTML: application/octet-stream"


def test_long_pdf_is_cut_at_50_pages_and_60000_chars(site, monkeypatch):
    _fresh_cache(monkeypatch)
    # 60 pages of ~1500 chars: 50 pages x 1500 = 75k > 60k, so the char cap bites too
    line = "wort " * 30
    page = "\n".join([line] * 10)
    ROUTES["/long.pdf"] = ("application/pdf", make_pdf([page] * 60))
    docs, failed = pages_mod.read_pages([site + "/long.pdf"])
    assert failed == []
    d = docs[0]
    assert d["page_count"] == 60 and d["truncated"] is True
    assert len(d["content"]) <= pdf_mod.MAX_CHARS
    assert "[Seite 51]" not in d["content"]


def test_fifty_pages_cap_without_char_cap(site, monkeypatch):
    _fresh_cache(monkeypatch)
    ROUTES["/p.pdf"] = ("application/pdf", make_pdf([f"Seite mit Inhalt {i}" for i in range(55)]))
    d = pages_mod.read_pages([site + "/p.pdf"])[0][0]
    assert d["page_count"] == 55 and "[Seite 50]" in d["content"] and "[Seite 51]" not in d["content"]
    assert d["truncated"] is True


def test_encrypted_pdf(site, monkeypatch):
    _fresh_cache(monkeypatch)
    ROUTES["/enc.pdf"] = ("application/pdf", make_pdf(["geheim"], encrypted=True))
    docs, failed = pages_mod.read_pages([site + "/enc.pdf"])
    assert docs == [] and failed[0]["error"] == "pdf_encrypted"


def test_pdf_without_a_text_layer(site, monkeypatch):
    _fresh_cache(monkeypatch)
    ROUTES["/scan.pdf"] = ("application/pdf", make_pdf(["", ""]))
    docs, failed = pages_mod.read_pages([site + "/scan.pdf"])
    assert docs == [] and failed[0]["error"] == "pdf_no_text"


def test_broken_pdf_is_a_clean_failure(site, monkeypatch):
    _fresh_cache(monkeypatch)
    ROUTES["/bad.pdf"] = ("application/pdf", b"%PDF-1.4\nthis is not a pdf at all")
    docs, failed = pages_mod.read_pages([site + "/bad.pdf"])
    assert docs == [] and failed[0]["error"] == "extract_failed"


def test_too_large_stream_is_refused_not_cut(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "MAX_BYTES", 300_000)
    sent = []

    def endless():
        yield b"%PDF-1.4\n"
        for _ in range(100):  # 6.4 MB if nobody stops us
            chunk = b"0" * 65536
            sent.append(len(chunk))
            yield chunk

    ROUTES["/big.pdf"] = ("application/pdf", endless)
    docs, failed = pages_mod.read_pages([site + "/big.pdf"])
    assert docs == [] and failed[0]["error"] == "pdf_too_large"
    assert sum(sent) < 2_000_000  # we stopped reading early


def test_too_large_by_declared_length(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "MAX_BYTES", 1000)
    ROUTES["/big2.pdf"] = ("application/pdf", b"%PDF-1.4\n" + b"0" * 5000)
    docs, failed = pages_mod.read_pages([site + "/big2.pdf"])
    assert failed[0]["error"] == "pdf_too_large"


def test_download_has_its_own_time_budget(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "DOWNLOAD_TIMEOUT_S", 0.6)

    def drip():
        yield b"%PDF-1.4\n"
        for _ in range(30):
            time.sleep(0.2)
            yield b"0" * 10

    ROUTES["/slow.pdf"] = ("application/pdf", drip)
    t = time.monotonic()
    docs, failed = pages_mod.read_pages([site + "/slow.pdf"], timeout=5)
    assert failed[0]["error"] == "timeout" and time.monotonic() - t < 4


def test_hanging_child_is_killed_after_the_poll_window(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "_TARGET", _sleepy_child)
    monkeypatch.setattr(pdf_mod, "EXTRACT_TIMEOUT_S", 1.5)
    ROUTES["/a.pdf"] = ("application/pdf", make_pdf(["Etwas Text auf der Seite"]))
    t = time.monotonic()
    docs, failed = pages_mod.read_pages([site + "/a.pdf"])
    assert failed[0]["error"] == "timeout"
    assert time.monotonic() - t < 12  # spawn start + 1.5 s poll + kill, nowhere near the 60 s sleep


def test_crashing_child_only_costs_that_pdf(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "_TARGET", _crashing_child)
    ROUTES["/a.pdf"] = ("application/pdf", make_pdf(["Etwas Text auf der Seite"]))
    docs, failed = pages_mod.read_pages([site + "/a.pdf"])
    assert failed[0]["error"] == "extract_failed"
    monkeypatch.undo()
    # the next PDF is fine
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(h, "_public_ips", lambda host: ["127.0.0.1"])
    docs, failed = pages_mod.read_pages([site + "/a.pdf"])
    assert failed == [] and docs[0]["page_count"] == 1


def test_result_larger_than_a_pipe_is_read_before_the_join(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "_TARGET", _chatty_child)
    monkeypatch.setattr(pdf_mod, "EXTRACT_TIMEOUT_S", 10)
    ROUTES["/a.pdf"] = ("application/pdf", make_pdf(["Etwas Text auf der Seite"]))
    docs, failed = pages_mod.read_pages([site + "/a.pdf"])
    assert failed == [] and len(docs[0]["content"]) == 600_000


def test_title_and_author_are_capped(site, monkeypatch):
    _fresh_cache(monkeypatch)
    # make_pdf writes << /Title (...) >>: the ") /Author (" smuggles in a second entry
    ROUTES["/meta.pdf"] = ("application/pdf", make_pdf(["Etwas Text auf der Seite"],
                                                       title="T" * 50_000 + ") /Author (" + "A" * 50_000))
    docs, failed = pages_mod.read_pages([site + "/meta.pdf"])
    assert failed == []
    assert docs[0]["title"] == "T" * 300
    assert docs[0]["author"] == "A" * 300


def test_cache_size_counts_title_author_and_description():
    doc = {"content": "x", "title": "T" * 1000, "author": "A" * 10, "description": "D" * 100,
           "published": "2026-11-03", "links": [{"text": "a", "url": "https://a.example/"}]}
    assert h.PageCache._size(doc) == 1 + 1000 + 10 + 100 + 120


def test_an_oversized_answer_from_the_child_is_refused(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "_TARGET", _huge_child)
    monkeypatch.setattr(pdf_mod, "EXTRACT_TIMEOUT_S", 10)
    ROUTES["/a.pdf"] = ("application/pdf", make_pdf(["Etwas Text auf der Seite"]))
    docs, failed = pages_mod.read_pages([site + "/a.pdf"])
    assert docs == [] and failed[0]["error"] == "extract_failed"


@pytest.mark.skipif(not (sys.platform == "darwin" or sys.platform.startswith("linux")),
                    reason="the RSS watch reads libproc or /proc")
def test_a_child_growing_past_the_memory_cap_is_killed(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "_TARGET", _greedy_child)
    monkeypatch.setattr(pdf_mod, "MAX_CHILD_RSS", 256 << 20, raising=False)
    monkeypatch.setattr(pdf_mod, "EXTRACT_TIMEOUT_S", 5)
    ROUTES["/a.pdf"] = ("application/pdf", make_pdf(["Etwas Text auf der Seite"]))
    t = time.monotonic()
    docs, failed = pages_mod.read_pages([site + "/a.pdf"], timeout=10, deadline_s=20)
    assert docs == [] and failed[0]["error"] == "pdf_too_large"
    assert time.monotonic() - t < 5  # killed while growing, not after the extract timeout


def test_the_parent_never_unpickles_the_childs_answer(site, monkeypatch):
    _fresh_cache(monkeypatch)
    UNPICKLED.clear()
    monkeypatch.setattr(pdf_mod, "_TARGET", _pickling_child)
    monkeypatch.setattr(pdf_mod, "EXTRACT_TIMEOUT_S", 10)
    ROUTES["/a.pdf"] = ("application/pdf", make_pdf(["Etwas Text auf der Seite"]))
    docs, failed = pages_mod.read_pages([site + "/a.pdf"])
    assert UNPICKLED == []
    assert docs == [] and failed[0]["error"] == "extract_failed"


def test_pdfium_runs_without_the_parents_environment(site, monkeypatch, tmp_path):
    _fresh_cache(monkeypatch)
    probe = tmp_path / "seen.json"
    monkeypatch.setenv("SEARCHIFY_PROBE_SECRET", "sk-test-not-a-real-key")
    monkeypatch.setenv("SEARCHIFY_PROBE_FILE", str(probe))
    monkeypatch.setattr(pdf_mod, "_TARGET", _env_probe_child)
    monkeypatch.setattr(pdf_mod, "EXTRACT_TIMEOUT_S", 10)
    ROUTES["/a.pdf"] = ("application/pdf", make_pdf(["Etwas Text auf der Seite"]))
    docs, failed = pages_mod.read_pages([site + "/a.pdf"])
    assert failed == [] and "Etwas Text" in docs[0]["content"]
    assert json.loads(probe.read_text()) == [[]]  # pdfium was called once, with no probe variable left


@pytest.mark.parametrize("raw", [b"", b"not json", b"[1, 2, 3]", b'["err", ["x"]]', b'["err", "rm -rf"]',
                                 b'["ok", {"content": 5, "page_count": 1}]',
                                 b'["ok", {"content": "x", "page_count": true}]', b'{"ok": 1, "x": 2}'])
def test_a_malformed_answer_is_extract_failed(raw):
    assert pdf_mod._read_answer(raw) == (None, "extract_failed")


def test_a_deeply_nested_answer_is_extract_failed():
    raw = b"[" * 200_000 + b"]" * 200_000  # json.loads hits the recursion limit
    assert pdf_mod._read_answer(raw) == (None, "extract_failed")


def test_the_largest_real_answer_fits_the_reply_cap():
    # astral characters need 12 bytes each as JSON escapes (a surrogate pair)
    worst = {"title": "\U0001F600" * pdf_mod.MAX_META_CHARS, "author": "\U0001F600" * pdf_mod.MAX_META_CHARS,
             "page_count": 10**9, "content": "\U0001F600" * pdf_mod.MAX_CHARS, "truncated": True}
    assert len(json.dumps(["ok", worst]).encode("ascii")) <= pdf_mod.MAX_REPLY_BYTES


def test_cache_put_measures_outside_the_lock(monkeypatch):
    cache = h.PageCache(10, 60)

    def size(value):
        assert not cache._lock.locked(), "json.dumps of large blocks ran under the cache lock"
        return 1

    monkeypatch.setattr(cache, "_size", size)
    cache.put("u", {"content": "x", "structured": {"json_ld": [{"a": 1}]}})
    assert cache.get("u")


def test_a_wellformed_answer_is_read_field_by_field():
    raw = b'["ok", {"content": "Text \\ud800", "page_count": 3, "title": 7, "truncated": "yes", "x": 1}]'
    assert pdf_mod._read_answer(raw) == ({"content": "Text \ud800", "page_count": 3, "title": "",
                                          "author": None, "truncated": False}, None)


def test_rss_of_a_live_and_a_gone_process():
    if sys.platform == "darwin" or sys.platform.startswith("linux"):
        assert pdf_mod._rss_bytes(os.getpid()) > 5 << 20
    assert pdf_mod._rss_bytes(2**22 + 12345) is None  # no such pid


def test_at_most_two_pdfs_at_once(site, monkeypatch):
    _fresh_cache(monkeypatch)
    live = []
    peak = [0]
    lock = threading.Lock()
    real = pdf_mod._run_child

    def counting(url, data):
        with lock:
            live.append(1)
            peak[0] = max(peak[0], len(live))
        try:
            time.sleep(0.3)
            return real(url, data)
        finally:
            with lock:
                live.pop()

    monkeypatch.setattr(pdf_mod, "_run_child", counting)
    monkeypatch.setattr(pdf_mod, "EXTRACT_TIMEOUT_S", 60)
    urls = []
    for i in range(5):
        ROUTES[f"/{i}.pdf"] = ("application/pdf", make_pdf([f"Text Nummer {i} hier"]))
        urls.append(f"{site}/{i}.pdf")
    docs, failed = pages_mod.read_pages(urls, timeout=40, deadline_s=60)
    assert failed == [] and len(docs) == 5 and peak[0] == 2


def test_pdf_failures_skip_the_browser_stages(monkeypatch):
    called = []
    monkeypatch.setattr(reader.render_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader.render_mod, "try_render", lambda *a, **k: called.append("render") or ("tried", "<p>x</p>"))
    monkeypatch.setattr(reader.stealth_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader.stealth_mod, "try_stealth", lambda *a, **k: called.append("stealth") or ("tried", "<p>x</p>"))
    failed = [{"url": "https://a.example/x.pdf", "error": "pdf_no_text"},
              {"url": "https://a.example/y.pdf", "error": "Zu wenig Text",
               "static": {"url": "https://a.example/y.pdf", "content": "wenig", "page_count": 1}}]
    docs, still = reader._render_failed([], failed, "text", [f["url"] for f in failed])
    assert called == []
    assert [d["url"] for d in docs] == ["https://a.example/y.pdf"]  # thin text is kept
    assert still == [{"url": "https://a.example/x.pdf", "error": "pdf_no_text"}]


# ---------------------------------------------------------------- without the extra


def test_without_the_extra_a_pdf_names_the_missing_extra(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setitem(sys.modules, "pypdfium2", None)  # import is impossible
    assert pdf_mod.available() is False
    ROUTES["/a.pdf"] = ("application/pdf", make_pdf(["Etwas Text auf der Seite"]))
    ROUTES["/b.bin"] = ("application/octet-stream", make_pdf(["Vielleicht ein PDF"]))
    docs, failed = pages_mod.read_pages([site + "/a.pdf", site + "/b.bin"])
    errors = {f["url"]: (f["error"], f["reason"]) for f in failed}
    assert docs == []
    # the reason stays not_html, the text says how to read it
    assert errors[site + "/a.pdf"] == (
        'not HTML: application/pdf (install the extra to read PDFs: pip install "searchify[pdf]")',
        "not_html")
    # an octet-stream may be anything: no guess, the old text
    assert errors[site + "/b.bin"] == ("not HTML: application/octet-stream", "not_html")
    assert sorted(HITS) == ["/a.pdf", "/b.bin"]  # no second request either


def test_html_pages_never_reach_the_pdf_code(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "_download", lambda url: pytest.fail("html page went to the PDF path"))
    ROUTES["/p"] = ("text/html", b"<html><body><article><p>" + b"Ein normaler Absatz mit genug Woertern. " * 20 + b"</p></article></body></html>")
    docs, failed = pages_mod.read_pages([site + "/p"])
    assert failed == [] and docs


@pytest.mark.parametrize("code", ["pdf_encrypted", "pdf_no_text", "pdf_too_large"])
def test_pdf_failures_get_their_own_reason_code(code):
    from searchify.reasons import REASONS, code_for
    assert code in REASONS and code_for(code) == code


def test_big_non_pdf_is_not_called_pdf_too_large(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "MAX_BYTES", 1000)
    ROUTES["/v.mp4"] = ("video/mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 5000)
    ROUTES["/big.bin"] = ("application/octet-stream", b"PK\x03\x04" + b"0" * 5000)
    docs, failed = pages_mod.read_pages([site + "/v.mp4", site + "/big.bin"])
    errors = {f["url"]: (f["error"], f["reason"]) for f in failed}
    assert docs == []
    assert errors[site + "/v.mp4"] == ("not HTML: video/mp4", "not_html")
    assert errors[site + "/big.bin"] == ("not HTML: application/octet-stream", "not_html")


def test_big_pdf_behind_a_wrong_type_is_still_too_large(site, monkeypatch):
    _fresh_cache(monkeypatch)
    monkeypatch.setattr(pdf_mod, "MAX_BYTES", 1000)
    ROUTES["/dl"] = ("application/octet-stream", b"%PDF-1.4\n" + b"0" * 5000)
    docs, failed = pages_mod.read_pages([site + "/dl"])
    assert docs == [] and failed[0]["error"] == "pdf_too_large"


def test_non_pdf_types_cost_one_request_only(site, monkeypatch):
    _fresh_cache(monkeypatch)
    ROUTES["/x.png"] = ("image/png", b"\x89PNG\r\n\x1a\n" + b"0" * 200)
    ROUTES["/x.json"] = ("application/json", b'{"a": 1}')
    ROUTES["/x.zip"] = ("application/zip", b"PK\x03\x04" + b"0" * 100)
    docs, failed = pages_mod.read_pages([site + "/x.png", site + "/x.json", site + "/x.zip"])
    assert docs == [] and len(failed) == 3
    assert sorted(HITS) == ["/x.json", "/x.png", "/x.zip"]  # no second GET


def test_pdf_document_has_no_empty_structured_block():
    doc = pdf_mod._document("https://a.example/x.pdf", {"title": "T", "content": "Text", "page_count": 1})
    assert "structured" not in doc and doc["page_count"] == 1


@pytest.mark.parametrize("ctype", ["application/x-pdf", "binary/octet-stream", "application/download",
                                   "application/force-download"])
def test_pdf_like_types_still_get_the_second_look(site, monkeypatch, ctype):
    _fresh_cache(monkeypatch)
    ROUTES["/f"] = (ctype, make_pdf(["Text hinter dem Typ"]))
    docs, failed = pages_mod.read_pages([site + "/f"])
    assert failed == [] and "Text hinter" in docs[0]["content"]

