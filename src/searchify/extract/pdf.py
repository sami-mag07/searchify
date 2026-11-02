"""PDF reading (optional extra ``pdf``, needs ``pypdfium2``).

Without the extra nothing here does anything and a PDF URL fails exactly as before
("not HTML: application/pdf"). With it, ``read_if_pdf`` is called from the page
reader once the normal fetch has refused a document, and:

* downloads the bytes through the same pinned httpx client and SSRF hook as every
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


def read_if_pdf(
    url: str, html: Optional[str], failure: Optional[str]
) -> Optional[Tuple[Optional[Dict], Optional[str]]]:
    """Called after the normal fetch of `url`. Returns None when this is not a
    case for the PDF path (extra missing, or the fetch did not hint at a PDF), so
    the caller carries on as before. Otherwise (document, None) or (None, reason).
    A "hint" is a refusal as non-HTML or a body that starts with %PDF-."""
    hinted = (failure or "").startswith("not HTML:") or (html or "").startswith("%PDF-")
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
        with htmltext._client().stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S) as resp:
            if resp.status_code >= 400:
                return None
            ctype = resp.headers.get("content-type", "").lower()
            declared = resp.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > MAX_BYTES:
                return None, "pdf_too_large"
            body = bytearray()
            sniffed = "application/pdf" in ctype
            for part in resp.iter_bytes():
                body.extend(part)
                if len(body) > MAX_BYTES:
                    return None, "pdf_too_large"
                if not sniffed and len(body) >= 5:
                    if b"%PDF-" not in bytes(body[:_SNIFF]):
                        return None
                    sniffed = True
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
            if sys.platform.startswith("linux"):
                resource.setrlimit(resource.RLIMIT_AS, (3 << 30, 3 << 30))
        except Exception:  # noqa: BLE001 - limits are a bonus
            pass
        import pypdfium2 as pdfium  # noqa: PLC0415

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
            "title": (meta.get("Title") or "").strip(),
            "author": (meta.get("Author") or "").strip() or None,
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


_TARGET = _worker  # tests swap this for a slow or crashing child


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
        if not recv_end.poll(EXTRACT_TIMEOUT_S):
            return None, "timeout"
        try:
            result = recv_end.recv()
        except (EOFError, OSError):
            return None, "extract_failed"  # child died without an answer
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
    title = info.get("title") or _title_from_url(url)
    doc = htmltext._doc(url, {"title": title, "author": info.get("author")}, info["content"], [])
    doc["structured"] = {"json_ld": [], "meta": {}}
    doc["page_count"] = info["page_count"]
    if info.get("truncated"):
        doc["truncated"] = True
    return doc


def _title_from_url(url: str) -> str:
    parts = urlparse(url)
    name = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    name = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE).replace("_", " ").replace("-", " ").strip()
    return name or parts.hostname or url
