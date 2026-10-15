# 15 Jina Reader und Search: Spec des Nachbaus

Stand 11.10.2026. Clean-room: Grundlage nur die oeffentliche Doku
(jina.ai/reader, README von github.com/jina-ai/reader). Kein Jina-Output als
Vorlage, keine Anfrage an r.jina.ai oder s.jina.ai. Code: `app/compat/jina.py`,
Tests: `tests/test_compat_jina.py` (25 Tests, offline).

## Endpunkte

| Original | Nachbau | Eingabe |
|---|---|---|
| `GET r.jina.ai/{url}` | `GET /jina/r/{url}` | Ziel-URL im Pfad, Query-String der Ziel-URL bleibt erhalten |
| `POST r.jina.ai` `{"url"}` | `POST /jina/r` `{"url"}` | JSON-Body |
| `GET s.jina.ai/{query}` | `GET /jina/s/{query}` oder `GET /jina/s?q=` | Suchanfrage im Pfad |
| `POST s.jina.ai` `{"q"}` | `POST /jina/s` `{"q", "hl"?}` | JSON-Body |

Router: `app.compat.jina.router`, Registrierung in `app/main.py` macht die
andere Session.

## Header

| Header | Wirkung |
|---|---|
| `Authorization: Bearer <key>` | Pflicht nur, wenn `SERVICE_API_KEY` gesetzt ist (app/auth.require_key) |
| `Accept: application/json` | JSON `{code:200, status:20000, data:{title,url,content,description,publishedTime,links?}}`, Search: `data:[{title,url,description,content,publishedTime?}]` |
| `X-Return-Format: markdown \| text` | Default markdown (trafilatura), text = reiner Haupttext |
| `X-With-Links-Summary: true` | Text: Abschnitt `Links/Buttons:` mit `- [Text](URL)`; JSON: `data.links` als Objekt `{Text: URL}` |
| `X-Timeout: <s>` | Ladezeit je Seite, gedeckelt auf 1 bis 15 s |
| `X-No-Cache: true` | Seiten-Cache-Eintrag wird vorher verworfen |
| `Accept-Language`, `X-Locale`, Param `hl`/`language` | Nur Search: Sprache en/de/es/fr, Reihenfolge Body/Param vor Header vor Auto-Erkennung |

## Antwortformat (Text)

Reader:

```
Title: …

URL Source: …

Published Time: …        (nur wenn bekannt)

Markdown Content:
…

Links/Buttons:           (nur mit X-With-Links-Summary)
- [Text](URL)
```

Search: je Treffer `[n] Title`, `[n] URL Source`, `[n] Description`,
`[n] Published Time` (falls bekannt), `[n] Markdown Content:` plus Inhalt,
Bloecke durch Leerzeile getrennt. Immer `text/plain; charset=utf-8`.

## Fehler

JSON im Jina-Stil `{data:null, code, name, status, message, readableMessage}`.

| Fall | Code |
|---|---|
| interne Adresse (localhost, 127/8, 10/8, 169.254/16, .local, .internal), anderes Schema als http(s) | 400 `SecurityCompromiseError` / `ParamValidationError` |
| Domain nicht aufloesbar, Seite nicht ladbar, kein Haupttext | 422 `AssertionFailureError` |
| Such-Backend weg | 502 |
| falscher Key | 401 (FastAPI-Standard `{"detail"}`) |

Die Vorpruefung liefert nur den klaren Code. Der eigentliche SSRF-Schutz
(jeder Redirect, DNS-Rebinding per IP-Pinning) bleibt in `app/extract.fetch_url`.

## Was gleich ist

- Pfade mit URL bzw. Query im Pfad, POST-Varianten, Bearer-Auth.
- Textformat mit Title / URL Source / Published Time / Markdown Content.
- JSON-Huelle `code` / `status` / `data`.
- Header Accept, X-Return-Format (markdown, text), X-With-Links-Summary,
  X-Timeout, X-No-Cache.
- Search: Top 5, jeder Treffer mit vollem Seiteninhalt.

## Was fehlt

- Kein JS-Rendering (kein Headless-Browser): SPAs liefern wenig oder 422.
- Keine PDFs (fetch_url nimmt nur HTML/Text), keine Bild-Captions
  (`X-With-Generated-Alt`), kein `X-Return-Format: html | screenshot | pageshot`.
- Kein `X-Target-Selector`, `X-Wait-For-Selector`, `X-Remove-Selector`,
  `X-Proxy-Url`, `X-Set-Cookie`, kein Streaming (`text/event-stream`).
- Kein `X-With-Images-Summary`, kein `usage.tokens` im JSON.
- Search: kein `site=`-Filter und kein `gl`; Engine-Reihenfolge statt
  eigenem Ranking.

## Was besser ist

- Kostenlos und lokal, ohne Rate-Limit und ohne Token-Abrechnung.
- Mehrsprachig EN/DE/ES/FR: Sprache per `hl`, `language`, `X-Locale` oder
  `Accept-Language`; ohne Angabe erkennt eine kleine Stoppwort- und
  Zeichen-Heuristik Deutsch, Spanisch und Franzoesisch und reicht die Sprache
  an SearXNG durch. Englisch und Unklares bleiben auf auto.
- Akzente und Umlaute sauber als UTF-8 (Test mit FR- und ES-Fixture,
  live gegen fr.wikipedia.org/wiki/Montréal geprueft).
- Aktions-Links (Anmelden, Bewerben, Formular) stehen in der Linkliste
  zuerst; ES/FR-Woerter dafuer kommen zentral in `app/extract.py`.
- Seiten-Cache (LRU + TTL) fuer Reader und Search gemeinsam mit den anderen
  kompatiblen Endpunkten.
- Search: Seiten, die nicht laden, liefern das Snippet als `content` statt
  leerer Treffer.

## Live-Rauchtest (11.10.2026, 6 Anfragen, lokales SearXNG)

| Anfrage | Status | Latenz |
|---|---|---|
| Reader de.wikipedia.org/wiki/Berlin (ohne Schema) | 200 | 0.88 s |
| Reader fr.wikipedia Montréal, JSON + Links | 200 | 1.55 s |
| Reader http://127.0.0.1:8888/ | 400 | 0.01 s |
| Search DE "wie hoch ist der Berliner Fernsehturm" | 200 | 5.86 s |
| Search ES "¿Cuál es la capital de Argentina?" | 200 | 4.86 s |
| Search EN "python asyncio tutorial" | 200 | 3.18 s |

Auffaellig: Bei der DE-Suche brach SearXNG einmal mit RemoteProtocolError ab,
der ddgs-Fallback lieferte einen unpassenden Treffer 1 (Musikvideo "Wie?").
Search-Latenz wird von der langsamsten der 5 Seiten bestimmt (bis zum
Fetch-Timeout); Wikipedia-Seiten kommen ungekuerzt (bis 150k Zeichen).

## Lese-Modus (seit 11.10.2026, app/compat/reader.py)

/jina/r und /extract nutzen nicht mehr den knappen Such-Haupttext, sondern einen
eigenen Lese-Modus (Search /jina/s bleibt auf app/pages, wegen Tempo und Frist):

- Inhalt: trafilatura `favor_recall=True` (Markdown bzw. txt). Hat der Haupttext
  weniger als 40 % der Woerter der ganzen Seite (Teaser-Startseiten, Programmseiten
  aus vielen Kaesten), kommt der Text der ganzen Seite (resiliparse ohne
  main_content, mit Listenpunkten). Offline auf 40 URLs: Schwelle 0.3 bis 0.5
  gleich gut, 0.6 drueckt Precision auf 84 %.
- Titel: og:title, sonst `<title>`, Suffix " - Wikipedia..." entfernt (so wie das Original).
- Links: bis 1500, inkl. Links auf die Seite selbst (oft der Button), Aktions-Links zuerst.
- Cache: eigener Schluessel `reader-<fmt>|<url>` im Dokument-Cache, X-No-Cache verwirft nur ihn.

Messung (eval/reader_bench.py, 40 URLs, Original aus Cache): Recall 66 -> 78 %,
Precision 92 %, Titel gleich 45 -> 82 %, Aktions-Links 67 -> 81 %, Links Median
25 -> 64 (Original 66), 0.25 s statt 3.8 s. Rest-Luecken: Boilerplate des Originals,
SPAs ohne JS-Rendering (healthcare-hackathon.de), Bot-Schutz (lemonde.fr), Seiten
ueber 1 MB HTML (Kern schneidet ab, Wikipedia Paris/Berlin).
