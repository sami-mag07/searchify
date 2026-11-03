"""PDF reading (optional extra ``pdf``, needs ``pypdfium2``).

Without the extra no PDF is read. A document declared as a PDF (application/pdf,
application/x-pdf) then fails with reason ``not_html`` and an error text that names
the extra: ``not HTML: application/pdf (install the extra to read PDFs: pip install
"searchify[pdf]")``; other refused types keep their plain text. With the extra,
``read_if_pdf`` is called from the page reader once the normal fetch has refused a
document, and:

* downloads the bytes (asking for Accept-Encoding: identity; a compressed answer is
  decoded with an output cap, stacked codings are refused) through the same pinned
  httpx client and SSRF hook as every
  HTML page (``htmltext._client()``), streaming with a byte counter. More than
  ``MAX_BYTES`` ends with ``pdf_too_large``; nothing is silently cut off.
* has its own download budget (``DOWNLOAD_TIMEOUT_S``, not FETCH_TIMEOUT).
* detects a PDF by the Content-Type or by ``%PDF-`` in the first bytes.
* extracts the text in one fresh ``spawn`` process per PDF, at most ``MAX_PARALLEL``
  at a time. The parent polls the pipe first and only then joins or kills: a child
  that writes more than 64 KB blocks until somebody reads, so joining first would
  report a timeout for every larger document. A crash or a hang only costs that one
  PDF, never the HTML worker pool or another PDF. No thread fallback.
  The child is crash isolation, not a sandbox. What protects the parent is the
  answer format: plain JSON, checked field by field, never pickle, so a child taken
  over by a crafted PDF cannot run code in the parent through its answer. The child
  also empties os.environ before loading pdfium; that is hygiene against passing
  API keys on by accident (to pdfium or anything it starts), not protection: the
  original environment block stays in the process (ps eww, /proc/self/environ),
  and the same user can read the parent's.
* returns at most ``MAX_PAGES`` pages and ``MAX_CHARS`` characters, with ``[Seite N]``
  markers and ``page_count`` (the real page count of the file).

Failure codes: pdf_encrypted, pdf_no_text, pdf_too_large; also timeout,
extract_failed, network error and address blocked, as for HTML.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import multiprocessing
import os
import re
import sys
import threading
import time
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

import httpx

from . import htmltext, netlimits

log = logging.getLogger("rag-search")

MAX_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 20.0
EXTRACT_TIMEOUT_S = 15.0
MAX_PARALLEL = 2
MAX_PAGES = 50
MAX_CHARS = 60_000
MIN_TEXT_CHARS = 10  # less than this on all read pages = a scan without a text layer
MAX_META_CHARS = 300  # title and author; an 8 MB /Title used to arrive whole
# Largest answer the parent accepts from a child: every character of text, title and
# author as an astral one (12 bytes as a JSON surrogate pair escape), plus room for
# the page markers' separators and the other fields.
MAX_REPLY_BYTES = 12 * (MAX_CHARS + 2 * MAX_META_CHARS) + 4096
_SNIFF = 1024  # the PDF header may follow a few junk bytes, the spec allows 1024

_SLOTS = threading.BoundedSemaphore(MAX_PARALLEL)
_BLANKS = re.compile(r"\n{3,}")


def available() -> bool:
    """True if the optional extra is installed."""
    try:
        return importlib.util.find_spec("pypdfium2") is not None
    except (ImportError, ValueError):
        return False


def is_pdf_failure(fail: Dict) -> bool:
    """A failed entry that came from this module, or a PDF whose text was too thin
    (it carries page_count): a browser cannot do better on those, so the stages skip them."""
    return str(fail.get("error") or "").startswith("pdf_") or "page_count" in (fail.get("static") or {})


_DECLARED_PDF = ("application/pdf", "application/x-pdf")
_PDF_TYPES = _DECLARED_PDF + ("application/octet-stream", "binary/octet-stream",
                              "application/download", "application/force-download")
EXTRA_HINT = 'install the extra to read PDFs: pip install "searchify[pdf]"'


def _refused_type(failure: Optional[str]) -> str:
    """The type of a "not HTML: <type>" refusal, lower case; "" for any other failure."""
    text = failure or ""
    return text[len("not HTML:"):].strip().lower() if text.startswith("not HTML:") else ""


def _pdf_like(failure: Optional[str]) -> bool:
    """A "not HTML: <type>" refusal whose type can hide a PDF."""
    return _refused_type(failure) in _PDF_TYPES


def read_if_pdf(
    url: str, html: Optional[str], failure: Optional[str]
) -> Optional[Tuple[Optional[Dict], Optional[str]]]:
    """Called after the normal fetch of `url`. Returns None when this is not a
    case for the PDF path (the fetch did not hint at a PDF, or the extra is missing
    and the type does not say PDF), so the caller carries on as before. Otherwise
    (document, None) or (None, reason). Without the extra a declared PDF gives
    (None, the refusal text plus the install hint), still reason not_html.
    A "hint" is a refusal as non-HTML with a PDF-like type or a body that starts
    with %PDF-; png, json, zip and the like cost no second request."""
    hinted = _pdf_like(failure) or (html or "").startswith("%PDF-")
    if not hinted:
        return None
    if not available():
        if _refused_type(failure) in _DECLARED_PDF:
            return None, f"{failure} ({EXTRA_HINT})"
        return None
    got = _download(url)
    if got is None:
        return None  # not a PDF after all: the original failure stands
    data, err = got
    if err:
        return None, err
    return _extract(url, data)


# ---------------------------------------------------------------- Download


def _download(url: str) -> Optional[Tuple[Optional[bytes], Optional[str]]]:
    """None = not a PDF (or a plain HTTP error: the earlier failure text is better).
    Else (bytes, None) or (None, reason)."""
    deadline = time.monotonic() + DOWNLOAD_TIMEOUT_S
    try:
        # identity asked for: a compressed PDF gains nothing. A server that compresses
        # anyway is decoded with the output cap of iter_decoded, never by httpx
        # (compression bomb, Security-Review 03.11.2026)
        with netlimits.overall_deadline(DOWNLOAD_TIMEOUT_S), \
                htmltext._client().stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S,
                                          headers={"Accept-Encoding": "identity"}) as resp:
            if resp.status_code >= 400:
                return None
            ctype = resp.headers.get("content-type", "").lower()
            declared = resp.headers.get("content-length", "")
            # only a declared PDF is refused by length; anything else must show %PDF- first
            if declared.isdigit() and int(declared) > MAX_BYTES and "pdf" in ctype:
                return None, "pdf_too_large"
            body = bytearray()
            sniffed = "application/pdf" in ctype
            for part in netlimits.iter_decoded(resp):  # pieces of at most ~256 KB
                body.extend(part)
                if not sniffed and len(body) >= 5:  # first bytes before the size verdict
                    if b"%PDF-" not in bytes(body[:_SNIFF]):
                        return None
                    sniffed = True
                if len(body) > MAX_BYTES:
                    return None, "pdf_too_large"
                if time.monotonic() > deadline:
                    return None, "timeout"
    except httpx.TimeoutException:
        return None, "timeout"
    except netlimits._EncodingRefused:
        return None, "network error (unsupported content encoding)"
    except httpx.HTTPError as exc:
        return None, f"network error ({type(exc).__name__})"
    if not sniffed and b"%PDF-" not in bytes(body[:_SNIFF]):
        return None
    return bytes(body), None


# ---------------------------------------------------------------- Extraction


def _answer(conn, kind: str, payload) -> None:
    """The child's one answer: JSON ["ok", {...}] or ["err", code]. ASCII escapes keep
    lone surrogates from a broken text layer intact, as pickle did."""
    conn.send_bytes(json.dumps([kind, payload]).encode("ascii"))


def _worker(conn, data: bytes, max_pages: int, max_chars: int) -> None:
    """Runs in its own process. Sends exactly one answer (see _answer)."""
    try:
        # Hygiene, not a sandbox: os.environ (API keys) is empty before pypdfium2 loads,
        # so nothing passes it on by accident. The original env block stays readable
        # in this process (ps eww, /proc/self/environ); see the module docstring.
        os.environ.clear()
        try:
            import resource  # noqa: PLC0415 - Unix only

            resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        except Exception:  # noqa: BLE001 - limits are a bonus
            pass
        import pypdfium2 as pdfium  # noqa: PLC0415

        _limit_address_space()

        try:
            doc = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as exc:
            # FPDF_ERR_PASSWORD = 4: encrypted and not openable without a password
            code = "pdf_encrypted" if getattr(exc, "err_code", None) == 4 else "extract_failed"
            _answer(conn, "err", code)
            return
        total = len(doc)
        parts = []
        used = 0
        letters = 0
        truncated = total > max_pages
        for i in range(min(total, max_pages)):
            page = doc[i]
            textpage = page.get_textpage()
            text = (textpage.get_text_range() or "").replace("\r\n", "\n").replace("\r", "\n")
            text = _BLANKS.sub("\n\n", htmltext.strip_line_ends(text)).strip()
            textpage.close()
            page.close()
            if not text:
                continue
            letters += len(text)
            piece = f"[Seite {i + 1}]\n{text}"
            if used + len(piece) > max_chars:
                piece = piece[: max(0, max_chars - used)].rstrip()
                truncated = True
                if piece:
                    parts.append(piece)
                break
            parts.append(piece)
            used += len(piece) + 2
        meta = {}
        try:
            meta = doc.get_metadata_dict(skip_empty=True)
        except Exception:  # noqa: BLE001
            pass
        doc.close()
        if letters < MIN_TEXT_CHARS:
            _answer(conn, "err", "pdf_no_text")
            return
        _answer(conn, "ok", {
            "title": (meta.get("Title") or "").strip()[:MAX_META_CHARS],
            "author": (meta.get("Author") or "").strip()[:MAX_META_CHARS] or None,
            "page_count": total,
            "content": "\n\n".join(parts),
            "truncated": truncated,
        })
    except MemoryError:
        _answer(conn, "err", "pdf_too_large")
    except Exception:  # noqa: BLE001 - whatever pdfium throws, the parent just needs a code
        try:
            _answer(conn, "err", "extract_failed")
        except Exception:  # noqa: BLE001
            pass
    finally:
        conn.close()


def _limit_address_space() -> None:
    """Linux: at most MAX_CHILD_RSS more address space than after loading pdfium (an
    absolute cap would also count what pdfium reserves at start). macOS refuses
    RLIMIT_AS with ValueError; there only the parent's RSS watch below holds."""
    try:
        import resource  # noqa: PLC0415 - Unix only

        with open("/proc/self/statm") as f:
            now = int(f.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
        resource.setrlimit(resource.RLIMIT_AS, (now + MAX_CHILD_RSS, now + MAX_CHILD_RSS))
    except Exception:  # noqa: BLE001 - no /proc or no RLIMIT_AS: the watch still applies
        pass


_TARGET = _worker  # tests swap this for a slow or crashing child

# Memory watch on the child (Security-Review 03.11.2026): a 522 KB PDF drove a child
# to 1081 MB RSS, and macOS has no working RLIMIT_AS. The parent reads the child's
# resident size while it waits and kills it above MAX_CHILD_RSS ("pdf_too_large").
MAX_CHILD_RSS = 1 << 30
_WATCH_EVERY_S = 0.05
_LIBPROC = None


def _rss_bytes(pid: int) -> Optional[int]:
    """Resident size of `pid` in bytes; None when unknown (process gone, other OS).
    macOS: proc_pidinfo(PROC_PIDTASKINFO), Linux: /proc/<pid>/statm. No subprocess."""
    global _LIBPROC
    try:
        if sys.platform == "darwin":
            import ctypes  # noqa: PLC0415
            import struct  # noqa: PLC0415

            if _LIBPROC is None:
                lib = ctypes.CDLL("/usr/lib/libproc.dylib")
                lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                             ctypes.c_void_p, ctypes.c_int]
                lib.proc_pidinfo.restype = ctypes.c_int
                _LIBPROC = lib
            buf = ctypes.create_string_buffer(96)  # struct proc_taskinfo
            if _LIBPROC.proc_pidinfo(pid, 4, 0, buf, 96) != 96:  # 4 = PROC_PIDTASKINFO
                return None
            return struct.unpack_from("=Q", buf, 8)[0]  # pti_resident_size
        with open(f"/proc/{pid}/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _wait_for_answer(recv_end, proc) -> Optional[str]:
    """Waits until the child answers or dies (None), up to EXTRACT_TIMEOUT_S ("timeout").
    Reads the child's RSS between polls and kills it above MAX_CHILD_RSS ("pdf_too_large").
    The poll returns as soon as the answer is there, so a normal PDF waits no longer."""
    deadline = time.monotonic() + EXTRACT_TIMEOUT_S
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return "timeout"
        if recv_end.poll(min(_WATCH_EVERY_S, left)):
            return None
        rss = _rss_bytes(proc.pid)
        if rss is not None and rss > MAX_CHILD_RSS:
            proc.kill()  # at once: every further poll lets it grow
            log.warning("pdf child killed at %d MB resident", rss >> 20)
            return "pdf_too_large"


def _extract(url: str, data: bytes) -> Tuple[Optional[Dict], Optional[str]]:
    if not _SLOTS.acquire(timeout=EXTRACT_TIMEOUT_S):
        return None, "timeout"  # two other PDFs are still being read
    try:
        return _run_child(url, data)
    finally:
        _SLOTS.release()


def _run_child(url: str, data: bytes) -> Tuple[Optional[Dict], Optional[str]]:
    ctx = multiprocessing.get_context("spawn")
    recv_end, send_end = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_TARGET, args=(send_end, data, MAX_PAGES, MAX_CHARS), daemon=True)
    raw = b""
    try:
        try:
            proc.start()
        except Exception as exc:  # noqa: BLE001
            log.debug("pdf child did not start: %r", exc)
            return None, "extract_failed"
        send_end.close()  # parent keeps only the read end, so a dead child gives EOF
        # Poll and read FIRST. Joining a child that is blocked writing >64 KB would
        # deadlock until the timeout and wrongly report every large PDF as too slow.
        failure = _wait_for_answer(recv_end, proc)
        if failure:
            return None, failure
        try:
            # bounded: a child gone wrong cannot hand the parent an answer of any size
            raw = recv_end.recv_bytes(maxlength=MAX_REPLY_BYTES)
        except (EOFError, OSError):
            return None, "extract_failed"  # child died without an answer, or one too large
    finally:
        recv_end.close()
        if proc.pid is not None:
            proc.join(2)
            if proc.is_alive():
                proc.kill()
                proc.join(2)
    info, err = _read_answer(raw)
    if err:
        return None, err
    return _document(url, info), None


_CHILD_CODES = {"pdf_encrypted", "pdf_no_text", "pdf_too_large", "extract_failed"}


def _read_answer(raw: bytes) -> Tuple[Optional[Dict], Optional[str]]:
    """The child's JSON answer, checked field by field: anything unexpected is
    extract_failed, never an exception in the parent."""
    try:
        kind, payload = json.loads(raw)
    except (ValueError, TypeError, RecursionError):  # RecursionError: [[[[...]]]] nested deep
        return None, "extract_failed"
    if kind == "err":
        return None, payload if isinstance(payload, str) and payload in _CHILD_CODES else "extract_failed"
    if kind != "ok" or not isinstance(payload, dict):
        return None, "extract_failed"
    content, pages = payload.get("content"), payload.get("page_count")
    if not isinstance(content, str) or type(pages) is not int:
        return None, "extract_failed"
    title, author = payload.get("title"), payload.get("author")
    return {
        "content": content,
        "page_count": pages,
        "title": title if isinstance(title, str) else "",
        "author": author if isinstance(author, str) else None,
        "truncated": payload.get("truncated") is True,
    }, None


def _document(url: str, info: Dict) -> Dict:
    title = (info.get("title") or "")[:MAX_META_CHARS] or _title_from_url(url)
    author = (info.get("author") or "")[:MAX_META_CHARS] or None
    doc = htmltext._doc(url, {"title": title, "author": author}, info["content"], [])
    doc["page_count"] = info["page_count"]
    doc["stage"] = "static"
    if info.get("truncated"):
        doc["truncated"] = True
    return doc


def _title_from_url(url: str) -> str:
    parts = urlparse(url)
    name = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    name = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE).replace("_", " ").replace("-", " ").strip()
    return name or parts.hostname or url
