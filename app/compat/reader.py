"""Lese-Modus fuer die Reader-Endpunkte (/jina/r und /extract).

Die Such-Pipeline (app/extract.py) will knappen Haupttext. Ein Reader soll wie
r.jina.ai die ganze Seite lesbar machen. Darum hier eine eigene Extraktion:

- Inhalt: trafilatura mit favor_recall (Markdown oder Text). Erwischt der
  Haupttext weniger als FULL_PAGE_RATIO der Woerter der ganzen Seite (Startseiten
  mit Teasern, Programmseiten aus vielen Kaesten), kommt stattdessen der Text der
  ganzen Seite (resiliparse ohne Haupttext-Filter).
- Titel: og:title, sonst <title>, ohne "- Wikipedia"-Suffix (wie das Original).
- Links: alle Links der Seite (bis MAX_LINKS) inkl. Links auf die Seite selbst,
  Aktions-Links zuerst.

`read_pages` entspricht app.pages.read_pages (SSRF-Schutz und Groessengrenzen
ueber app.extract.fetch_url, Extraktion im Prozess-Pool), cached aber unter
eigenem Schluessel "reader-<fmt>|<url>", damit die knappen Dokumente der Suche
und die vollstaendigen des Readers sich nicht vermischen.
"""

from __future__ import annotations

import re
import time
from typing import Dict, List, Optional, Tuple

from .. import extract as extract_mod
from .. import pages as pages_mod
from . import render as render_mod

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
        for head in tree.xpath("//h1|//h2|//h3|//h4|//h5|//h6"):
            head.text = "#" * int(head.tag[1]) + " " + (head.text or "").lstrip()
    return _full_page_text(HTMLTree.parse(lxml_html.tostring(tree, encoding="unicode")))


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
        title = reader_title(tree, None)
    except Exception:  # noqa: BLE001
        title = None
    # Datum wie das Original nur aus Meta-Angaben (publishedTime), ohne die
    # teure Freitext-Datumssuche (bis 4-5 s auf Forenseiten). Zuletzt, weil
    # htmldate den Baum veraendern darf.
    meta = _reader_metadata(html, url, tree)
    doc = extract_mod._doc(url, meta, content, links)
    if title:
        doc["title"] = title
    return doc


# ---------------------------------------------------------------- Laden


READER_MAX_BYTES = 4_000_000  # ganze Seiten lesen (Wikipedia wurde bei 1 MB abgeschnitten)
_PREFIX = "reader-"


def forget(urls: List[str], fmt: str) -> None:
    pages_mod.forget(urls, fmt, prefix=_PREFIX)


def read_pages(
    urls: List[str], fmt: str = "markdown", timeout: Optional[float] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """app.pages.read_pages mit dem Lese-Modus (eigener Cache-Schluessel, 4 MB)."""
    fmt = "markdown" if fmt == "markdown" else "text"
    deadline = time.monotonic() + timeout if timeout else None
    docs, failed = pages_mod.read_pages(
        urls, fmt, timeout, extractor=reader_document, cache_prefix=_PREFIX,
        max_bytes=READER_MAX_BYTES,
    )
    if failed and render_mod.enabled():
        docs, failed = _render_failed(docs, failed, fmt, urls, deadline)
    return docs, failed


RENDER_MAX = 2  # hoechstens so viele Seiten je Anfrage nachrendern (je 2-10 s)
RENDER_TIMEOUT_S = 12.0  # ohne Frist des Aufrufers (x-timeout)
RENDER_MIN_REST_S = 1.0  # weniger Restzeit lohnt keinen Browserstart
RENDER_MIN_WORDS = 40
_CHALLENGE_RE = re.compile(
    r"verifying (?:you are|your browser)|just a moment|enable javascript and cookies"
    r"|captcha|access denied|couldn.t load", re.IGNORECASE)


def _render_failed(
    docs: List[Dict], failed: List[Dict], fmt: str, urls: List[str],
    deadline: Optional[float] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Reserve fuer JS-Seiten: fehlgeschlagene URLs im Headless-Browser rendern.
    Bot-Schutz-Seiten (Challenge-Text, kaum Woerter) zaehlen weiter als Fehler.
    Mit `deadline` (monotonic, aus x-timeout) bekommt jeder Render nur die Restzeit;
    ist der Render-Platz belegt, wird nach kurzer Wartezeit uebersprungen."""
    still: List[Dict] = []
    tried = 0  # echte Render-Versuche (Zeitlimit-Fehler und belegter Platz zaehlen nicht)
    busy = False
    for fail in failed:
        rest = RENDER_TIMEOUT_S if deadline is None else deadline - time.monotonic()
        if (busy or tried >= RENDER_MAX or fail.get("error") == "Zeitlimit"
                or rest < RENDER_MIN_REST_S):
            still.append(fail)
            continue
        status, html = render_mod.try_render(fail["url"], timeout=min(rest, RENDER_TIMEOUT_S))
        if status != "tried":
            # Platz belegt: die uebrigen nicht auch noch je 1 s warten lassen.
            busy = busy or status in ("busy", "off")
            still.append(fail)
            continue
        tried += 1
        doc = reader_document(html, fail["url"], fmt) if html else None
        content = (doc or {}).get("content") or ""
        if _words(content) < RENDER_MIN_WORDS or (
                _words(content) < 300 and _CHALLENGE_RE.search(content)):
            still.append(fail)
            continue
        pages_mod._docs().put(f"{_PREFIX}{fmt}|{fail['url']}", doc)
        docs.append(doc)
    order = {u: i for i, u in enumerate(urls)}
    docs.sort(key=lambda d: order.get(d.get("url"), len(order)))
    return docs, still
