# 15 Exa-kompatible Schnittstelle (Stand 11.10.2026)

Clean-room: Grundlage ist nur die oeffentliche Exa-Doku (OpenAPI unter
`https://exa.ai/docs/exa-spec.yaml`, Seiten `docs.exa.ai/reference/search`,
`/search/highlights`). Kein Exa-Key benutzt, keine Anfrage an Exa gesendet,
keine Exa-Ausgabe als Vorlage. Code: `app/compat/exa.py`, Tests:
`tests/test_compat_exa.py` (offline).

## Endpunkte

| Unser Pfad | Exa | Zweck |
|---|---|---|
| `POST /exa/search` | `POST /search` | Suche, optional mit Inhalten |
| `POST /exa/contents` | `POST /contents` | Inhalte fuer bekannte URLs |
| `POST /exa/findSimilar` | `POST /findSimilar` (bei Exa inzwischen deprecated) | aehnliche Seiten zu einer URL |

Auth: wie alle Endpunkte ueber `app.auth.require_key` (nur aktiv mit
`SERVICE_API_KEY`), Header `x-api-key` oder `Authorization: Bearer`, genau wie Exa.

## Request-Felder

Gemeinsam fuer search und findSimilar:

| Feld | Umsetzung |
|---|---|
| `numResults` | Default 10, still auf 1..25 begrenzt (Exa: bis 100) |
| `includeDomains` / `excludeDomains` | Host, Host+Pfad (`example.com/docs`), `*.example.com`, auch mit `https://`. Host geht als `site:` ans Backend, Pfad wird danach exakt geprueft |
| `startPublishedDate` / `endPublishedDate` | Datum aus dem Seiten-Dokument (`published`, trafilatura). Ausserhalb: raus. **Ohne Datum: bleibt drin, hinten angestellt.** Startdatum wird zusaetzlich als SearXNG-`time_range` (day/week/month/year) vorgegeben |
| `includeText` / `excludeText` | wortweise wie bei Exa beschrieben (nicht als exakte Phrase) |
| `category` | `news` -> News-Kategorie; `research paper`/`publication` -> 6 Fachdomains (arXiv, Semantic Scholar, ...), `github`, `tweet`, `linkedin profile` -> passende Domains; `pdf` -> `filetype:pdf`; `company`, `people`, `personal site`, `financial report` werden angenommen, wirken aber nicht |
| `userLocation` | Laendercode (String oder `{country}`) -> `country` |
| `contents.text` | `true` oder `{maxCharacters}`; `includeHtmlTags`, `verbosity`, `includeSections` ignoriert |
| `contents.highlights` | `true` oder `{query, numSentences, highlightsPerUrl, maxCharacters}` |
| `contents.summary` | `true` oder `{query}`; `schema` ignoriert |
| `contents.extras.links` | Zahl (oder `true` = bis 100) -> `extras.links` als URL-Liste |
| `contents.context` | kombinierter String (bei Exa deprecated) |
| Legacy `text`, `highlights`, `summary`, `extras`, `context` auf oberster Ebene | werden angenommen, `contents` hat Vorrang |
| `language` (eigenes Feld) | `en`, `de`, `es`, `fr` (auch `de-DE`, `Deutsch`, `Français`), sonst automatisch |

Nur search: `query`, `type` (`auto`, `neural`, `keyword`, `fast`, `instant`, `deep*`).
Nur findSimilar: `url`, `excludeSourceDomain` (Default false).
Nur contents: `ids` oder `urls` (max. 100); ohne Optionen kommt `text` wie bei Exa.

Ignoriert (angenommen, ohne Wirkung): `moderation`, `livecrawl`, `maxAgeHours`,
`livecrawlTimeout`, `subpages`, `subpageTarget`, `snapshotAsOf`, `outputSchema`,
`stream`, `systemPrompt`, `objective`, `additionalQueries`, `userIdentifier`,
`compliance`, `extras.imageLinks`.

## Antwort

```
{requestId, resolvedSearchType, results: [{id, url, title, publishedDate, author,
 score, text?, highlights?, highlightScores?, summary?, extras?}],
 searchTime, costDollars: {total: 0}, context?}
```

- `id` = URL (Exa: temporaere Dokument-ID, die /contents versteht; bei uns versteht /contents die URL).
- `publishedDate` als `YYYY-MM-DDT00:00:00.000Z` oder `null`.
- `score` = gestreckter Cross-Encoder-Score 0..1 (eigene Skala, nicht Exas Cosinus).
- `highlightScores` = sigmoid(Logit/T) des Cross-Encoders je Highlight (Exa: Cosinus).
- contents: zusaetzlich `statuses: [{id, status: success|error, source?, error?: {tag, httpStatusCode}}]`,
  Tags `SOURCE_NOT_AVAILABLE`, `CRAWL_TIMEOUT`, `CRAWL_NOT_FOUND`, `INVALID_URL`, `CRAWL_UNKNOWN_ERROR`.
  `httpStatusCode` ist immer `null` (fetch_url gibt den Status nicht heraus).
- Fehler im Exa-Umschlag `{requestId, error, tag}`; Such-Backend weg -> 502 `SEARCH_BACKEND_UNAVAILABLE`.
  Validierungsfehler und 401 kommen im FastAPI-Format.

## Pipeline

- search: `app.main.run_search` (SearXNG/Serper/ddgs, Bing-zuerst-Stream, Cross-Encoder),
  `type` -> `search_depth` (fast/instant -> fast, deep* oder Inhalte gewuenscht -> advanced,
  sonst basic). Danach Seiten-Dokumente ueber `app.pages.read_pages` parallel mit Zeitbudget
  (0,4 s fast, 1,2 s normal, 4 s mit Filtern oder Inhalten). Was nicht rechtzeitig kommt,
  nutzt den Volltext aus run_search bzw. das Snippet; der Download laeuft im Hintergrund
  weiter in den Seiten-Cache. Mit Filtern wird die doppelte Kandidatenzahl geholt.
- Kandidaten-Pool: n+10 (max 30; mit Filtern/Rerank 2n). Danach Domain-Vielfalt
  (`diversify`): je Domain (ohne www.) bleiben die ersten 2 Treffer an ihrem Platz, weitere
  ruecken ans Ende, nichts wird verworfen. Gemessen 11.10.2026 (history_bench): queries
  site@30/mrr 0.10/0.23 -> 0.11/0.27, niche 0.10/0.18 -> 0.12/0.17. Verworfen: advanced
  fuer auto (langsamer, kein Gewinn), RRF mit Backend-Rang (Ankunftsreihenfolge, mrr
  0.27 -> 0.16), 1 statt 2 je Domain (kein Gewinn).
- Highlights: Saetze splitten, Fenster aus `numSentences` Saetzen (gleitend), BM25
  (`rerank.lexical_scores`) waehlt 8 Fenster je Seite, ein Cross-Encoder-Lauf ueber
  alle Seiten (`rerank.rerank`), je Seite die besten nicht ueberlappenden Fenster.
  Default 3 Saetze, 2 Highlights je URL. Ohne Query (contents) fuehrt Titel + Beschreibung.
- Satzzerlegung fuer en/de/es/fr: Satzende nur vor Grossbuchstabe, Ziffer oder
  oeffnendem `¿ ¡ « „ “`, schliessende `» ” )` gehoeren zum Satz (auch franz. `. »`
  und `Vraiment ?`), kein Ende nach Abkuerzungen (z. B., bzw., e.g., Mr., p. ej.,
  Sr., Sra., M., Mme, p. ex., cf. ...), Initialen und Ordnungszahlen ("3. Oktober").
- summary: Exa nutzt ein LLM. Wir nur, wenn `OPENAI_API_KEY` gesetzt ist
  (`app.answer.generate_answer`, Claude ueber claude-code-api, 4000 Zeichen der Seite,
  Quellenmarker entfernt, Sprache der Seite geraten). **Ohne Key fehlt das Feld `summary`.**
- findSimilar: Quellseite lesen, Anfrage = Titel ohne Seitennamen + 4 haeufigste lange
  Nicht-Stoppwoerter (en/de/es/fr), Sprache der Quelle geraten und als `language`
  gesetzt, Suche mit doppelter Kandidatenzahl, Quelle selbst immer raus, Quell-Host
  mit `excludeSourceDomain`. Neu gerankt per Cross-Encoder mit "Titel. Beschreibung"
  der Quelle als Query. Seite nicht lesbar: Begriffe aus dem URL-Pfad.

## Was gleich ist

Pfade (mit `/exa`-Praefix), Feldnamen, Antwortform, Auth-Header, Domain-Syntax,
Datumsfilter, Highlights als Liste mit Scores, statuses bei contents,
Legacy-Felder, Fehler-Umschlag.

## Was fehlt

- **Eigener neuraler Index.** Exa sucht per Embedding in einem eigenen Crawl. Wir haben
  nur Web-Suchmaschinen (lexikalisch) plus neuronales Reranking der Top-Treffer. Darum
  `resolvedSearchType` ehrlich `keyword` (bei `type: keyword`) oder `neural-rerank`; wo
  Exa semantisch weit entfernte Formulierungen findet, die keine Suchmaschine liefert,
  sind wir schwaecher. findSimilar ist entsprechend eine Annaeherung ueber Begriffe.
- numResults ueber 25, `deep`-Synthese (`output`), `outputSchema`, Subpages,
  Bild-Links, strukturierte Entities (company/people), Moderation, Snapshots.
- `publishedDate` nur, wenn die Seite ein Datum traegt (trafilatura); Exa schaetzt auch ohne.
- summary nur mit LLM-Key.

## Was besser ist

- Kostenlos und lokal: `costDollars.total` = 0, keine Anfrage verlaesst den eigenen Rechner
  ausser an die Suchmaschinen; Key optional.
- Vier Sprachen gleichwertig: `language` (en, de, es, fr) wird ans Such-Backend gereicht,
  Highlights funktionieren auf Englisch, Deutsch, Spanisch und Franzoesisch mit eigener
  Satzzerlegung (spanische ¿ ¡, franzoesische « » und Leerzeichen vor ? !, deutsche
  Ordnungszahlen und Abkuerzungen), Cross-Encoder ist mehrsprachig (mMiniLM).
- Fehlende Daten werden beim Datumsfilter nicht verworfen, sondern hinten angestellt.
- Links je Ergebnis aus der eigenen Link-Extraktion (Aktions-Links zuerst).
- Highlight-Scores sind nachvollziehbar (offengelegte Formel), Highlights rein
  extraktiv: jeder Satz steht wirklich auf der Seite.

## Live-Rauchtest 11.10.2026 (SearXNG lokal, 6 Anfragen, Modell vorgewaermt)

| Anfrage | Status | Latenz |
|---|---|---|
| search DE "Hauptstadt von Australien" + highlights | 200 | Latenz nicht mitgeschnitten (Ausgabe abgeschnitten) |
| search EN Python 3.13 + text + startPublishedDate | 200 | 2,58 s |
| search ES "¿Cuál es la capital de Australia?" + highlights | 200 | 2,95 s |
| search FR, `type: fast`, highlights | 200 | 1,73 s |
| contents (Wikipedia + .invalid), highlights | 200 | 0,47 s |
| findSimilar de.wikipedia.org/wiki/Canberra | 200 | 2,28 s |

Alle Top-Highlights in DE/ES/FR enthielten die Antwort (Canberra). Mit Inhalten
dominiert das 4-s-Metadatenbudget die Latenz. findSimilar lieferte im Lauf
Wikipedia-Startseiten, weil der Titel "Canberra – Wikipedia" zu "Wikipedia"
gekuerzt wurde; danach behoben (Teile, die im Hostnamen stehen, fliegen raus),
offline getestet, live noch nicht erneut gemessen.
