"""Fetch + Extraktion + Chunking.

- Laden der Quell-URLs via httpx, parallel mit hartem Zeitbudget.
- Haupttext-Extraktion via trafilatura (Apache-2.0).
- Chunking als reine Python-Funktion (Wort-Fenster + Overlap, eigene Wahl).

trafilatura wird LAZY importiert, damit dieses Modul auch ohne installierte
Schwer-Abhaengigkeit importierbar bleibt (Tests/Dry-Run).
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import logging
import multiprocessing
import os
import re
import socket
import threading
import time
import zlib
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FuturesTimeout
from concurrent.futures.process import BrokenProcessPool
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpcore
import httpx

from .config import get_settings

log = logging.getLogger("rag-search")


def strip_line_ends(text: str) -> str:
    """Spaces and tabs before each line break removed, in linear time.

    Same result as re.sub(r"[ \t]+\n", "\n", text), which backtracks quadratically on
    a long run of spaces without a line break (40,000 spaces took 2 s)."""
    lines = text.split("\n")
    return "\n".join([line.rstrip(" \t") for line in lines[:-1]] + lines[-1:])


def chunk_text(text: str, chunk_size: int = 350, overlap: int = 60) -> List[str]:
    """Teilt Text in sich ueberlappende Wort-Fenster.

    Pure function, no external dependencies. chunk_size/overlap are in words
    (a rough token approximation).
    """
    words = text.split()
    if not words:
        return []
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 4)

    chunks: List[str] = []
    step = max(1, chunk_size - overlap)
    for start in range(0, len(words), step):
        window = words[start : start + chunk_size]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + chunk_size >= len(words):
            break
    return chunks


_MIN_WORDS_FAST = 120  # darunter hat der schnelle Extraktor vermutlich zu wenig erwischt


def _extract_fast(html: str) -> Optional[str]:
    """resiliparse (Apache-2.0): ~1 ms je Seite. None, wenn nicht installiert."""
    try:
        from resiliparse.extract.html2text import extract_plain_text  # noqa: PLC0415
        from resiliparse.parse.html import HTMLTree  # noqa: PLC0415
    except ImportError:
        return None
    text = extract_plain_text(
        HTMLTree.parse(html), main_content=True, links=False, alt_texts=False,
        list_bullets=False, form_fields=False,
    )
    return _drop_ui_lines(text) or None


def _drop_ui_lines(text: str) -> str:
    """Navigation, Buttons, Menues raus: Zeilen unter 5 Woertern ohne Satzzeichen
    am Ende und ohne Ziffer. resiliparse laesst solche Reste ("Zum Inhalt
    springen", "Meine Merkliste") durch, und mit dem Seitentitel davor rankten
    sie teils auf Platz 1. Zeilen mit Ziffern bleiben (Infobox: "Einwohner 456.692")."""
    keep = [
        line.strip()
        for line in text.splitlines()
        if len(line.split()) >= 5
        or line.rstrip().endswith((".", "!", "?", ":"))
        or any(ch.isdigit() for ch in line)
    ]
    return "\n".join(keep).strip()


def extract_main_text(html: str, url: str = "") -> Optional[str]:
    """Haupttext aus HTML ziehen. Lazy-Import.

    Zweistufig: erst resiliparse (schnell), bei weniger als 120 Woertern
    trafilatura (genauer bei kurzen/ungewoehnlichen Seiten).
    Gibt None zurueck, wenn kein verwertbarer Haupttext gefunden wird.
    """
    fast = _extract_fast(html)
    if fast and len(fast.split()) >= _MIN_WORDS_FAST:
        return fast
    try:
        import trafilatura  # noqa: PLC0415 (bewusst lazy)
    except ImportError as exc:  # pragma: no cover - nur ohne Installation
        raise ImportError(
            "trafilatura ist nicht installiert. `pip install trafilatura` "
            "(steht in requirements.txt)."
        ) from exc

    extracted = trafilatura.extract(
        html,
        url=url or None,
        include_comments=False,
        include_tables=True,
        no_fallback=True,  # Fallback-Extraktoren kosten ~3x Zeit bei kaum mehr Text
    )
    if not extracted:
        return fast
    return extracted.strip()


# Aktions-Links (Anmeldung, Bewerbung, Formulare) zuerst: Fuer Programme und
# Bewerbungen ist genau der Link hinter dem Button oft die eigentliche Antwort.
_ACTION_RE = re.compile(
    r"\b(anmeld\w*|registr\w*|bewerb\w*|teilnahme\w*|teilnehm\w*|formular\w*|"
    r"antrag\w*|antr(ae|ä)ge|einreich\w*|buchen|buchung|apply|application|applications|"
    r"register|registration|sign[ -]?up|enrol+(ment)?|submit|forms?|ausschreibung\w*|"
    # Spanisch
    r"inscripci(o|ó)n\w*|inscrib\w*|solicitud\w*|solicitar|registr(o|arse)|formulario\w*|"
    r"postular\w*|postulaci(o|ó)n\w*|convocatoria\w*|matr(i|í)cula\w*|"
    # Franzoesisch
    r"inscri(re|ption|vez|ptions)\w*|candidat(ure|er)\w*|postuler|formulaire\w*|"
    r"demande\w*|s.?enregistrer|appel (a|à) (projets|candidatures))\b"
    r"|\.pdf\b",
    re.IGNORECASE,
)
_MAX_LINKS = 25


def extract_links(
    html: Optional[str],
    base_url: str = "",
    limit: int = _MAX_LINKS,
    keep_self: bool = False,
    tree=None,
) -> List[Dict[str, str]]:
    """Links einer Seite als [{"text", "url"}], Aktions-Links zuerst.

    Ohne javascript:/mailto:/tel: und reine Sprungmarken, absolute URLs, keine
    Dubletten, hoechstens 25. keep_self (Lese-Modus): Links auf die Seite selbst
    bleiben samt Sprungmarke wie beim Original, sonst fallen Knoepfe wie
    "Apply" -> "#modal_box" als Dublette des ersten Selbstlinks weg.
    Reine Funktion (laeuft im Extraktions-Prozess).
    """
    try:
        from lxml import html as lxml_html  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        return []
    if tree is None:
        try:
            tree = lxml_html.fromstring(html)
        except (ValueError, TypeError, lxml_html.etree.ParserError):
            return []
    page = base_url.split("#", 1)[0]
    seen: set = set()
    action: List[Dict[str, str]] = []
    other: List[Dict[str, str]] = []
    for el in tree.iter("a", "form"):
        if el.tag == "form" and _is_search_form(el):
            continue  # Suchfeld der Website ist keine Aktion
        href = (el.get("href") if el.tag == "a" else el.get("action")) or ""
        href = href.strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        if href.startswith("#") and not keep_self:
            continue  # Sprungmarke = Seite selbst
        try:
            url = urljoin(base_url, href)
        except ValueError:
            continue  # kaputte URL (z. B. "http://[bad") ueberspringen
        if not keep_self:
            url = url.split("#", 1)[0]
        elif url.endswith("#"):
            url = url[:-1]
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        if url == page and not keep_self:
            continue
        text = " ".join((el.text_content() if el.tag == "a" else "Formular").split())[:120]
        if not text:
            text = (el.get("aria-label") or el.get("title") or "").strip()[:120]
        if not text:
            continue
        seen.add(url)
        # Ganze Woerter: "form" darf nicht in "informationen" treffen.
        probe = f"{text} {url.replace('-', ' ').replace('_', ' ').replace('/', ' ')}"
        (action if _ACTION_RE.search(probe) else other).append(
            {"text": text, "url": url}
        )
    return (action + other)[:limit]


def _is_search_form(form) -> bool:
    action = (form.get("action") or "").lower()
    if any(w in action for w in ("search", "suche", "/such")):
        return True
    for inp in form.iter("input"):
        if (inp.get("type") or "").lower() == "search" or (inp.get("name") or "").lower() in (
            "q", "s", "query", "search", "suche", "keys",
        ):
            return True
    return False


def _metadata(html: str, url: str = "", extensive: bool = True) -> Dict:
    """title, date, author, description (trafilatura); leer bei Fehler.
    extensive=False: Datum nur aus Meta-Angaben/Markup, ohne Freitext-Suche per
    dateparser (die kostet beim ersten Aufruf je Sprache mehrere Sekunden)."""
    try:
        from trafilatura.metadata import extract_metadata  # noqa: PLC0415

        m = extract_metadata(html, default_url=url or None, extensive=extensive)
        if m is None:
            return {}
        return m.as_dict() if hasattr(m, "as_dict") else dict(m.__dict__)
    except Exception:  # noqa: BLE001 - Metadaten sind Beiwerk
        return {}


def _doc(url: str, meta: Dict, content: Optional[str], links: List[Dict[str, str]]) -> Dict:
    return {
        "url": url,
        "title": (meta.get("title") or "").strip() or None,
        "published": meta.get("date"),
        "author": meta.get("author"),
        "description": meta.get("description"),
        "content": (content or "").strip() or None,
        "links": links,
    }


def extract_document(html: str, url: str = "", fmt: str = "text") -> Dict:
    """Ganze Seite als Dokument: title, published (ISO-Datum oder None), author,
    description, content (Text oder Markdown je `fmt`), links. Reine Funktion,
    runs in the extraction process."""
    meta = _metadata(html, url)
    content: Optional[str] = None
    if fmt == "markdown":
        try:
            import trafilatura  # noqa: PLC0415

            content = trafilatura.extract(
                html, url=url or None, output_format="markdown", include_formatting=True,
                include_tables=True, include_links=False, no_fallback=True,
            )
        except Exception:  # noqa: BLE001
            content = None
    if not content:
        content = extract_main_text(html, url)
    try:
        links = extract_links(html, url)
    except Exception:  # noqa: BLE001
        links = []
    return _doc(url, meta, content, links)


def extract_page(html: str, url: str = "") -> Tuple[Optional[str], List[Dict[str, str]], Dict]:
    """Haupttext, Links und Metadaten in einem Durchgang (eine Prozess-Uebergabe).
    Die Metadaten fuellen den Dokument-Cache, damit Exa/Jina/Extract eine Seite
    aus der Suche nicht ein zweites Mal laden. Fehler bei Links oder Metadaten
    duerfen den Text nie kosten."""
    text = extract_main_text(html, url)
    try:
        links = extract_links(html, url)
    except Exception:  # noqa: BLE001 - Links sind Beiwerk
        links = []
    return text, links, _metadata(html, url)


# Wikimedia (and others) answer 403 to bots whose User-Agent names no contact, except for
# pages their edge cache already holds: with the old "rag-search-bot ... clean-room" agent
# Wikipedia's popular articles loaded and most others never did. Operators can put their
# own contact here (SEARCHIFY_USER_AGENT), as the Wikimedia policy asks.
_USER_AGENT = os.environ.get("SEARCHIFY_USER_AGENT") or (
    "Mozilla/5.0 (compatible; searchify; +https://github.com/sami-mag07/searchify)"
)
_MAX_BYTES = 1_000_000  # groessere Seiten werden abgeschnitten, nicht komplett geladen

# ---------------------------------------------------------------- bounded body decoding
# httpx decodes every network read (up to 64 KB) in one call without an output cap,
# stacked encodings ("gzip, gzip") included: 610 bytes on the wire became one 256 MB
# piece before any size check (Security-Review 03.11.2026). The page and PDF reads
# therefore take the raw bytes and decode them here, at most _DECODE_STEP per piece
# and at most one encoding layer. Brotli only with a module that has an output cap.
_DECODE_STEP = 256 * 1024


def _bounded_brotli():
    for name in ("brotli", "brotlicffi"):
        try:
            mod = __import__(name)
            probe = mod.Decompressor()
        except Exception:  # noqa: BLE001 - missing or broken: br is simply not offered
            continue
        if hasattr(probe, "can_accept_more_data"):  # Brotli >= 1.2 has output_buffer_limit
            return mod
    return None


_BROTLI = _bounded_brotli()
ACCEPT_ENCODING = "gzip, deflate, br" if _BROTLI is not None else "gzip, deflate"


class _Inflate:
    """gzip or deflate (zlib wrapper, raw deflate as fallback like httpx)."""

    def __init__(self, kind: str) -> None:
        self._raw_fallback = kind == "deflate"
        self._obj = zlib.decompressobj(zlib.MAX_WBITS | 16 if kind == "gzip" else zlib.MAX_WBITS)

    def feed(self, data: bytes) -> Iterator[bytes]:
        while not self._obj.eof:
            try:
                out = self._obj.decompress(data, _DECODE_STEP)
            except zlib.error:
                if not self._raw_fallback:
                    raise
                self._raw_fallback = False
                self._obj = zlib.decompressobj(-zlib.MAX_WBITS)
                continue
            self._raw_fallback = False
            data = self._obj.unconsumed_tail
            if out:
                yield out
            if not data and len(out) < _DECODE_STEP:
                return  # input used up and nothing left inside the decoder

    def finish(self) -> Iterator[bytes]:
        """Whatever the decoder still holds after the last network read."""
        return self.feed(b"")


class _Brotli:
    def __init__(self, mod) -> None:
        self._obj = mod.Decompressor()
        self._run = getattr(self._obj, "process", None) or self._obj.decompress

    def feed(self, data: bytes) -> Iterator[bytes]:
        out = self._run(data, output_buffer_limit=_DECODE_STEP)
        # Drain until a call gives nothing: in Brotli 1.2 can_accept_more_data() only
        # reports pending input, not output still inside the decoder, and the rest of a
        # page behind the last network read was silently lost (887 KB -> 622 KB).
        while out:
            yield out
            out = self._run(b"", output_buffer_limit=_DECODE_STEP)

    def finish(self) -> Iterator[bytes]:
        """Whatever the decoder still holds after the last network read."""
        return self.feed(b"")


# Real content codings (RFC 9110, with the old x- aliases). Anything else in the header
# ("UTF-8", "none", "binary" on an uncompressed body) is skipped, as httpx does.
_CODINGS = {"gzip": "gzip", "x-gzip": "gzip", "deflate": "deflate", "br": "br",
            "zstd": "zstd", "compress": "compress", "x-compress": "compress"}


def content_encodings(resp: httpx.Response) -> List[str]:
    """The known content codings of `resp`, in header order (aliases normalised)."""
    values = resp.headers.get_list("content-encoding", split_commas=True)
    return [_CODINGS[v] for v in (x.strip().lower() for x in values) if v in _CODINGS]


class _EncodingRefused(httpx.DecodingError):
    """More than one coding layer, or one we cannot decode with an output cap."""


def iter_decoded(resp: httpx.Response) -> Iterator[bytes]:
    """The body of a streamed `resp`, decoded in pieces of at most about _DECODE_STEP
    bytes, so a compression bomb never lands in memory in one go. Raises
    _EncodingRefused for stacked codings or one without an output cap (zstd, compress,
    br without a capping Brotli module), httpx.DecodingError for a broken stream."""
    layers = content_encodings(resp)
    if len(layers) > 1 or (layers and layers[0] not in ("gzip", "deflate", "br")) \
            or (layers == ["br"] and _BROTLI is None):
        raise _EncodingRefused("unsupported content encoding", request=resp.request)
    if not layers:
        yield from resp.iter_raw()
        return
    decoder = _Brotli(_BROTLI) if layers[0] == "br" else _Inflate(layers[0])
    errors = (zlib.error,) + ((_BROTLI.error,) if _BROTLI is not None else ())
    try:
        for raw in resp.iter_raw():
            yield from decoder.feed(raw)
        yield from decoder.finish()
    except errors as exc:
        raise httpx.DecodingError(str(exc), request=resp.request) from exc


class _RedirectBodyGuard(httpx.BaseTransport):
    """httpx reads and decodes the whole body of every redirect it follows, without
    a cap. Here that body is read raw up to 64 KB (so the connection stays reusable)
    and replaced by an empty one; nobody needs it, the browser follows Location."""

    _MAX_RAW = 64 * 1024

    def __init__(self, inner: httpx.BaseTransport) -> None:
        self._inner = inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        resp = self._inner.handle_request(request)
        if resp.status_code not in _REDIRECTS:
            return resp
        try:
            seen = 0
            for raw in resp.stream:  # raw bytes, httpx has not decoded anything yet
                seen += len(raw)
                if seen > self._MAX_RAW:
                    break
        finally:
            resp.stream.close()
        return httpx.Response(resp.status_code, headers=resp.headers, stream=httpx.ByteStream(b""),
                              extensions=resp.extensions, request=request)

    def close(self) -> None:
        self._inner.close()


_CLIENT: Optional[httpx.Client] = None
_POOL: Optional[ThreadPoolExecutor] = None


_SIZED_FIELDS = ("content", "title", "author", "description")


class PageCache:
    """Extrahierte Seitentexte im Speicher (LRU + TTL), threadsicher.

    Downloads, die das Zeitbudget einer Anfrage verpassen, laufen im Hintergrund
    zu Ende und landen hier. Die naechste Anfrage mit derselben Quelle bekommt
    den Volltext dann sofort. Das ist der kleine, lokale Ersatz fuer einen Index.
    """

    def __init__(self, max_items: int, ttl_s: int, max_bytes: Optional[int] = None) -> None:
        self.max_items, self.ttl_s, self.max_bytes = max_items, ttl_s, max_bytes
        self._data: "OrderedDict[str, Tuple[float, object]]" = OrderedDict()
        self._sizes: Dict[str, int] = {}
        self._total = 0
        self._lock = threading.Lock()

    @staticmethod
    def _size(value) -> int:
        """Grobe Groesse in Zeichen: Text bzw. content, Titel, Autor, Beschreibung und
        Links eines Dokuments (ein 8-MB-PDF-Titel zaehlte vorher 0, Security-Review 03.11.2026)."""
        if isinstance(value, str):
            return len(value)
        if isinstance(value, dict):
            text = sum(len(v) for v in (value.get(k) for k in _SIZED_FIELDS) if isinstance(v, str))
            # JSON-LD and meta blocks can be as large as the page itself
            blocks = sum(len(json.dumps(value[k], default=str)) for k in ("structured", "product")
                         if value.get(k))
            return text + blocks + 120 * len(value.get("links") or [])
        if isinstance(value, list):
            return 120 * len(value)
        return 0

    def _drop(self, key: str) -> None:
        self._data.pop(key, None)
        self._total -= self._sizes.pop(key, 0)

    def get(self, url: str) -> Optional[str]:
        if self.ttl_s <= 0:
            return None
        with self._lock:
            hit = self._data.get(url)
            if not hit:
                return None
            stored, text = hit
            if time.monotonic() - stored > self.ttl_s:
                self._drop(url)
                return None
            self._data.move_to_end(url)
            return text

    def put(self, url: str, text: str) -> None:
        if self.ttl_s <= 0:
            return
        with self._lock:
            self._drop(url)
            self._data[url] = (time.monotonic(), text)
            self._sizes[url] = self._size(text)
            self._total += self._sizes[url]
            # Grenze nach Anzahl UND Groesse: Reader-Dokumente (ganze Seiten, viele
            # Links) wuerden sonst bei 5000 Eintraegen mehrere GB belegen.
            while self._data and (
                len(self._data) > self.max_items
                or (self.max_bytes is not None and self._total > self.max_bytes)
            ):
                self._drop(next(iter(self._data)))


_INIT_LOCK = threading.Lock()
_CACHE: Optional[PageCache] = None


def _cache() -> PageCache:
    global _CACHE
    if _CACHE is None:
        with _INIT_LOCK:
            if _CACHE is None:
                settings = get_settings()
                _CACHE = PageCache(settings.page_cache_size, settings.page_cache_ttl_s)
    return _CACHE


# is_global laesst IPv6-Formen durch, die eine IPv4 einbetten oder veraltet sind
# (Security-Review 15.10.2026): NAT64 64:ff9b::/96 fuehrt in NAT64-Netzen (Hotspot)
# zu 192.168.x.x, dazu 6to4, IPv4-kompatibel ::a.b.c.d und Site-Local fec0::/10.
_V6_EMBED = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_V6_BLOCK = (ipaddress.ip_network("fec0::/10"), ipaddress.ip_network("::/96"))


def ip_is_public(raw: str) -> bool:
    """Oeffentliche Adresse (is_global), eingebettete IPv4 wird mitgeprueft."""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if not ip.is_global:
        return False
    if ip.version == 6:
        if any(ip in net for net in _V6_BLOCK):
            return False
        inner = ip.ipv4_mapped or ip.sixtofour
        if inner is None and any(ip in net for net in _V6_EMBED):
            inner = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
        if inner is not None and not inner.is_global:
            return False
    return True


def _public_ips(host: str) -> List[str]:
    """Aufgeloeste IPs des Hosts, aber nur wenn ALLE oeffentlich sind, sonst [].
    Loopback, private Netze, Link-Local & Co. fuehren zu []."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    ips: List[str] = []
    for info in infos:
        raw = info[4][0].split("%")[0]
        if not ip_is_public(raw):
            return []
        if raw not in ips:
            ips.append(raw)
    return ips


def _host_is_public(host: str) -> bool:
    return bool(_public_ips(host))


# The overall budget of one fetch (Security-Review 03.11.2026): httpx only knows a
# timeout per read, and the body loop only checked the budget once the headers were
# in. A server dripping 64 header bytes every 0.1 s held a 2 s budget for 6.4 s.
# The caller sets a deadline for its thread (overall_deadline); every connect,
# TLS handshake, read and write of the pinned client then gets at most the time left.
_MAX_CONNECT_IPS = 4  # addresses tried per host, each with its own connect timeout


class overall_deadline:  # noqa: N801 - used like a function: with overall_deadline(5): ...
    """All network I/O of the pinned client in this thread ends `seconds` from now
    (or at an earlier deadline already set) with a timeout error."""

    __slots__ = ("_seconds", "_outer")

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def __enter__(self) -> float:
        self._outer = getattr(_TLS, "deadline", None)
        deadline = time.monotonic() + self._seconds
        _TLS.deadline = deadline if self._outer is None else min(self._outer, deadline)
        return _TLS.deadline

    def __exit__(self, *exc) -> None:
        _TLS.deadline = self._outer


def _time_left(timeout: Optional[float], error: type) -> Optional[float]:
    deadline = getattr(_TLS, "deadline", None)
    if deadline is None:
        return timeout
    left = deadline - time.monotonic()
    if left <= 0:
        raise error("overall time budget used up")
    return left if timeout is None else min(timeout, left)


class _DeadlineStream(httpcore.NetworkStream):
    """A connection whose every step is capped by the deadline of the thread using it."""

    def __init__(self, inner: httpcore.NetworkStream) -> None:
        self._inner = inner

    def read(self, max_bytes: int, timeout: Optional[float] = None) -> bytes:
        return self._inner.read(max_bytes, _time_left(timeout, httpcore.ReadTimeout))

    def write(self, buffer: bytes, timeout: Optional[float] = None) -> None:
        self._inner.write(buffer, _time_left(timeout, httpcore.WriteTimeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None) -> "_DeadlineStream":
        # Python bounds the whole handshake by the socket timeout, not each step
        inner = self._inner.start_tls(ssl_context, server_hostname,
                                      _time_left(timeout, httpcore.ConnectTimeout))
        return _DeadlineStream(inner)

    def get_extra_info(self, info: str):
        return self._inner.get_extra_info(info)


class _PinnedBackend(httpcore.SyncBackend):
    """Verbindet nur zu geprueften, oeffentlichen IPs (Schutz gegen DNS-Rebinding).

    Pruefen und Verbinden passieren hier in einem Schritt: Der Host wird einmal
    aufgeloest, geprueft und genau diese IP verbunden. Ohne das koennte ein
    Angreifer-DNS bei der Pruefung eine oeffentliche und beim Verbinden eine
    interne IP liefern. TLS/SNI und Zertifikat laufen weiter auf den Hostnamen.
    """

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        ips = _public_ips(host)
        if not ips:
            raise httpcore.ConnectError(f"Interne oder unaufloesbare Adresse blockiert: {host}")
        # Wie socket.create_connection: gepruefte Adressen der Reihe nach, damit ein
        # toter erster A-Record (oder IPv6 ohne Route) den Host nicht kippt. Hoechstens
        # _MAX_CONNECT_IPS, sonst kosten 50 tote Records 50 Connect-Timeouts.
        last_error: Optional[Exception] = None
        for ip in ips[:_MAX_CONNECT_IPS]:
            try:
                stream = super().connect_tcp(
                    ip, port, timeout=_time_left(timeout, httpcore.ConnectTimeout),
                    local_address=local_address, socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
                continue
            return _DeadlineStream(stream)
        raise last_error  # type: ignore[misc]


class _BlockedAddress(httpx.RequestError):
    """The request goes to an internal or invalid address (reported as "address not allowed")."""


def _block_internal(request: httpx.Request) -> None:
    """SSRF protection: applies to the first request AND every redirect, so a
    page cannot redirect to a private or link-local address (e.g. 169.254.x)."""
    if request.url.scheme not in ("http", "https") or not _host_is_public(request.url.host):
        raise _BlockedAddress("Interne oder ungueltige Adresse blockiert", request=request)


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        with _INIT_LOCK:
            if _CLIENT is None:
                transport = httpx.HTTPTransport(
                    # Lange Keep-Alive-Zeit: haeufige Hosts (Wikipedia & Co.) sparen den TLS-Aufbau.
                    limits=httpx.Limits(
                        max_connections=64, max_keepalive_connections=32, keepalive_expiry=120
                    ),
                )
                # httpx bietet keinen offiziellen Parameter fuers Netzwerk-Backend; httpx
                # 0.28 / httpcore 1.0 sind in requirements.txt gepinnt, Test prueft die Naht.
                transport._pool._network_backend = _PinnedBackend()
                _CLIENT = httpx.Client(
                    follow_redirects=True,
                    # only what iter_decoded can decode with an output cap
                    headers={"User-Agent": _USER_AGENT, "Accept-Encoding": ACCEPT_ENCODING},
                    event_hooks={"request": [_block_internal]},
                    transport=_RedirectBodyGuard(transport),
                )
    return _CLIENT


_POOL_WORKERS = 48
# Obergrenze fuer gleichzeitig laufende + wartende Downloads. Darueber nutzt eine
# Anfrage nur noch Snippets und Cache, statt hinter verspaeteten Downloads anzustehen.
_MAX_INFLIGHT = 2 * _POOL_WORKERS
_INFLIGHT: set = set()  # URLs, die gerade laden (gegen doppeltes Laden)
_INFLIGHT_LOCK = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        with _INIT_LOCK:
            if _POOL is None:
                _POOL = ThreadPoolExecutor(max_workers=_POOL_WORKERS, thread_name_prefix="fetch")
    return _POOL


_TLS = threading.local()
_REDIRECTS = {301, 302, 303, 307, 308}


# Why the last fetch on this thread failed, so callers can report a reason
# ("HTTP 403", "not HTML: application/pdf", ...) instead of a generic message.
def _note_failure(reason: str) -> None:
    _TLS.failure = reason


def _reset_failure() -> None:
    _TLS.failure = None


def fetch_failure() -> Optional[str]:
    return getattr(_TLS, "failure", None)
_BLOCKED = {403, 429, 503}  # typische Antworten von Bot-Schutz


def _curl_session():
    """Eine curl_cffi-Session je Thread (Sessions sind nicht threadsicher)."""
    sess = getattr(_TLS, "curl", None)
    if sess is None:
        from curl_cffi import requests as curl_requests  # noqa: PLC0415 (optional)

        # trust_env=False: a proxy from the environment would resolve the host itself
        # and see the request before the connected-IP check (blind SSRF).
        sess = curl_requests.Session(impersonate="chrome", trust_env=False)
        _TLS.curl = sess
    return sess


def _plain_url(url: str) -> bool:
    """Nur eindeutige http(s)-URLs fuer curl: kein Backslash, keine Leer-/Steuerzeichen,
    keine Zugangsdaten vor dem Host. Sonst koennten urlparse und curl verschiedene
    Hosts lesen (geprueft wird der eine, verbunden der andere)."""
    if any(c == "\\" or ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
        return False
    try:
        parts = urlparse(url)
        parts.port  # noqa: B018 - wirft bei ungueltigem Port
    except ValueError:
        return False
    # Kein % und nur ASCII im Host: curl dekodiert %-Folgen und wandelt Unicode nach
    # IDNA2008 ("faß" -> xn--fa-hia), Python prueft dagegen den Rohtext bzw. IDNA2003
    # ("fass"). Dann loeste curl einen ungeprueften Host selbst auf.
    netloc_ok = parts.netloc.isascii() and "%" not in parts.netloc and "@" not in parts.netloc
    return parts.scheme in ("http", "https") and bool(parts.hostname) and netloc_ok


def _fetch_url_curl(url: str, timeout: float, max_bytes: int = _MAX_BYTES) -> Optional[str]:
    """Wie fetch_url, aber mit Chrome-TLS-Fingerprint (curl_cffi): weniger 403 von
    Bot-Schutz. Redirects werden selbst verfolgt, damit jeder Sprung den
    SSRF-Check durchlaeuft."""
    from curl_cffi import CurlOpt  # noqa: PLC0415 (optional)

    deadline = time.monotonic() + timeout
    try:
        for _ in range(5):
            if not _plain_url(url):
                _note_failure("address not allowed")
                return None
            parts = urlparse(url)
            ips = _public_ips(parts.hostname or "")
            if not ips:
                _note_failure("address not public or not resolvable")
                return None
            # Geprueften Host auf die gepruefte IP festnageln (CURLOPT_RESOLVE), damit
            # curl nicht selbst neu aufloest (DNS-Rebinding). Session ist je Thread.
            port = parts.port or (443 if parts.scheme == "https" else 80)
            sess = _curl_session()
            pinned = ",".join(f"[{ip}]" if ":" in ip else ip for ip in ips)
            # RESOLVE nagelt den geprueften Host auf die geprueften IPs fest. CONNECT_TO
            # (eine Liste) nimmt curl_cffi nicht an: es warf TypeError, der still None
            # ergab, darum lief dieser Rueckfall nie. Stattdessen wird unten die IP geprueft,
            # mit der curl tatsaechlich verbunden ist, bevor ein Byte gelesen wird.
            sess.curl_options = {CurlOpt.RESOLVE: [f"{parts.hostname}:{port}:{pinned}"],
                                 CurlOpt.PROXY: ""}
            resp = sess.get(
                url, timeout=max(0.1, deadline - time.monotonic()), allow_redirects=False, stream=True
            )
            try:
                if _connected_ip(resp) not in {str(ipaddress.ip_address(ip)) for ip in ips}:
                    log.warning("curl fallback connected to an unchecked address, dropped: %s", url)
                    _note_failure("address not public or not resolvable")
                    return None
                if resp.status_code in _REDIRECTS and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                if resp.status_code >= 400:
                    _note_failure(f"HTTP {resp.status_code}")
                    return None
                ctype = resp.headers.get("content-type", "")
                if ctype and "html" not in ctype and "text" not in ctype:
                    _note_failure(f"not HTML: {ctype.split(';')[0].strip()}")
                    return None
                body = bytearray()
                for part in resp.iter_content():
                    body.extend(part)
                    if len(body) >= max_bytes:
                        break
                    if time.monotonic() > deadline:
                        _note_failure("timeout")
                        return None
                return body.decode(resp.encoding or "utf-8", errors="replace")
            finally:
                resp.close()
    except Exception as exc:  # noqa: BLE001 - curl_cffi wirft eigene Fehlertypen
        log.debug("curl fallback failed for %s: %r", url, exc)
        _note_failure(f"network error ({type(exc).__name__})")
        return None
    _note_failure("too many redirects")
    return None


def _connected_ip(resp) -> str:
    """The address curl really connected to, in ipaddress's normal form ("" if unreadable)."""
    raw = (getattr(resp, "primary_ip", "") or "").strip("[]").split("%")[0]
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return ""


def fetch_url(
    url: str, timeout: Optional[float] = None, max_bytes: Optional[int] = None,
    *, plain: bool = False,
) -> Optional[str]:
    """Fetch a URL and return its HTML text (or None on failure).

    `timeout` applies to the whole download, headers included (overall_deadline):
    httpx only counts per read step, so a slow-dripping server could otherwise hold
    a worker indefinitely. `plain` forces httpx and switches the Chrome-TLS fallback off, whatever FETCH_CLIENT
    and FETCH_FALLBACK say (the ladder cap ``plain``); the search path never sets it.
    """
    settings = get_settings()
    if plain:
        settings = dataclasses.replace(settings, fetch_client="httpx", fetch_fallback=False)
    timeout = timeout or settings.fetch_timeout
    limit = max_bytes or _MAX_BYTES
    _reset_failure()
    return _fetch_live(url, timeout, limit, settings)


def _fetch_live(url: str, timeout: float, limit: int, settings) -> Optional[str]:
    if settings.fetch_client == "curl_cffi":
        return _fetch_url_curl(url, timeout, limit)
    deadline = time.monotonic() + timeout
    try:
        with overall_deadline(timeout), _client().stream("GET", url, timeout=timeout) as resp:
            blocked = resp.status_code in _BLOCKED and settings.fetch_fallback
            if not blocked:
                if not resp.is_success:
                    _note_failure(f"HTTP {resp.status_code}")
                    return None
                ctype = resp.headers.get("content-type", "")
                if ctype and "html" not in ctype and "text" not in ctype:
                    _note_failure(f"not HTML: {ctype.split(';')[0].strip()}")
                    return None
                body = bytearray()
                for part in iter_decoded(resp):  # pieces of at most _DECODE_STEP bytes
                    body.extend(part)
                    if len(body) >= limit:
                        del body[limit:]
                        break
                    if time.monotonic() > deadline:
                        _note_failure("timeout")
                        return None
                return body.decode(resp.encoding or "utf-8", errors="replace")
            _note_failure(f"HTTP {resp.status_code}")
    except httpx.TimeoutException:
        _note_failure("timeout")
        return None
    except _EncodingRefused:
        _note_failure("network error (unsupported content encoding)")
        return None
    except _BlockedAddress:
        _note_failure("address not allowed")
        return None
    except httpx.HTTPError as exc:
        _note_failure(f"network error ({type(exc).__name__})")
        return None
    # Bot-Schutz antwortet dem ehrlichen Bot-UA oft mit 403/429/503: einmal mit
    # Chrome-TLS-Profil nachladen (gemessen 14.10.: +4,7 % der Trefferseiten lesbar).
    remaining = deadline - time.monotonic()
    if remaining < 0.3:
        return None  # no time for the retry: the block status noted above stays
    blocked_status = fetch_failure()
    html = _fetch_url_curl(url, remaining, limit)
    if html is None and blocked_status:
        retry = fetch_failure()
        blocked_again = (retry or "").startswith("HTTP ") and (retry or "")[5:8].isdigit() \
            and int(retry[5:8]) in _BLOCKED
        if blocked_again:
            _note_failure(f"{blocked_status}, also after retry")
        elif retry and retry != blocked_status:
            _note_failure(f"{blocked_status}, retry: {retry}")
    return html


_EXTRACT_POOL: Optional[ProcessPoolExecutor] = None


def _extract_pool() -> ProcessPoolExecutor:
    """Extraktion ist reine CPU-Arbeit (100-250 ms je grosser Seite). In Threads
    laufen die Seiten wegen der GIL praktisch nacheinander, in Prozessen parallel."""
    global _EXTRACT_POOL
    if _EXTRACT_POOL is None:
        with _INIT_LOCK:
            if _EXTRACT_POOL is None:
                workers = get_settings().extract_workers or max(2, min(8, (os.cpu_count() or 4) - 1))
                # spawn explizit: Linux nimmt sonst fork und erbt Threads/torch-Zustand.
                _EXTRACT_POOL = ProcessPoolExecutor(
                    max_workers=workers, mp_context=multiprocessing.get_context("spawn")
                )
    return _EXTRACT_POOL


_CLOSED = False


def shutdown() -> None:
    """Prozess-Pool beim Dienst-Ende sauber beenden. Danach extrahiert nichts mehr,
    damit Hintergrund-Downloads keinen neuen Pool starten."""
    global _EXTRACT_POOL, _CLOSED
    with _INIT_LOCK:
        _CLOSED = True
        pool, _EXTRACT_POOL = _EXTRACT_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def warm_up() -> None:
    """Startet die Extraktions-Prozesse vorab (trafilatura-Import ~0.5 s je Prozess)."""
    pool = _extract_pool()
    for fut in [pool.submit(extract_page, "<p>warm</p>") for _ in range(pool._max_workers)]:
        fut.result()


def discard_extract_pool(pool: ProcessPoolExecutor) -> None:
    """Einen abgestuerzten Pool verwerfen, damit der naechste Aufruf einen neuen
    anlegt. Nur genau diesen Pool: ein schon neu angelegter bleibt."""
    global _EXTRACT_POOL
    with _INIT_LOCK:
        if _EXTRACT_POOL is pool:
            _EXTRACT_POOL = None


def _extract(html: str, url: str) -> Tuple[Optional[str], List[Dict[str, str]], Dict]:
    if _CLOSED:
        return None, [], {}
    pool = _extract_pool()
    try:
        return pool.submit(extract_page, html, url).result(timeout=10)
    except FuturesTimeout:
        return None, [], {}  # pathologische Seite: nicht noch einmal im Thread rechnen
    except BrokenProcessPool:
        log.warning("Extraktions-Pool kaputt, wird neu angelegt")
        discard_extract_pool(pool)
        return extract_page(html, url)
    except RuntimeError:  # Pool nicht verfuegbar (z. B. beim Herunterfahren)
        return extract_page(html, url)


_LINKS: Optional[PageCache] = None
_DOCS: Optional[PageCache] = None


def doc_cache() -> PageCache:
    """Ganze Dokumente je "<format>|<url>" (genutzt von app/pages und _fetch_text)."""
    global _DOCS
    if _DOCS is None:
        with _INIT_LOCK:
            if _DOCS is None:
                settings = get_settings()
                _DOCS = PageCache(
                    settings.page_cache_size, max(settings.page_cache_ttl_s, 600),
                    max_bytes=settings.doc_cache_max_mb * 1_000_000,
                )
    return _DOCS


def _links_cache() -> PageCache:
    global _LINKS
    if _LINKS is None:
        with _INIT_LOCK:
            if _LINKS is None:
                settings = get_settings()
                _LINKS = PageCache(settings.page_cache_size, max(settings.page_cache_ttl_s, 600))
    return _LINKS


def links_for(url: str) -> List[Dict[str, str]]:
    """Links einer in dieser oder einer frueheren Anfrage geladenen Seite."""
    return _links_cache().get(url) or []


def _fetch_text(url: str) -> Optional[str]:
    """Laden + Extrahieren mit dem normalen FETCH_TIMEOUT (nicht dem Anfrage-Budget),
    damit verspaetete Seiten fertig werden und im Cache landen."""
    try:
        html = fetch_url(url)
        text, links, meta = _extract(html, url) if html else (None, [], {})
        if text:
            _cache().put(url, text)
            doc_cache().put(f"text|{url}", _doc(url, meta, text, links))
        if links:
            _links_cache().put(url, links)
        return text
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(url)


def _claim(url: str) -> bool:
    """True, wenn diese Anfrage die URL laden darf (nicht schon unterwegs, Limit frei)."""
    with _INFLIGHT_LOCK:
        if url in _INFLIGHT or len(_INFLIGHT) >= _MAX_INFLIGHT:
            return False
        _INFLIGHT.add(url)
        return True


class PageFetcher:
    """Laedt Seiten parallel; URLs koennen jederzeit nachgereicht werden.

    `poll` liefert alles, was seit dem letzten Aufruf fertig wurde (Cache-Treffer
    sofort). Laufende Downloads werden nie abgebrochen: Was das Budget einer
    Anfrage verpasst, fuellt den Cache fuer spaetere Anfragen.
    Nur aus einem Thread benutzen (dem der Anfrage).
    """

    def __init__(self) -> None:
        self._futures: Dict[Future, int] = {}
        self._ready: List[Tuple[int, str]] = []

    def add(self, idx: int, url: str) -> None:
        if not url:
            return
        text = _cache().get(url)
        if text:
            self._ready.append((idx, text))
        elif _claim(url):
            self._futures[_pool().submit(_fetch_text, url)] = idx

    def has_pending(self) -> bool:
        return bool(self._futures or self._ready)

    def poll(self, timeout: float) -> List[Tuple[int, str]]:
        """Fertige Seiten abholen; wartet hoechstens `timeout` auf die erste."""
        batch, self._ready = self._ready, []
        if self._futures:
            done, _ = wait(
                list(self._futures),
                timeout=0 if batch else max(0.0, timeout),
                return_when=FIRST_COMPLETED,
            )
            for fut in done:
                idx = self._futures.pop(fut)
                try:
                    text = fut.result()
                except Exception:  # noqa: BLE001 - kaputte Seite darf die Suche nicht kippen
                    text = None
                if text:
                    batch.append((idx, text))
        return batch


def iter_pages(
    candidates: List[Dict[str, str]], budget_s: Optional[float] = None
) -> Iterator[List[Tuple[int, str]]]:
    """Startet SOFORT alle Downloads parallel und liefert danach Buendel von
    (index, haupttext): jeweils alles, was seit dem letzten Buendel fertig wurde.
    So rankt der Aufrufer gleichzeitig angekommene Seiten in einem Durchgang.

    Kein Generator auf oberster Ebene, damit die Downloads schon laufen, waehrend
    der Aufrufer noch etwas anderes tut (z. B. die Snippets rankt). Harte
    Zeitgrenze `budget_s` ab dem Aufruf: Was bis dahin nicht da ist, wird nicht
    mehr abgewartet (die Quelle laeuft dann nur mit Snippet weiter).
    """
    budget = budget_s if budget_s is not None else get_settings().fetch_budget_ms / 1000
    deadline = time.monotonic() + budget
    fetcher = PageFetcher()
    for i, c in enumerate(candidates):
        fetcher.add(i, c.get("url", ""))

    def _arrivals() -> Iterator[List[Tuple[int, str]]]:
        while fetcher.has_pending():
            remaining = deadline - time.monotonic()
            batch = fetcher.poll(max(0.0, remaining))
            if batch:
                yield batch
            elif remaining <= 0:
                return  # Zeitbudget aufgebraucht

    return _arrivals()


def page_chunks(
    cand: Dict[str, str],
    full_text: Optional[str],
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
    *,
    with_snippet: bool = True,
) -> List[Dict[str, str]]:
    """Chunks einer Quelle. Das Snippet des Such-Backends ist ein eigener Chunk,
    weil es meist genau die gesuchte Stelle trifft."""
    settings = get_settings()
    url, title, snippet = cand.get("url", ""), cand.get("title", ""), cand.get("content", "")
    out: List[Dict[str, str]] = []
    if snippet and with_snippet:
        out.append({"url": url, "title": title, "content": snippet, "full_text": full_text or ""})
    if full_text:
        for piece in chunk_text(full_text, chunk_size or settings.chunk_size, overlap or settings.chunk_overlap):
            out.append({"url": url, "title": title, "content": piece, "full_text": full_text})
    return out


def fetch_and_extract(
    candidates: List[Dict[str, str]],
    *,
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
    budget_s: Optional[float] = None,
) -> List[Dict[str, str]]:
    """Alle Chunks auf einmal (fuer Eval/Skripte). Der Dienst nutzt iter_pages."""
    texts = dict(pair for batch in iter_pages(candidates, budget_s) for pair in batch)
    out: List[Dict[str, str]] = []
    for i, cand in enumerate(candidates):
        out.extend(page_chunks(cand, texts.get(i), chunk_size, overlap))
    return out
# Stand: 24.10.2026
