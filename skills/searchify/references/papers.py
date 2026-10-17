#!/usr/bin/env python3
"""Paper-Suche fuer wissenschaftliche Literatur. Privat, nicht ins Skeleton.

Nur stdlib. Fragt OpenAlex, Semantic Scholar und arXiv parallel ab, fuehrt
Dubletten zusammen (DOI, arXiv-ID, Titel) und sortiert nach Mehrfachfund und
Zitationen. Dazu Schneeball (refs/cites) und PDF-Download fuer Open Access.

  papers.py search "silent speech interface emg" --since 2018 --limit 15
  papers.py refs  DOI:10.1145/3172944.3172977     # was zitiert das Paper
  papers.py cites ARXIV:2010.02960                # wer zitiert das Paper
  papers.py pdf   DOI:10.xxxx/yyy -o paper.pdf    # Open-Access-PDF laden

Optional in der Umgebung: S2_API_KEY (hoeheres Semantic-Scholar-Limit).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

UA = "searchify-papers/1.0"
TIMEOUT = 25
S2_FIELDS = "title,year,venue,citationCount,externalIds,openAccessPdf,abstract,url"


def _get(url, headers=None, raw=False):
    h = {"User-Agent": UA}
    if "semanticscholar" in url and os.environ.get("S2_API_KEY"):
        h["x-api-key"] = os.environ["S2_API_KEY"]
    h.update(headers or {})
    for attempt in range(3):  # Semantic Scholar ohne Key antwortet oft 429
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=TIMEOUT) as r:
                data = r.read()
            return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def _paper(title, year, venue, cites, doi, arxiv, pdf, url, abstract, engine):
    return {"title": (title or "").strip(), "year": year, "venue": venue or "",
            "citations": cites or 0, "doi": (doi or "").lower().removeprefix("https://doi.org/"),
            "arxiv": arxiv or "", "pdf": pdf or "", "url": url or "",
            "abstract": (abstract or "").strip(), "engines": [engine]}


def _openalex_abstract(inv):
    if not inv:
        return ""
    pos = {i: w for w, idx in inv.items() for i in idx}
    return " ".join(pos[i] for i in sorted(pos))


def openalex(q, since, limit):
    params = {"search": q, "per-page": limit}
    if since:
        params["filter"] = f"from_publication_date:{since}-01-01"
    data = _get("https://api.openalex.org/works?" + urllib.parse.urlencode(params))
    out = []
    for w in data.get("results", []):
        loc = (w.get("primary_location") or {}).get("source") or {}
        arxiv = ""
        for l in w.get("locations") or []:
            m = re.search(r"arxiv\.org/(?:abs|pdf)/([\d.]+)", (l or {}).get("landing_page_url") or "")
            if m:
                arxiv = m.group(1)
        out.append(_paper(w.get("title"), w.get("publication_year"), loc.get("display_name"),
                          w.get("cited_by_count"), w.get("doi"), arxiv,
                          (w.get("open_access") or {}).get("oa_url"), w.get("id"),
                          _openalex_abstract(w.get("abstract_inverted_index")), "openalex"))
    return out


def _s2_to_paper(p, engine="s2"):
    ids = p.get("externalIds") or {}
    return _paper(p.get("title"), p.get("year"), p.get("venue"), p.get("citationCount"),
                  ids.get("DOI"), ids.get("ArXiv"), (p.get("openAccessPdf") or {}).get("url"),
                  p.get("url"), p.get("abstract"), engine)


def semantic_scholar(q, since, limit):
    params = {"query": q, "limit": limit, "fields": S2_FIELDS}
    if since:
        params["year"] = f"{since}-"
    data = _get("https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params))
    return [_s2_to_paper(p) for p in data.get("data") or []]


def arxiv(q, since, limit):
    terms = " AND ".join(f"all:{t}" for t in q.split())
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": terms, "max_results": limit, "sortBy": "relevance"})
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(_get(url, raw=True))
    out = []
    for e in root.findall("a:entry", ns):
        year = int(e.findtext("a:published", "0000", ns)[:4])
        if since and year < since:
            continue
        aid = re.sub(r"v\d+$", "", e.findtext("a:id", "", ns).rsplit("/abs/", 1)[-1])
        out.append(_paper(" ".join(e.findtext("a:title", "", ns).split()), year, "arXiv", 0,
                          "", aid, f"https://arxiv.org/pdf/{aid}", f"https://arxiv.org/abs/{aid}",
                          " ".join(e.findtext("a:summary", "", ns).split()), "arxiv"))
    return out


ENGINES = {"openalex": openalex, "s2": semantic_scholar, "arxiv": arxiv}


def _key(p):
    if p["doi"]:
        return "doi:" + p["doi"]
    if p["arxiv"]:
        return "arxiv:" + p["arxiv"]
    return "t:" + re.sub(r"[^a-z0-9]", "", p["title"].lower())[:80]


def merge(lists):
    """Dubletten ueber DOI, arXiv-ID und Titel zusammenfuehren."""
    by_key, alias = {}, {}
    for p in (p for lst in lists for p in lst if p["title"]):
        keys = {k for k in (_key(p), "t:" + re.sub(r"[^a-z0-9]", "", p["title"].lower())[:80])}
        hit = next((alias[k] for k in keys if k in alias), None)
        if hit is None:
            hit = _key(p)
            by_key[hit] = p
        else:
            m = by_key[hit]
            m["engines"] = sorted(set(m["engines"] + p["engines"]))
            m["citations"] = max(m["citations"], p["citations"])
            for f in ("doi", "arxiv", "pdf", "venue", "abstract", "year"):
                m[f] = m[f] or p[f]
        for k in keys:
            alias[k] = hit
    return list(by_key.values())


def cmd_search(a):
    errors = []

    def run(name):
        try:
            return ENGINES[name](a.query, a.since, a.limit)
        except Exception as e:  # eine tote Quelle soll die Suche nicht abbrechen
            errors.append(f"{name}: {e}")
            return []

    with ThreadPoolExecutor(len(ENGINES)) as ex:
        results = merge(ex.map(run, a.engines))
    if a.oa:
        results = [p for p in results if p["pdf"]]
    results.sort(key=lambda p: p["citations"] * (1 + 0.5 * (len(p["engines"]) - 1)), reverse=True)
    for e in errors:
        print(f"[warnung] {e}", file=sys.stderr)
    return results


def _s2_list(pid, kind, limit):
    url = (f"https://api.semanticscholar.org/graph/v1/paper/{urllib.parse.quote(pid, safe=':')}/{kind}?"
           + urllib.parse.urlencode({"fields": S2_FIELDS, "limit": limit}))
    side = "citedPaper" if kind == "references" else "citingPaper"
    res = [_s2_to_paper(x[side]) for x in _get(url).get("data") or [] if x.get(side, {}).get("title")]
    return sorted(res, key=lambda p: p["citations"], reverse=True)


def cmd_pdf(a):
    p = _get(f"https://api.semanticscholar.org/graph/v1/paper/{urllib.parse.quote(a.id, safe=':')}?fields={S2_FIELDS}")
    url = (p.get("openAccessPdf") or {}).get("url")
    if not url and (p.get("externalIds") or {}).get("ArXiv"):
        url = f"https://arxiv.org/pdf/{p['externalIds']['ArXiv']}"
    if not url:
        sys.exit("Kein Open-Access-PDF gefunden.")
    with open(a.out, "wb") as f:
        f.write(_get(url, raw=True))
    print(f"{a.out} <- {url}")


def show(papers, as_json, abstracts):
    if as_json:
        print(json.dumps(papers, ensure_ascii=False, indent=1))
        return
    for i, p in enumerate(papers, 1):
        ident = f"DOI:{p['doi']}" if p["doi"] else (f"ARXIV:{p['arxiv']}" if p["arxiv"] else p["url"])
        print(f"{i:>2}. {p['title']} ({p['year']}, {p['venue'] or '?'})")
        print(f"    zit. {p['citations']} | {ident} | {','.join(p['engines'])}"
              + (f" | PDF {p['pdf']}" if p["pdf"] else ""))
        if abstracts and p["abstract"]:
            print("    " + p["abstract"][:400])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--since", type=int)
    s.add_argument("--limit", type=int, default=15, help="pro Quelle")
    s.add_argument("--engines", nargs="+", default=list(ENGINES), choices=list(ENGINES))
    s.add_argument("--oa", action="store_true", help="nur mit frei ladbarem PDF")
    for kind in ("refs", "cites"):
        r = sub.add_parser(kind)
        r.add_argument("id", help="DOI:..., ARXIV:... oder Semantic-Scholar-ID")
        r.add_argument("--limit", type=int, default=30)
    d = sub.add_parser("pdf")
    d.add_argument("id")
    d.add_argument("-o", "--out", required=True)
    for p in (s, *[sub.choices[k] for k in ("refs", "cites")]):
        p.add_argument("--json", action="store_true")
        p.add_argument("--abstracts", action="store_true")
    a = ap.parse_args()

    if a.cmd == "pdf":
        return cmd_pdf(a)
    if a.cmd == "search":
        papers = cmd_search(a)
    else:
        papers = _s2_list(a.id, "references" if a.cmd == "refs" else "citations", a.limit)
    show(papers, a.json, a.abstracts)


if __name__ == "__main__":
    main()
