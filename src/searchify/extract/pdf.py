"""PDF reading (optional extra ``pdf``, needs ``pypdfium2``).

Without the extra nothing here does anything and a PDF URL fails exactly as before
("not HTML: application/pdf"). With it, ``read_if_pdf`` is called from the page
reader once the normal fetch has refused a document, and:

* downloads the bytes uncompressed (Accept-Encoding: identity, any other encoding is
  refused) through the same pinned httpx client and SSRF hook as every
  HTML page (``htmltext._client()``), streaming with a byte counter. More than
  ``MAX_BYTES`` ends with ``pdf_too_large``; nothing is silently cut off.
* has its own download budget (``DOWNLOAD_TIMEOUT_S``, not FETCH_TIMEOUT).
* detects a PDF by the Content-Type or by ``%PDF-`` in the first bytes.
* extracts the text in one fresh ``spawn`` process per PDF, at most ``MAX_PARALLEL``
  at a time. The parent polls the pipe first and only then joins or kills: a child
  that writes more than 64 KB blocks until somebody reads, so joining first would
  report a timeout for every larger document. A crash or a hang only costs that one
  PDF, never the HTML worker pool or another PDF. No thread fallback.
* returns at most ``MAX_PAGES`` pages and ``MAX_CHARS`` characters, with ``[Seite N]``
  markers and ``page_count`` (the real page count of the file).

Failure codes: pdf_encrypted, pdf_no_text, pdf_too_large; also timeout,
extract_failed, network error and address blocked, as for HTML.
"""

from __future__ import annotations

import importlib.util
import logging
import multiprocessing
import os
import pickle
import re
import sys
import threading
import time
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

import httpx

from . import htmltext

log = logging.getLogger("rag-search")

MAX_BYTES = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 20.0
EXTRACT_TIMEOUT_S = 15.0
MAX_PARALLEL = 2
MAX_PAGES = 50
MAX_CHARS = 60_000
MIN_TEXT_CHARS = 10  # less than this on all read pages = a scan without a text layer
MAX_META_CHARS = 300  # title and author; an 8 MB /Title used to arrive whole
# Largest answer the parent accepts from a child: MAX_CHARS of text at up to 6 bytes
# per character (JSON escapes) plus title and author fit many times over.
MAX_REPLY_BYTES = 1 << 20
_SNIFF = 1024  # the PDF header may follow a few junk bytes, the spec allows 1024

_SLOTS = threading.BoundedSemaphore(MAX_PARALLEL)
_BLANKS = re.compile(r"\n{3,}")
_TRAIL_WS = re.compile(r"[ \t]+\n")


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


_PDF_TYPES = ("application/pdf", "application/x-pdf", "application/octet-stream",
              "binary/octet-stream", "application/download", "application/force-download")


def _pdf_like(failure: Optional[str]) -> bool:
    """A "not HTML: <type>" refusal whose type can hide a PDF."""
    text = failure or ""
    return text.startswith("not HTML:") and text[len("not HTML:"):].strip().lower() in _PDF_TYPES


def read_if_pdf(
    url: str, html: Optional[str], failure: Optional[str]
) -> Optional[Tuple[Optional[Dict], Optional[str]]]:
    """Called after the normal fetch of `url`. Returns None when this is not a
    case for the PDF path (extra missing, or the fetch did not hint at a PDF), so
    the caller carries on as before. Otherwise (document, None) or (None, reason).
    A "hint" is a refusal as non-HTML with a PDF-like type or a body that starts
    with %PDF-; png, json, zip and the like cost no second request."""
    hinted = _pdf_like(failure) or (html or "").startswith("%PDF-")
    if not hinted or not available():
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
        # identity only: a compressed PDF gains nothing, and httpx would decode it
        # without an output cap (compression bomb, Security-Review 03.11.2026)
        with htmltext.overall_deadline(DOWNLOAD_TIMEOUT_S), \
                htmltext._client().stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S,
                                          headers={"Accept-Encoding": "identity"}) as resp:
            if resp.status_code >= 400:
                return None
            if htmltext.content_encodings(resp):
                return None, "network error (unsupported content encoding)"
            ctype = resp.headers.get("content-type", "").lower()
            declared = resp.headers.get("content-length", "")
            # only a declared PDF is refused by length; anything else must show %PDF- first
            if declared.isdigit() and int(declared) > MAX_BYTES and "pdf" in ctype:
                return None, "pdf_too_large"
            body = bytearray()
            sniffed = "application/pdf" in ctype
            for part in resp.iter_raw():  # never decoded: the check above refused any encoding
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
    except httpx.HTTPError as exc:
        return None, f"network error ({type(exc).__name__})"
    if not sniffed and b"%PDF-" not in bytes(body[:_SNIFF]):
        return None
    return bytes(body), None


# ---------------------------------------------------------------- Extraction


def _worker(conn, data: bytes, max_pages: int, max_chars: int) -> None:
    """Runs in its own process. Sends exactly one tuple, ("ok", dict) or ("err", code)."""
    try:
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
            conn.send(("err", code))
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
            text = _BLANKS.sub("\n\n", _TRAIL_WS.sub("\n", text)).strip()
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
            conn.send(("err", "pdf_no_text"))
            return
        conn.send(("ok", {
            "title": (meta.get("Title") or "").strip()[:MAX_META_CHARS],
            "author": (meta.get("Author") or "").strip()[:MAX_META_CHARS] or None,
            "page_count": total,
            "content": "\n\n".join(parts),
            "truncated": truncated,
        }))
    except MemoryError:
        conn.send(("err", "pdf_too_large"))
    except Exception:  # noqa: BLE001 - whatever pdfium throws, the parent just needs a code
        try:
            conn.send(("err", "extract_failed"))
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
    result = None
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
            result = pickle.loads(recv_end.recv_bytes(maxlength=MAX_REPLY_BYTES))
        except (EOFError, OSError):
            return None, "extract_failed"  # child died without an answer, or one too large
    finally:
        recv_end.close()
        if proc.pid is not None:
            proc.join(2)
            if proc.is_alive():
                proc.kill()
                proc.join(2)
    kind, payload = result
    if kind == "err":
        return None, payload
    return _document(url, payload), None


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
