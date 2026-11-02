"""Reading mode: turn a whole page into clean, readable text.

A full-page reader keeps the whole article, not just a short snippet:

- Content: trafilatura with favor_recall (Markdown or plain text). If the main
  text covers less than FULL_PAGE_RATIO of the page's words (landing pages with
  teasers, program pages made of many boxes), the full page text is used instead.
- Title: og:title, else <title>, without a trailing site suffix.
- Links: all links on the page (up to MAX_LINKS), action links first.

Pages load in parallel with SSRF protection and size limits; extraction runs in
a process pool. Results are cached under their own key so reader documents and
any short-snippet documents never mix.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import htmltext as extract_mod
from . import pages as pages_mod
from . import pdf as pdf_mod
from . import render as render_mod
from . import stealth as stealth_mod
from ..config import get_settings
from ..search._common import SearchBackendError
from ..search.cloud import tavily_post

FULL_PAGE_RATIO = 0.4  # offline auf 40 URLs: 0.3 bis 0.5 gleich gut, 0.6 kostet Precision
WIKI_MIN_WORDS = 100  # darunter (Stub, Weiterleitung) zusaetzlich trafilatura
MAX_LINKS = 1500  # Original liefert alle Links (Wikipedia Median ~1400)
_WORD_RE = re.compile(r"[^\W\d_]{4,}")
_TITLE_SUFFIX_RE = re.compile(r"\s+[-–—|]\s+Wikip[eé]dia\b.*$", re.IGNORECASE)


def _words(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def _full_page_text(tree) -> str:
    from resiliparse.extract.html2text import extract_plain_text  # noqa: PLC0415

    return extract_plain_text(
        tree, main_content=False, links=False, alt_texts=False, list_bullets=True,
        form_fields=False,
    ).strip()


# MediaWiki (Wikipedia & Co.): Haupttext-Extraktion verliert Belege, Hauptartikel-
# Hinweise und Navigationsleisten. Darum dort die ganze Seite ohne Seitenrahmen.
_WIKI_CHROME = (
    "//nav|//header[not(.//h1)]|//footer|//aside|//script|//style|//noscript|//form"
    "|//*[@role='navigation' or @role='banner' or @role='contentinfo' or @role='search']"
    "|//*[contains(concat(' ',normalize-space(@class),' '),' mw-editsection ')]"
    "|//*[contains(concat(' ',normalize-space(@class),' '),' mw-jump-link ')]"
    "|//*[contains(concat(' ',normalize-space(@class),' '),' vector-page-toolbar ')]"
    "|//*[contains(concat(' ',normalize-space(@class),' '),' vector-dropdown ')]"
    "|//*[contains(concat(' ',normalize-space(@class),' '),' vector-menu ')]"
    "|//*[contains(concat(' ',normalize-space(@class),' '),' vector-column-end ')]"
    "|//*[@id='mw-hidden-catlinks' or @id='siteSub' or @id='contentSub']"
    # Seitenrahmen unter dem Artikel: "Abgerufen von ...", Kategorien, Normdaten (GND/VIAF)
    "|//*[@id='catlinks' or @id='normdaten']"
    "|//*[contains(concat(' ',normalize-space(@class),' '),' printfooter ')]"
    "|//*[contains(concat(' ',normalize-space(@class),' '),' catlinks ')]"
    "|//*[@id='mw-navigation' or @id='mw-panel' or @id='p-lang' or @id='footer'"
    " or @id='mw-head' or @id='vector-toc' or @id='toc']"
)


_HIDDEN = "//*[contains(translate(@style,' ',''),'display:none')]"


def _tables_as_rows(tree) -> None:
    """Tabellen als Markdown-Zeilen ("| a | b |") wie das Original. Ohne das klebt
    der Text der Zellen zusammen ("PréfectureProvence-Alpes")."""
    from lxml import etree  # noqa: PLC0415

    for table in tree.xpath("//table[not(ancestor::table)]"):
        for el in table.xpath("." + _HIDDEN):
            if el.getparent() is not None:
                el.drop_tree()
        lines: List[str] = []
        for tr in table.iter("tr"):
            if next(tr.iterancestors("table"), None) is not table:
                continue  # Zeilen verschachtelter Tabellen stecken schon in der Zelle
            cells = []
            for cell in tr.xpath("./th|./td"):
                for el in cell.iter("br", "p", "div", "li"):
                    el.tail = " " + (el.tail or "")
                cells.append(" ".join(cell.text_content().split()).replace("|", "/"))
            if not any(cells):
                continue
            lines.append("| " + " | ".join(cells) + " |")
            if len(lines) == 1:
                lines.append("|" + " --- |" * len(cells))
        box = etree.Element("p")
        for line in lines:
            etree.SubElement(box, "br").tail = line
        box.tail = table.tail
        parent = table.getparent()
        if parent is not None:
            parent.replace(table, box)


def _wiki_text(html: str, fmt: str = "markdown") -> str:
    """MediaWiki-Seite ohne Navigation, Kopf, Fuss und [Bearbeiten]-Links
    (Markdown: Ueberschriften als #-Zeilen, der Rest als Text)."""
    from lxml import html as lxml_html  # noqa: PLC0415
    from resiliparse.parse.html import HTMLTree  # noqa: PLC0415

    tree = lxml_html.fromstring(html)
    for el in tree.xpath(_WIKI_CHROME):
        if el.getparent() is not None:
            el.drop_tree()
    _tables_as_rows(tree)
    if fmt == "markdown":
        _mark_headings(tree)
    return _full_page_text(HTMLTree.parse(lxml_html.tostring(tree, encoding="unicode")))


def _mark_headings(tree) -> None:
    for head in tree.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6"):
        head.text = "#" * int(head.tag[1]) + " " + (head.text or "").lstrip()


# GitHub: die README steckt in article.markdown-body. Neben Dateiliste, Menues und
# Seitenleiste ist sie oft unter FULL_PAGE_RATIO der Seite, dann kaeme die ganze Seite.
_README = "//article[contains(concat(' ',normalize-space(@class),' '),' markdown-body ')]"
README_MIN_WORDS = 50


def _readme_text(html: str, fmt: str = "markdown") -> str:
    from lxml import html as lxml_html  # noqa: PLC0415
    from resiliparse.parse.html import HTMLTree  # noqa: PLC0415

    found = lxml_html.fromstring(html).xpath(_README)
    if not found:
        return ""
    box = found[0]
    for el in box.xpath(".//svg|.//script|.//style|.//*[contains(@class,'anchor')]"):
        if el.getparent() is not None:
            el.drop_tree()
    _tables_as_rows(box)
    if fmt == "markdown":
        _mark_headings(box)
    return _full_page_text(HTMLTree.parse(lxml_html.tostring(box, encoding="unicode")))


def _unwrap_forms(html: str) -> str:
    """Formulare als normale Bloecke: die Beschriftungen (Bewerbungsfragen,
    "max. 1000 characters") sind Inhalt, die Felder selbst nicht. Ohne das
    verwerfen trafilatura und resiliparse den ganzen <form>-Block."""
    from lxml import html as lxml_html  # noqa: PLC0415

    tree = lxml_html.fromstring(html)
    for el in tree.xpath("//select|//textarea|//input|//button[not(normalize-space())]"):
        if el.getparent() is not None:
            el.drop_tree()
    for form in tree.xpath("//form"):
        form.tag = "div"
    # trafilatura verwirft <label>/<legend>/<fieldset> samt Text ("Proposal title",
    # "(max. 1000 characters)"); als normale Absaetze bleiben sie.
    for el in tree.xpath("//label|//legend"):
        el.tag = "p"
    for el in tree.xpath("//fieldset"):
        el.tag = "div"
    return lxml_html.tostring(tree, encoding="unicode")


def reader_content(html: str, url: str = "", fmt: str = "markdown") -> Optional[str]:
    """Haupttext mit hoher Abdeckung, bei zu knappem Haupttext die ganze Seite."""
    import trafilatura  # noqa: PLC0415
    from resiliparse.parse.html import HTMLTree  # noqa: PLC0415

    if "<form" in html or "<FORM" in html:
        try:
            html = _unwrap_forms(html)
        except Exception:  # noqa: BLE001 - kaputtes HTML: wie gehabt
            pass

    wiki = ""
    if "mw-parser-output" in html:
        # MediaWiki: Seite ohne Rahmen reicht; trafilatura (bis 1 s auf grossen
        # Artikeln) nur bei kurzen Seiten, dann gewinnt der laengere Text.
        try:
            wiki = _wiki_text(html, fmt)
        except Exception:  # noqa: BLE001
            wiki = ""
        if _words(wiki) >= WIKI_MIN_WORDS:
            return wiki
    if "markdown-body" in html:
        try:
            readme = _readme_text(html, fmt)
        except Exception:  # noqa: BLE001 - kaputtes HTML: wie gehabt
            readme = ""
        if _words(readme) >= README_MIN_WORDS:
            return readme
    try:
        main = trafilatura.extract(
            html, url=url or None, favor_recall=True, include_tables=True,
            include_comments=False, no_fallback=True,
            output_format="markdown" if fmt == "markdown" else "txt",
            include_formatting=fmt == "markdown", include_links=False,
        ) or ""
    except Exception:  # noqa: BLE001 - kaputtes HTML: ganze Seite versuchen
        main = ""
    try:
        full = _full_page_text(HTMLTree.parse(html))
    except Exception:  # noqa: BLE001
        full = ""
    n_main, n_full = _words(main), _words(full)
    if wiki and _words(wiki) > n_main:
        return wiki
    if n_full and n_main < FULL_PAGE_RATIO * n_full:
        return full
    return main.strip() or full or None


_TITLE_SEP_RE = re.compile(r" [|\-\\/>»] ")
_TITLE_HIER_RE = re.compile(r" [\\/>»] ")
_LD_ARTICLE_RE = re.compile(
    r"^(?:Article|AdvertiserContentArticle|NewsArticle|AnalysisNewsArticle|AskPublicNewsArticle"
    r"|BackgroundNewsArticle|OpinionNewsArticle|ReportageNewsArticle|ReviewNewsArticle|Report"
    r"|SatiricalArticle|ScholarlyArticle|MedicalScholarlyArticle|SocialMediaPosting|BlogPosting"
    r"|LiveBlogPosting|DiscussionForumPosting|TechArticle|APIReference)$")
_META_PROP_RE = re.compile(r"^\s*(dc|dcterm|og|twitter)\s*:\s*title\s*$", re.IGNORECASE)
_META_NAME_RE = re.compile(
    r"^\s*(?:(dc|dcterm|og|twitter|parsely|weibo:(?:article|webpage))\s*[-.:]\s*)?title\s*$", re.IGNORECASE)
# Reihenfolge wie Mozilla Readability (_getArticleMetadata)
_META_ORDER = ("dc:title", "dcterm:title", "og:title", "weibo:article:title", "weibo:webpage:title",
               "title", "twitter:title", "parsely:title")


def _jsonld_title(tree) -> str:
    """Titel aus JSON-LD (nur Artikel-Typen, wie Readability)."""
    import json  # noqa: PLC0415

    for node in tree.xpath('//script[@type="application/ld+json"]'):
        raw = (node.text or "").strip().replace("<![CDATA[", "").replace("]]>", "")
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        flat: List[Dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                flat.extend(g for g in graph if isinstance(g, dict))
            flat.append(item)
        for item in flat:
            kinds = item.get("@type")
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if not any(isinstance(k, str) and _LD_ARTICLE_RE.match(k) for k in kinds):
                continue
            for key in ("name", "headline"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    return " ".join(val.split())
    return ""


def _meta_title(tree) -> str:
    found: Dict[str, str] = {}
    for meta in tree.xpath("//meta[@content]"):
        content = " ".join((meta.get("content") or "").split())
        if not content:
            continue
        prop, name = meta.get("property") or "", meta.get("name") or ""
        for tok in prop.split():
            if _META_PROP_RE.match(tok):
                found.setdefault(re.sub(r"\s", "", tok.lower()), content)
        if name and _META_NAME_RE.match(name):
            key = re.sub(r"\s", "", name.lower()).replace(".", ":").replace("-", ":")
            found.setdefault(key, content)
    for key in _META_ORDER:
        if found.get(key):
            return found[key]
    return ""


def _doc_title(tree) -> str:
    """Dokument-Titel gekuerzt wie Readability (_getArticleTitle): Seitenname hinter
    dem letzten Trenner weg, ausser der Rest wird zu kurz."""
    orig = " ".join((tree.findtext(".//title") or "").split())
    cur, hier = orig, False
    wc = lambda s: len(s.split())  # noqa: E731
    if _TITLE_SEP_RE.search(cur):
        hier = bool(_TITLE_HIER_RE.search(cur))
        cur = re.sub(r"(.*)[|\-\\/>»] .*", r"\1", orig)
        if wc(cur) < 3:
            cur = re.sub(r"^[^|\-\\/>»]*[|\-\\/>»](.*)", r"\1", orig)
    elif ": " in cur:
        heads = {" ".join(h.text_content().split()) for h in tree.xpath("//h1|//h2")}
        if cur not in heads:
            cur = orig[orig.rfind(":") + 1:]
            if wc(cur) < 3:
                cur = orig[orig.find(":") + 1:]
            elif wc(orig[:orig.find(":")]) > 5:
                cur = orig
    elif len(cur) > 150 or len(cur) < 15:
        h1 = tree.xpath("//h1")
        if len(h1) == 1:
            cur = h1[0].text_content()
    cur = " ".join(cur.split())
    n = wc(cur)
    if n <= 4 and (not hier or n != wc(re.sub(r"[|\-\\/>»]+", "", orig)) - 1):
        cur = orig
    return cur


def reader_title(tree, fallback: Optional[str]) -> Optional[str]:
    """Titel wie das Original (Readability): JSON-LD-Artikel, sonst Meta-Titel
    (dc/og/title/twitter), sonst <title> ohne Seitennamen; Wikipedia-Suffix weg."""
    title = ""
    if tree is not None:
        for get in (_jsonld_title, _meta_title, _doc_title):
            try:
                title = get(tree)
            except Exception:  # noqa: BLE001
                title = ""
            if title:
                break
    title = " ".join(title.split()) or (fallback or "").strip()
    return _TITLE_SUFFIX_RE.sub("", title) or None


def reader_links(tree, base_url: str = "", limit: int = MAX_LINKS) -> List[Dict[str, str]]:
    """Alle Links der Seite wie beim Original (bis `limit`), Aktions-Links zuerst,
    Links auf die Seite selbst bleiben (oft genau der Button, z. B. "Apply now!")."""
    if tree is None:
        return []
    return extract_mod.extract_links(None, base_url, limit=limit, keep_self=True, tree=tree)


_TRAIL_WS_RE = re.compile(r"[ \t]+\n")
_BLANKS_RE = re.compile(r"\n{3,}")
INLINE_WINDOW = 1500  # Anker-Text hoechstens so weit hinter der letzten Fundstelle suchen
_ANCHOR_MAX = 200


def _anchors(tree, base_url: str) -> List[Tuple[str, str, str]]:
    """(Text, absolute URL, title-Attribut) aller Links in Dokument-Reihenfolge."""
    from urllib.parse import urljoin  # noqa: PLC0415

    out: List[Tuple[str, str, str]] = []
    for a in tree.iter("a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        text = " ".join(a.text_content().split())
        if not text or len(text) > _ANCHOR_MAX:
            continue
        try:
            link = urljoin(base_url, href) if base_url else href
        except ValueError:
            continue
        if not link.startswith(("http://", "https://")):
            continue
        title = " ".join((a.get("title") or "").split()).replace('"', "'")
        out.append((text, link.replace(" ", "%20"), title))
    return out


def _is_word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def inline_links(content: str, tree, base_url: str = "", window: int = INLINE_WINDOW) -> str:
    """Links wie das Original als Markdown in den Text setzen: [Text](URL "Titel").
    Die Extraktion selbst laeuft ohne Links (Link-Dichte bleibt Boilerplate-Signal);
    danach wird jeder Anker-Text in Dokument-Reihenfolge ab der letzten Fundstelle
    gesucht (ganze Woerter, hoechstens `window` Zeichen weiter). Nicht gefundene
    Anker (Navigation, die die Extraktion verworfen hat) bleiben weg."""
    if not content or tree is None:
        return content
    parts: List[str] = []
    cur = 0
    for text, link, title in _anchors(tree, base_url):
        pos = content.find(text, cur, cur + window + len(text))
        while pos >= 0 and ((pos > 0 and _is_word(content[pos - 1]) and _is_word(text[0])) or (
                pos + len(text) < len(content) and _is_word(content[pos + len(text)])
                and _is_word(text[-1]))):
            pos = content.find(text, pos + 1, cur + window + len(text))
        if pos < 0:
            continue
        parts.append(content[cur:pos])
        md = f'[{text}]({link} "{title}")' if title else f"[{text}]({link})"
        parts.append(md)
        cur = pos + len(text)
    parts.append(content[cur:])
    return "".join(parts)


AUTHOR_SCAN_MAX_CHARS = 300_000  # Autor-Suche im Seitentext (Kopie des Baums) nur bis hier


def _reader_metadata(html: str, url: str, tree) -> Dict:
    """Wie app.extract._metadata(extensive=False), aber auf dem schon geparsten Baum
    und ohne die Autor-Suche im Seitentext auf grossen Seiten (Wikipedia: 0,3-0,7 s,
    findet dort ohnehin nur Navigationstext). Datum nur aus Meta-Angaben/Markup."""
    if tree is None:
        return extract_mod._metadata(html, url, extensive=False)
    try:
        from htmldate import find_date  # noqa: PLC0415
        from trafilatura.metadata import (  # noqa: PLC0415
            examine_meta, extract_author, extract_meta_json, extract_title, extract_url)
        from trafilatura.settings import set_date_params  # noqa: PLC0415

        md = examine_meta(tree)
        if md.author and " " not in md.author:
            md.author = None
        try:
            md = extract_meta_json(tree, md)
        except Exception:  # noqa: BLE001 - Fehler im JSON-LD-Parser
            pass
        if not md.title:
            md.title = extract_title(tree)
        if not md.author and len(html) <= AUTHOR_SCAN_MAX_CHARS:
            md.author = extract_author(tree)
        cfg = set_date_params(False)
        cfg["url"] = md.url or extract_url(tree, url or None)
        md.date = find_date(tree, **cfg)
        md.clean_and_trim()
        return {"title": md.title, "date": md.date, "author": md.author, "description": md.description}
    except Exception:  # noqa: BLE001 - interne trafilatura-API geaendert: wie bisher
        return extract_mod._metadata(html, url, extensive=False)


_STRUCTURED_META_RE = re.compile(r"^(og|twitter|article|product|fb|al|music|video|book|profile):", re.IGNORECASE)


def structured_data(tree) -> Dict:
    """The machine-readable blocks a page carries, untouched: every JSON-LD block
    that parses (``json_ld``, a list of the parsed objects) and the namespaced
    ``<meta>`` tags such as og:title or product:price:amount (``meta``, name to
    content). Shop and vendor pages carry price, currency, availability and
    GTIN here, so a caller needs no model to read them. Broken blocks are skipped."""
    import json  # noqa: PLC0415

    out: Dict = {"json_ld": [], "meta": {}}
    if tree is None:
        return out
    for node in tree.xpath('//script[@type="application/ld+json"]'):
        raw = (node.text or "").strip().replace("<![CDATA[", "").replace("]]>", "")
        if not raw:
            continue
        try:
            data = json.loads(raw, parse_constant=lambda _c: None)  # NaN/Infinity -> null
        except ValueError:
            continue
        out["json_ld"].extend(data if isinstance(data, list) else [data])
    for meta in tree.xpath("//meta[@content]"):
        key = (meta.get("property") or meta.get("name") or "").strip()
        if key and _STRUCTURED_META_RE.match(key):
            out["meta"].setdefault(key.lower(), " ".join((meta.get("content") or "").split()))
    return out


def reader_document(html: str, url: str = "", fmt: str = "markdown") -> Dict:
    """Ganze Seite als Dokument (Felder wie app.extract.extract_document).
    Reine Funktion, laeuft im Extraktions-Prozess."""
    try:
        from lxml import html as lxml_html  # noqa: PLC0415

        tree = lxml_html.fromstring(html)
    except Exception:  # noqa: BLE001
        tree = None
    try:
        content = reader_content(html, url, fmt)
    except Exception:  # noqa: BLE001
        content = extract_mod.extract_main_text(html, url)
    if fmt == "markdown":
        try:
            content = inline_links(content, tree, url)
        except Exception:  # noqa: BLE001 - Links sind Beiwerk
            pass
    if content:
        # Leerzeilen-Stapel (leere Formularfelder, Tabellen) wie beim Original auf eine
        content = _BLANKS_RE.sub("\n\n", _TRAIL_WS_RE.sub("\n", content))
    try:
        links = reader_links(tree, url)
    except Exception:  # noqa: BLE001
        links = []
    try:
        structured = structured_data(tree)
    except Exception:  # noqa: BLE001 - Beiwerk
        structured = {"json_ld": [], "meta": {}}
    try:
        title = reader_title(tree, None)
    except Exception:  # noqa: BLE001
        title = None
    # Datum wie das Original nur aus Meta-Angaben (publishedTime), ohne die
    # teure Freitext-Datumssuche (bis 4-5 s auf Forenseiten). Zuletzt, weil
    # htmldate den Baum veraendern darf.
    meta = _reader_metadata(html, url, tree)
    doc = extract_mod._doc(url, meta, content, links)
    if structured["json_ld"] or structured["meta"]:  # leere Seiten bleiben so klein wie vorher
        doc["structured"] = structured
    try:
        from .structured import product_from_items  # noqa: PLC0415

        product = product_from_items(structured["json_ld"])  # aus dem schon geparsten JSON-LD
    except Exception:  # noqa: BLE001 - Beiwerk
        product = None
    if product:
        doc["product"] = product
    if title:
        doc["title"] = title
    return doc


# ---------------------------------------------------------------- Laden


READER_MAX_BYTES = 4_000_000  # ganze Seiten lesen (Wikipedia wurde bei 1 MB abgeschnitten)
_PREFIX = "reader-"


def forget(urls: List[str], fmt: str) -> None:
    pages_mod.forget(urls, fmt, prefix=_PREFIX)


def read_pages_ex(
    urls: List[str], fmt: str = "markdown", timeout: Optional[float] = None,
    progress: Optional[Callable[[int, int, int, Dict[str, int]], None]] = None,
    structured: bool = False,
) -> Tuple[List[Dict], List[Dict], Dict[str, str]]:
    """Wie `read_pages`, plus ein Wörterbuch {url: grund} der gelieferten Seiten,
    die nur duennen Text haben ("gelesen, aber duenn": thin_content oder challenge).
    Das Dokument selbst und der geteilte Cache-Eintrag bleiben unveraendert, der
    Grund wird neben ihnen mitgefuehrt. Fehlschlaege tragen `reason`. Das rohe
    `structured` (JSON-LD, og:/twitter:-Meta) liefert nur `structured=True`."""
    fmt = "markdown" if fmt == "markdown" else "text"
    deadline = time.monotonic() + timeout if timeout else None
    docs, failed = pages_mod.read_pages(
        urls, fmt, timeout, extractor=reader_document, cache_prefix=_PREFIX,
        max_bytes=READER_MAX_BYTES, progress=progress,
    )
    thin_map: Dict[str, str] = {}
    for d in docs:
        d.setdefault("stage", "static")  # which rung of the ladder produced the page
    if render_mod.enabled() or stealth_mod.enabled():
        # Duenne Ergebnisse (JS-Huelle, "Skip to main content", Challenge-Seite) zaehlen
        # fuer die Stufen wie Fehler; scheitern alle, bleibt der statische Text.
        thin = [d for d in docs if _poor(d.get("content") or "")]
        if thin:
            docs = [d for d in docs if d not in thin]
            failed = failed + [{"url": d.get("url"), "error": "Zu wenig Text",
                                "reason": _thin_code(d.get("content") or ""), "static": d}
                               for d in thin]
        if failed:
            docs, failed = _render_failed(docs, failed, fmt, urls, deadline, thin=thin_map)
    if failed and get_settings().tavily_api_key:
        docs, failed = _tavily_failed(docs, failed, fmt, urls, deadline)
    if not structured:  # Kopie ohne den Schluessel, das gecachte Objekt bleibt wie es ist
        docs = [{k: v for k, v in d.items() if k != "structured"} if "structured" in d else d
                for d in docs]
    return docs, failed, thin_map


def read_pages(
    urls: List[str], fmt: str = "markdown", timeout: Optional[float] = None,
    structured: bool = False,
) -> Tuple[List[Dict], List[Dict]]:
    """app.pages.read_pages mit dem Lese-Modus (eigener Cache-Schluessel, 4 MB)."""
    docs, failed, _thin = read_pages_ex(urls, fmt, timeout, structured=structured)
    return docs, failed


TAVILY_TIMEOUT_S = 20.0
TAVILY_MIN_REST_S = 2.0
TAVILY_MAX_URLS = 20  # Tavily Extract nimmt hoechstens 20 Adressen je Aufruf


def _tavily_failed(
    docs: List[Dict], failed: List[Dict], fmt: str, urls: List[str],
    deadline: Optional[float] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Letzte Sicherheitsstufe: Seiten, an denen alle eigenen Stufen gescheitert
    sind, liest Tavily Extract. Laeuft nur mit TAVILY_API_KEY; scheitert der
    Aufruf, bleiben die Fehlschlaege wie sie waren."""
    rest = TAVILY_TIMEOUT_S if deadline is None else min(TAVILY_TIMEOUT_S, deadline - time.monotonic())
    if rest < TAVILY_MIN_REST_S:
        return docs, failed
    wanted = [f["url"] for f in failed if f.get("url")][:TAVILY_MAX_URLS]
    try:
        data = tavily_post("extract", {"urls": wanted, "format": fmt}, rest)
    except SearchBackendError:
        return docs, failed
    got = {}
    for it in data.get("results", []):
        content = (it.get("raw_content") or "").strip()
        if it.get("url") in wanted and content:
            doc = extract_mod._doc(it["url"], {}, content, [])
            doc["source"] = "tavily"
            doc["stage"] = "tavily"
            got[it["url"]] = doc
    for url, doc in got.items():
        pages_mod._docs().put(f"{_PREFIX}{fmt}|{url}", doc)
        docs.append(doc)
    still = [f for f in failed if f.get("url") not in got]
    order = {u: i for i, u in enumerate(urls)}
    docs.sort(key=lambda d: order.get(d.get("url"), len(order)))
    return docs, still


RENDER_MAX = 2  # hoechstens so viele Seiten je Anfrage nachrendern (je 2-10 s)
RENDER_TIMEOUT_S = 12.0  # ohne Frist des Aufrufers (x-timeout)
DIRECT_TIMEOUT_S = 15.0  # Stufe 3: Challenge-Seiten brauchen ein paar Sekunden
RENDER_MIN_REST_S = 1.0  # weniger Restzeit lohnt keinen Browserstart
DIRECT_MIN_REST_S = 3.0
RENDER_MIN_WORDS = 40
STEALTH_MAX = 1  # Camoufox hoechstens einmal je Anfrage (Prozessstart, 4 bis 8 s)
STEALTH_TIMEOUT_S = 15.0
STEALTH_MIN_REST_S = 6.0
_CHALLENGE_RE = re.compile(
    r"verifying (?:you are|your browser)|just a moment|enable javascript and cookies"
    r"|captcha|access denied|couldn.t load", re.IGNORECASE)


def _poor(content: str) -> bool:
    """Kein brauchbarer Text: zu kurz oder (kurz und) Challenge-/JS-Hinweis."""
    n = _words(content)
    return n < RENDER_MIN_WORDS or (n < 300 and bool(_CHALLENGE_RE.search(content)))


def _thin_code(content: str) -> str:
    """challenge (Bot-Schutz-Text) oder thin_content (schlicht zu wenig Text)."""
    n = _words(content)
    return "challenge" if n < 300 and _CHALLENGE_RE.search(content or "") else "thin_content"


def _render_failed(
    docs: List[Dict], failed: List[Dict], fmt: str, urls: List[str],
    deadline: Optional[float] = None, thin: Optional[Dict[str, str]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Reserve-Stufen fuer Fehlschlaege, jede nur, wenn die vorige scheitert:
    Stufe 2 rendert im geteilten Browser (alle Anfragen ueber httpx), Stufe 3 laesst
    den Browser die Seite selbst laden (gegen Bot-Schutz, render.try_render_direct),
    Stufe 4 ist Camoufox (stealth.py), hoechstens einmal je Anfrage.
    Bot-Schutz-Seiten (Challenge-Text, kaum Woerter) zaehlen weiter als Fehler.
    Mit `deadline` (monotonic, aus x-timeout) bekommt jede Stufe nur die Restzeit;
    ist ein Render-Platz belegt, wird nach kurzer Wartezeit uebersprungen.
    Behaelt eine Seite nur ihren duennen statischen Text, steht ihr Grund in `thin`."""
    still: List[Dict] = []
    tried = 0  # echte Render-Versuche (Zeitlimit-Fehler und belegter Platz zaehlen nicht)
    stealth_tried = 0
    busy = not render_mod.enabled()
    stealth_busy = not stealth_mod.enabled()

    def rest(cap: float) -> float:
        return cap if deadline is None else min(cap, deadline - time.monotonic())

    def give_up(fail: Dict) -> None:
        static = fail.pop("static", None)
        if static and (static.get("content") or "").strip():
            docs.append(static)  # duenner Text ist besser als nichts
            if thin is not None and fail.get("url"):
                thin[fail["url"]] = _thin_code(static.get("content") or "")
        else:
            still.append(fail)

    def good(html: Optional[str], url: str, stage: str) -> Optional[Dict]:
        doc = reader_document(html, url, fmt) if html else None
        if doc and not _poor(doc.get("content") or ""):
            doc["stage"] = stage
            return doc
        return None

    for fail in failed:
        if (fail.get("error") == "Zeitlimit" or pdf_mod.is_pdf_failure(fail)
                or rest(RENDER_TIMEOUT_S) < RENDER_MIN_REST_S):
            give_up(fail)
            continue
        doc = None
        if not busy and tried < RENDER_MAX:
            status, html = render_mod.try_render(fail["url"], timeout=rest(RENDER_TIMEOUT_S))
            if status == "tried":
                tried += 1
                doc = good(html, fail["url"], "browser")
                if doc is None and rest(DIRECT_TIMEOUT_S) >= DIRECT_MIN_REST_S:
                    status, html = render_mod.try_render_direct(fail["url"], timeout=rest(DIRECT_TIMEOUT_S))
                    doc = good(html, fail["url"], "browser-direct") if status == "tried" else None
            else:
                # Platz belegt: die uebrigen nicht auch noch je 1 s warten lassen.
                busy = status in ("busy", "off")
        if (doc is None and not stealth_busy and stealth_tried < STEALTH_MAX
                and rest(STEALTH_TIMEOUT_S) >= STEALTH_MIN_REST_S):
            status, html = stealth_mod.try_stealth(fail["url"], timeout=rest(STEALTH_TIMEOUT_S))
            if status == "tried":
                stealth_tried += 1
                doc = good(html, fail["url"], "stealth")
            else:
                stealth_busy = status in ("busy", "off")
        if doc is None:
            give_up(fail)
            continue
        pages_mod._docs().put(f"{_PREFIX}{fmt}|{fail['url']}", doc)
        docs.append(doc)
    order = {u: i for i, u in enumerate(urls)}
    docs.sort(key=lambda d: order.get(d.get("url"), len(order)))
    return docs, still
# Stand: 24.10.2026
