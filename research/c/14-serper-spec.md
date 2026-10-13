# Serper API (serper.dev) Black-Box-Spezifikation, Stand 11.10.2026

Methode: nur öffentliche Quellen. Tags: [dokumentiert] = serper.dev selbst (Startseite, Terms, Privacy); [Drittquelle] = Open-Source-Code, Blogs, auto-generierte OpenAPI. Es gibt KEINE öffentliche Doku-Seite (serper.dev/pricing, /faq = 404; Playground nur mit Login). Alles zu Parametern/Fehlern ist daher aus Client-Code rekonstruiert.
Hinweis: Ein einziger unauthentifizierter GET auf google.serper.dev/search (HTTP 403, Statuscheck) ging versehentlich raus. Kein Key, keine Credits berührt.

## 1. Endpunkte

Alle POST, JSON-Body, Header `X-API-KEY: <key>` + `Content-Type: application/json`.

| Pfad | Zweck | Quelle |
|---|---|---|
| https://google.serper.dev/search | Web | [dokumentiert] Startseiten-Tabs https://serper.dev/ ; [Drittquelle] https://github.com/langchain-ai/langchain-community/blob/main/libs/community/langchain_community/utilities/google_serper.py |
| /images /videos /places /maps /news /shopping /scholar /patents /autocomplete | Tabs | [dokumentiert] Startseite (Tabs Search, Images, News, Maps, Places, Videos, Shopping, Scholar, Patents, Autocomplete) |
| /reviews (Maps-Reviews), /lens (Bildsuche per URL) | | [Drittquelle] https://github.com/garylab/serper-mcp-server/blob/main/src/serper_mcp_server/enums.py (nicht auf Startseite) |
| https://scrape.serper.dev (Body `{"url":..}`) | Webseite zu Text/Markdown | [Drittquelle] https://github.com/garylab/serper-mcp-server/blob/main/src/serper_mcp_server/core.py , https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/tools/tool_implementations/web_search/clients/serper_client.py , https://github.com/agno-agi/agno/blob/main/libs/agno/agno/tools/serper.py |

Auth: [Drittquelle] `X-API-KEY` in allen Clients (LangChain, CrewAI, Haystack, Onyx, Agno). Falsch/fehlend: 403 "Unauthorized" laut Clay-Forum (nicht verifiziert, Seite nicht lesbar).

Batch: [Drittquelle] Body als JSON-Array von Param-Objekten an /search, Antwort ist ein Array in gleicher Reihenfolge (https://github.com/openfactcheck-research/openfactcheck/blob/main/src/openfactcheck/integrations/serper/client.py , Methode search_batch). Nicht auf serper.dev dokumentiert. Credit-Abrechnung pro Query vermutlich, ungeprüft.

Besonderheit LangChain: sendet Parameter als URL-Query-String bei POST (`requests.post(url, params=...)`), Server akzeptiert also beides [Drittquelle, google_serper.py oben]. LangChain unterstützt als `type` nur search/news/places/images.

## 2. Parameter

Quelle Schemas: https://github.com/garylab/serper-mcp-server/blob/main/src/serper_mcp_server/schemas.py , https://github.com/marcopesani/mcp-server-serper/blob/main/src/types/serper.ts , https://github.com/MiroMindAI/MiroThinker (serper_mcp_server.py) [alle Drittquelle]

- /search, /images, /videos, /news, /scholar(?): `q` (Pflicht), `gl` (Land, z. B. us), `hl` (Sprache), `location` (Freitext, z. B. "SoHo, New York, United States"), `num` (1-100, Default 10), `page` (>=1, Default 1), `tbs` (qdr:h|d|w|m|y; MCP-Schema nennt auch d/w/m/y), `autocorrect` (bool, Default true).
- Defaults: Haystack setzt gl=us, hl=en, autocorrect=true selbst (https://github.com/deepset-ai/haystack-core-integrations/blob/main/integrations/serperdev/src/haystack_integrations/components/websearch/serperdev/websearch.py). Serverseitiger Default bei Weglassen: ungeprüft.
- /shopping: q, gl, hl, location, page, num, autocorrect. /patents: q, num, page.
- /maps: q, ll (z. B. "@40.75,-73.98,14z"), placeId, cid, gl, hl, page. Antwort enthält `ll` (Startseite).
- /reviews: fid (Pflicht), cid, placeId, sortBy (mostRelevant|newest|highestRating|lowestRating), topicId, nextPageToken, gl, hl.
- /lens: url, gl, hl. /autocomplete: q (+gl, hl).
- scrape: url, includeMarkdown (bool, Default false).
- `type` steckt im Pfad; im Echo `searchParameters.type` steht z. B. "search".

Kosten num:
- [Drittquelle, widersprüchlich] 1 Credit bis 10 Ergebnisse, mehr = 2 Credits: https://coldiq.com/blog/serper-pricing (11-100 = 2), https://scrap.io/serper-dev-vs-scrap-io-google-maps-scraper-comparison (11-100 = 2), HN-Kommentar https://news.ycombinator.com/item?id=43921687 ("10 = 1 Credit, 20 bis 100 = 2 Credits"). Auf serper.dev selbst NICHT zu finden (https://apiserpent.com/blog/serper-pricing-credits-explained bestätigt das ausdrücklich). Einordnung: plausibel, nicht belegt; Schwelle 10 vs 20 unklar.
- Ein Skill nennt "Server max results 20" (https://github.com/code-yeongyu/ultimate-web-search-skill/blob/main/references/providers/serper.md), die MCP-Schemas 100. ColdIQ schreibt, Google habe num=100 im Sept. 2025 entfernt (ungeprüft). Maps liefert ca. 20 pro Seite (scrap.io). Praxis: num>10 nicht blind trauen, selbst messen.

## 3. Antwortformat

### /search [dokumentiert] (echtes Beispiel auf https://serper.dev/ , Query "google search", gekürzt)
```json
{
  "searchParameters": {"q":"...","type":"search","gl":"us","hl":"en","num":10,"page":1,"autocorrect":true},
  "knowledgeGraph": {"title":"Google Search","type":"Website","website":"https://google.com/","imageUrl":"https://encrypted-tbn0.gstatic.com/...","description":"...","descriptionSource":"Wikipedia","descriptionLink":"https://en.wikipedia.org/wiki/Google_Search","attributes":{"Written in":"Python, C, and C++","Category":"Search engine","Date launched":"1998"}},
  "answerBox": {"answer":"?","snippet":"...","snippetHighlighted":["..."],"title":"...","link":"...","date":"?"},
  "organic": [{"title":"Google","link":"https://www.google.com/?gws_rd=ssl","snippet":"...","sitelinks":[{"title":"Images","link":"https://images.google.com/"}],"position":1,
               "date":"2 days ago","attributes":{"Available in":"149 languages","URL":"www.google.com"}}],
  "peopleAlsoAsk": [{"question":"...","snippet":"...","title":"...","link":"..."}],
  "relatedSearches": [{"query":"Google Search Console login"}],
  "topStories": [{"title":"A news item"}],
  "credits": 1
}
```
Belegstatus je Feld: knowledgeGraph, organic (inkl. sitelinks, attributes, position), peopleAlsoAsk, relatedSearches im Startseiten-Beispiel [dokumentiert]. searchParameters (q, type, gl, hl, num, page, autocorrect, location): [Drittquelle] https://github.com/openfactcheck-research/openfactcheck/blob/main/src/openfactcheck/integrations/serper/responses.py und https://github.com/marcopesani/mcp-server-serper/blob/main/src/types/serper.ts ; CrewAI https://github.com/crewAIInc/crewAI (serper_dev_tool.py) mergt `searchParameters` ebenfalls. answerBox (answer, snippet, snippetHighlighted[], title, link; LangChain-Parser prüft in dieser Reihenfolge answer, snippet, snippetHighlighted): [Drittquelle] google_serper.py und openfactcheck tests (test_responses.py). `date` ist menschenlesbar ("2 days ago"), nicht ISO. `topStories` nur als Schlüssel in einem Test-Fixture belegt (Inhalt unbekannt, wahrscheinlich Felder wie bei news). `credits` im Search-Body: nur indirekt (CrewAI liest `results.get("credits", 1)`), kein echtes Beispiel gefunden. Felder fehlen, wenn Google sie nicht zeigt (alle Clients behandeln sie als optional).
Echte Fixture-Suche per GitHub code search scheiterte am Rate-Limit nach den ersten Treffern, daher kein weiteres Roh-JSON mit answerBox/topStories.

### Andere Typen [dokumentiert, Startseite]
- images: `{"images":[{"title","imageUrl","imageWidth","imageHeight","thumbnailUrl","thumbnailWidth","thumbnailHeight","source","domain","link","position"}]}`
- news: `{"news":[{"title","link","snippet","date","source","imageUrl","position"}]}`
- videos: `{"videos":[{"title","link","snippet","imageUrl","duration","source","channel","date","position"}]}`
- places: `{"places":[{"position","title","address","latitude","longitude","rating","ratingCount","category","phoneNumber","website","cid"}]}`
- maps: `{"ll":"@lat,lng,14z","places":[{"position","title","address","latitude","longitude","rating","ratingCount","type","types":[],"website","phoneNumber","description","thumbnailUrl","cid","placeId"}]}`
- shopping: `{"shopping":[{"title","source","link","price","delivery","imageUrl","rating","ratingCount","offers","productId","position"}]}`
- scholar: `{"organic":[{"title","link","publicationInfo","snippet","year","citedBy"}]}`
- patents: `{"organic":[{"title","snippet","link","priorityDate","filingDate","grantDate","publicationDate","inventor","assignee","publicationNumber","language","thumbnailUrl","pdfUrl","figures":[{"imageUrl","thumbnailUrl"}]}]}`
- autocomplete: `{"suggestions":[{"value":"how to build your own house"}]}`
- scrape [Drittquelle, Onyx + openfactcheck]: `{"text":"...","markdown":"... (nur mit includeMarkdown)","metadata":{"title","og:title",...},"jsonld":{"dateModified",...},"credits":1}`. Nur `text` ist garantiert. Scrape-Preis in Credits: nicht öffentlich belegt (openfactcheck-Modell hat Feld credits).
- reviews/lens: Antwortformat nicht gefunden.

## 4. Fehler
Nichts auf serper.dev dokumentiert. Belegbares:
- 403 bei fehlendem Key (eigener Statuscheck gegen die Search-URL ohne Key, Body nicht ausgewertet). 
- Auto-generierte OpenAPI (nicht offiziell): 400 (fehlende/ungültige Parameter), 401 (Key), 429 ("rate limit exceeded or credits exhausted"), Body `{"message": "<text>"}` (https://raw.githubusercontent.com/api-evangelist/serper/refs/heads/main/openapi/serper-search-api-openapi.yml). Wahrscheinlich aus Gedächtnis generiert, nicht als Fakt behandeln.
- Clay-Community: 403 "Unauthorized" bei ungültigem Key, nur Websuchen-Zusammenfassung (https://community.clay.com/x/support/rqmzvzs0zsep/troubleshooting-serper-key-connection-errors), Seite nicht lesbar.
- Scrape: 400 wenn nicht scrapbar, "responds with 500s regularly", daher Retry (Onyx-Code-Kommentar, [Drittquelle]).
- "Not enough credits" (400) konnte ich NICHT belegen. Die Brief-Annahme bitte als ungeprüft behandeln. Clients nutzen raise_for_status und geben den Body mit (Haystack: `Response: {e.response.text}`).

## 5. Preise, Limits, Latenz
[dokumentiert] https://serper.dev/ (Pricing-Sektion; Stand 30.09./06.10.2026 laut https://apiserpent.com/blog/serper-pricing-credits-explained):
| Pack | Preis (netto) | Credits | pro 1k | QPS |
|---|---|---|---|---|
| Free | 0 | 2.500 (einmalig, ohne Kreditkarte) | | n/a |
| Starter | $50 | 50.000 | $1,00 | 50 |
| Standard | $375 | 500.000 | $0,75 | 100 |
| Scale | $1.250 | 2,5 Mio. | $0,50 | 200 |
| Ultimate | $3.750 | 12,5 Mio. | $0,30 | 300 |
- Credits 6 Monate gültig; Abzug nur bei erfolgreicher Antwort; bei 0 Credits keine Queries mehr; Limit änderbar auf Anfrage; Ultimate = 15.000 bis 18.000 Suchen/Min. Zahlung: Karten + PayPal. Erstattung 7 Tage, wenn <20 % verbraucht (Terms https://serper.dev/terms).
- Latenz [dokumentiert]: "in general 1 to 2 seconds", manchmal 2-4 s bei Retries gegen Google. Keine Messwerte von mir.
- Free-Tier-QPS: nicht öffentlich.

## 6. Firma/Technik
- [dokumentiert] "Serper is not affiliated with or endorsed by Google"; liefert "web-scraped data from public domain sources"; B2B-only; recht: UK-Recht (Terms, Stand 29.05.2024); ein Account pro Person; Datenverarbeiter ohne Namensnennung, Mail privacy@serper.dev. Rechtsträger, Sitz, Gründer: auf der Seite nicht genannt, Suche fand nichts.
- [dokumentiert] FAQ: "We do not cache anything", Anfrage geht in Echtzeit an Google (Selbstaussage). Footer: Status page, Medium, Twitter, Integrationen Haystack, Jan, CrewAI, LangChain. Behauptet 1.000.000+ Nutzer (Startseite).
- Infrastruktur (Proxys, Hoster): nirgends öffentlich. Aus dem Verhalten (Retries bei Google-Blocks) ableitbar: Proxy-/Scraping-Pool.
- Kontext [Drittquelle]: Google verklagte im Dez. 2025 SerpApi, Verfahren nach 7 Monaten abgewiesen (https://scrap.io/serper-dev-vs-scrap-io-google-maps-scraper-comparison, Eigenwerbung, unbestätigt); Googles Custom Search JSON API für Neukunden geschlossen (https://coldiq.com/blog/serper-pricing).

## Offene Punkte (nur mit Account prüfbar)
1. Exakter Fehlerbody und Statuscode bei 0 Credits. 2. Schwelle/Kosten bei num>10 und page>1. 3. Credit-Kosten pro Endpunkt (maps, scrape, reviews, lens). 4. Ob Batch-Array pro Query abrechnet und Max-Größe. 5. Vorhandensein von `credits` im Search-Body. 6. Inhalt von topStories.
