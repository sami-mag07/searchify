# 15 Serper- und Tavily-Extract-Kompat: Spec (Stand 11.10.2026)

Clean-room: nur oeffentliche Doku (serper.dev, docs.tavily.com) und Drittclients als Spec, siehe research/14. Kein Call an Serper oder Tavily. Code: `app/compat/serper.py`, `app/compat/tavily_extract.py`; Tests: `tests/test_compat_serper.py` (15 Funktionen, 21 Faelle), `tests/test_compat_tavily_extract.py` (8), offline.

## 1. Serper: `POST /serper/search`, `POST /serper/news`
- Auth: `X-API-KEY` (auch `Authorization: Bearer`) ueber `app.auth.require_key`, nur aktiv mit `SERVICE_API_KEY`. Fehler 401 als `{"message": "Unauthorized.", "statusCode": 401}`.
- Body: Objekt oder Array von Objekten (Batch, Antwort als Array in gleicher Reihenfolge, max. 100; ein ungueltiger Eintrag ergibt 400 fuer den ganzen Batch, bevor gesucht wird).
- Felder: `q` (Pflicht), `gl`, `hl`, `num` (1..100, Default 10), `page` (ab 1), `tbs` (`qdr:h|d|w|m|y`). `location`, `autocorrect`, `type` werden angenommen und ignoriert. Eigene Erweiterung `extras: {"links": true}`.
- Antwort search: `searchParameters` (q, gl, hl, type, num, page, engine, tbs?, language?), `knowledgeGraph?` (title, website?, imageUrl?, description, descriptionSource, descriptionLink, attributes?), `answerBox?` (answer, link?), `organic[]` (title, link, snippet, position, date?), `peopleAlsoAsk: []`, `relatedSearches?` (query), `credits: 0`.
- Antwort news: `searchParameters`, `news[]` (title, link, snippet, source, position, date?, imageUrl?), `credits: 0`.
- Fehler: 400 `{"message", "statusCode"}` bei Body-Fehlern, 502 wenn SearXNG nicht antwortet (Details nur im Log).

### Mapping
| Serper | SearXNG |
|---|---|
| hl + gl | `language=hl-GL` (es+mx -> `es-MX`, en+uk -> `en-GB`) |
| nur hl | `language=hl` (`es`) |
| nur gl | Sprache des Landes + Land (`fr` -> `fr-FR`, `mx` -> `es-MX`, `at` -> `de-AT`) |
| nichts | keine `language`: SearXNG erkennt die Sprache der Frage selbst |
| tbs qdr:d/w/m/y | `time_range` day/week/month/year |
| tbs qdr:h | `time_range=day` (SearXNG kennt keine Stunde) |
| num/page | Offset `(page-1)*num`, ab SearXNG-Seite `offset//10+1`, Folgeseiten parallel (6 Threads), max. 10 Seiten |
| search | `engines=bing,yahoo,wikipedia,yandex`, bei Operatoren (`"`, `site:`, `OR`, `filetype:`, `intitle:`, `inurl:`) ohne Yandex; kein `categories` |
| Reihenfolge (Top 30) | ab Platz 1 sammeln (ohne Yandex eine Extraseite), Basisrang je Seite aus Bing/Yahoo-Positionen (Yandex nur Kandidat), RRF k=30 mit Cross-Encoder-Rang (Titel + Snippet), dann je Host ein Treffer vorn; ab Platz 31 rohe SearXNG-Folge |
| news | `categories=news` |
| Infobox[0] | knowledgeGraph |
| answers[0] | answerBox |
| suggestions[:8] | relatedSearches |

### Gleich
Pfade (unter `/serper/`), Header, Batch, Feldnamen, Positionen fortlaufend ueber Seiten, `num`-Grenzen.

### Fehlt / anders
- Treffer kommen aus Bing/Yahoo/Wikipedia, nicht Google (research/14).
- `peopleAlsoAsk` immer leer, `sitelinks`, `topStories`, organic-`attributes` fehlen. `answerBox` nur, wenn SearXNG eine Antwort hat.
- `qdr:h` wird auf einen Tag gerundet.
- News: Die lokale `deploy/searxng/settings.yml` hat keine News-Engine (`keep_only: bing, yahoo, wikipedia`). SearXNG faellt dann still auf allgemeine Treffer zurueck. Der Endpunkt erkennt das an `category != "news"`, sucht allgemein mit `time_range=month` (oder dem angefragten `tbs`), setzt `searchParameters.fallback = "general"` und fragt 10 Minuten lang keine News-Kategorie mehr ab. Live gemessen: ohne News-Engine kein Datum je Treffer.
- Fehlerform 401 statt Serpers 403 (Status kommt aus app/auth.py).
- Weitere Serper-Endpunkte (images, maps, places, videos, shopping, scholar, patents, autocomplete) fehlen.

### Besser
- Kostenlos: `credits: 0`, auch fuer num > 10 (Serper: 2 Credits).
- Mehrsprachig en/de/es/fr: `hl`/`gl` landen als echte SearXNG-Sprache plus Region; ohne Angabe Spracherkennung statt starrem en-US. Live: de-DE, es-ES, fr-FR lieferten jeweils Treffer in der Sprache (Wikipedia DE/FR, ES-Fachseite auf Platz 1).
- Datum als ISO `YYYY-MM-DD` statt "2 days ago".
- Tracking-Parameter (`utm_*`, `fbclid`, `gclid`, `msclkid`, ...) aus Links entfernt, Dubletten ueber Seitengrenzen entfernt.
- `searchParameters.language` zeigt, was wirklich an das Backend ging.
- `extras.links`: Top-3 werden ueber `app.pages.read_pages` geladen (SSRF-Schutz, Seiten-Cache), `links[]` haengt an den Treffern.
- Reihenfolge: Gold-Treffer stehen weiter vorn als bei Serper (Historien-Bench 11.10.2026, queries mrr 0.30 gegen Serper 0.27, site@10 0.10 gegen 0.12, 0.7 s gegen 1.6 s). Bing liefert ueber SearXNG ab Seite 2 nichts, deshalb Yandex als Kandidatenquelle.
- Ergebnis-Cache (TTL `SEARCH_CACHE_TTL_S`, News/Zeitfilter ein Zehntel), leere Antworten werden nicht gecacht.

## 2. Tavily Extract: `POST /extract`
- Auth: `api_key` im Body oder `Authorization: Bearer`, ueber `require_key`.
- Body: `urls` (String oder Liste, max. 20, Dubletten entfernt), `extract_depth` basic|advanced, `format` markdown|text (Default markdown), `include_images`, `include_favicon`, `include_usage`, `timeout` (0..60 s), eigene Erweiterung `include_metadata`.
- Antwort: `results[{url, raw_content, images: [], favicon?}]`, `failed_results[{url, error}]`, `response_time`, `request_id`, `usage?{credits: 0}`.
- Fehler: 400 `{"detail": {"error": ...}}` bei leerer oder zu langer Liste, 422 bei falschen Enum-Werten, 401 bei falschem Key.

### Gleich
Feldnamen, Default-Format markdown, Grenze 20 URLs, Teilfehler landen in `failed_results` statt die Anfrage zu kippen, Reihenfolge wie Eingabe.

### Fehlt / anders
- `include_images`: angenommen, `images` bleibt immer leer (Extraktion sammelt keine Bilder).
- `favicon` ist `https://<host>/favicon.ico`, nicht aus `<link rel=icon>` gelesen und nicht geprueft.
- `advanced` heisst hier nur laengeres Zeitlimit (max(3x FETCH_TIMEOUT, 15 s)), kein JS-Rendering, keine Tabellen-Sonderbehandlung.
- Fehlertexte sind deutsch ("Seite nicht ladbar ...", "Ungueltige URL").

### Besser
- Kostenlos, kein Credit-Verbrauch.
- `include_metadata: true` liefert `title`, `published`, `author`, `description`, `links`.
- Mehrsprachig: UTF-8 bleibt heil (Test mit FR/ES-Fixture durch die echte trafilatura-Extraktion: « », €, é, ñ, ¿). Live: fr.wikipedia Tour Eiffel (103k Zeichen) und es.wikipedia Fotosintesis (60k) in 1,31 s fuer beide.
- Cache je (URL, Format) aus app/pages.py, SSRF-Schutz aus app/extract.fetch_url.

## 3. Live-Rauchtest 11.10.2026 (6 Anfragen, je 1,1 s Pause, lokales SearXNG)
| Anfrage | Status | Latenz |
|---|---|---|
| search de/de "Hauptstadt von Australien" | 200, 10 Treffer, Canberra Platz 1 | 1,31 s |
| search es/es "que es la fotosintesis" | 200, 10 | 1,00 s |
| search gl=fr num=20 "tour eiffel hauteur" | 200, 20 | 1,29 s |
| news de/de "Bundestag" | 200, 10 (allgemeine Treffer, siehe oben) | 0,90 s |
| search + extras.links | 200, 25 Links an Platz 1 | 1,22 s |
| extract 2 URLs FR/ES | 200, beide ok | 1,31 s |

Die News-Erkennung (`category`) kam nach dem Rauchtest dazu und ist nur offline getestet.

## 4. Offen
- Router in `app/main.py` registrieren (andere Session).
- Fuer echte News eine News-Engine in `deploy/searxng/settings.yml` aufnehmen (z. B. `bing news`, `yahoo news`), dann greift `categories=news` ohne Ersatz.
- Bilder fuer `include_images` und echtes Favicon aus dem HTML.
