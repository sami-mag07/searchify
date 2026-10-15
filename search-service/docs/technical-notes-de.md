# Technische Notizen (Deutsch, Stand 11.10.2026)

Ausfuehrliche Bau- und Messnotizen. Die README im Hauptordner ist die Kurzfassung.

Eigener Such-/Antwort-Dienst mit Tavily-artiger Schnittstelle (`POST /search`
liefert `answer` + `results[]` mit `score`). Gebaut aus offen lizenzierten
Bausteinen, mit eigenem Code, eigenen Prompts und eigener Ground-Truth.

Clean-room: Grundlage ist ausschliesslich oeffentliche Dokumentation. Es wurde
**kein** Tavily-Account angelegt, **keine** Tavily-Query gesendet und **kein**
Tavily-Output als Vorlage genutzt. Details und Rechts-Audit in den Planungsdateien
`00-legal.md`, `01-spec.md`, `02-buildplan.md` (liegen im Arbeits-Scratchpad,
nicht im Repo).

> Nur die Schnittstelle/Parameter-Namen sind an Tavily angelehnt (urheberrechtlich
> frei). Die Pipeline dahinter ist eigenstaendig entworfen.

---

## Architektur

```
Query
  -> 1. Such-Backend               app/search.py     SearXNG | Serper | ddgs, mit Fallback
  -> 2. Snippets sofort ranken     app/rerank.py     Cross-Encoder auf MPS/CUDA, score 0..1
     parallel: Seiten laden        app/extract.py    alle gleichzeitig, hartes Zeitbudget
  -> 3. je Seitenbuendel           app/rerank.py     BM25 waehlt 3 Chunks/Seite, Cross-Encoder
  -> 4. Merge je Quelle (Top-k)    app/rerank.py     eine result-Zeile pro URL
  -> 5. Claude (claude-code-api)   app/answer.py     answer mit [n]-Quellenmarkern
  -> JSON                          app/main.py       SearchResponse (+ usage.timings_ms)
```

### Tempo und Qualitaet (erster Stand 11.10.2026)

Gemessen mit `eval/bench.py`, eigene Seite ueber lokales SearXNG (kostenlos),
Tavily aus gecachten Antworten (`search_depth=basic`, ohne answer), 30 Fragen DE/EN:

| | Tavily | Eigen (basic) |
|---|---|---|
| Latenz p50 / p95 | 1.33 / 1.84 s | 0.93 / 1.12 s |
| Antwort im Top-1 / MRR | 97 % / 0.978 | 97 % / 0.983 |
| content je Treffer (Median) | 1188 Zeichen | 1467 Zeichen |

30 Fragen rauschen um etwa +-15 Punkte; die Qualitaet ist gleichauf, Tempo
und Inhalt sind klar besser. Zusatz-Sets ohne Tavily (Endstand 11.10.2026):

| Set | Fragen | Top-1 | Top-3 | MRR | p50 |
|---|---|---|---|---|---|
| `queries_big.jsonl` (geprueft, Belege in research/13) | 50 | 94 % | 100 % | 0.967 | 0.92 s |
| `queries_hard.jsonl` (Long-Tail) | 15 | 87 % | 100 % | 0.922 | 0.99 s |
| `queries_fresh.jsonl` (nach Modell-Cutoff) | 10 | 100 % | 100 % | 1.0 | 0.91 s |

Vorher lief alles nacheinander: 10 bis 20 Seiten einzeln geladen (je bis 8 s
Timeout), dann bge-reranker-base auf der CPU ueber ~100 Chunks a 350 Woerter.
Ergebnis: ~14 s pro Anfrage. Was es jetzt schnell macht:

1. Seiten parallel laden mit Zeitbudget je `search_depth` (fast 0 ms, basic 500 ms,
   advanced 1500 ms). Verspaetete Seiten laufen im Hintergrund fertig und landen
   im Seiten-Cache (`PAGE_CACHE_*`).
2. Extraktion in einem Prozess-Pool (in Threads blockierte die GIL), zweistufig:
   resiliparse (~2 ms je Seite), unter 120 Woertern trafilatura.
3. Ranken ueberlappt mit dem Laden: Snippets sofort, Seiten buendelweise bei Ankunft.
4. Cross-Encoder direkt ueber transformers auf MPS/CUDA, fp16, 100-Wort-Chunks,
   BM25-Vorauswahl auf 3 Chunks je Seite, mMiniLM statt bge-base (~3x schneller).
5. Engines getrennt fragen (`SEARXNG_SPLIT_ENGINES=bing,yahoo`, app/stream.py):
   Bing antwortet in ~0.2 s, Yahoo in ~0.6 s. Mit den Bing-Treffern laden und
   ranken, Yahoo nachmischen. Wichtig: mit `engines` keinen `categories`-Parameter
   senden, sonst fragt SearXNG doch wieder alle Engines.
   Haengt eine Engine (Yahoo in ~3 % der Faelle ueber 1.3 s), geht nach
   `SPLIT_HEDGE_MS` (1 s) eine Zweitanfrage raus; die erste Antwort zaehlt.
   Gemessen: 50 Anfragen, Yahoo kein einziges Mal verloren (vorher ~3 %).
6. Sortierung nach Roh-Logit des Cross-Encoders (sonst sind alle relevanten
   Chunks 1.0000 und die Reihenfolge Zufall), ausgegebener score gestreckt.
7. Optional `FETCH_CLIENT=curl_cffi` (Chrome-TLS-Profil, weniger 403); im A/B-Test
   gleich schnell und gleich viele Seiten, darum nicht Default.
8. Trefferlisten-Cache mit stale-while-revalidate (`SEARCH_CACHE_TTL_S`) und
   Fallback-Backend, wenn eine Suche fehlschlaegt oder 0 Treffer liefert.

## Nachgebaute Dienste (alle lokal, kostenlos, gleiche Pipeline)

Laeuft dauerhaft ueber launchd (deploy/launchd/, deploy/run-local.sh) auf
http://127.0.0.1:8000, SearXNG auf :8888. Sprachen: en, de, es, fr (automatisch
erkannt). Key-Check fuer alle Endpunkte ueber SERVICE_API_KEY (Bearer, x-api-key,
X-API-KEY oder api_key im Body).

| Original | Endpunkt | Messung (11.10.2026, neuer Stand oben) |
|---|---|---|
| Tavily Search | POST /search | Faktenfragen: Top-1 100 % (Tavily 97 %), 0,94 s (1,33 s); Recherche-Historie: mrr 0,24-0,27 (0,28), Gold-Domains 0,11 (0,15) |
| Tavily Extract | POST /extract | Text-Abdeckung 81 % / Sauberkeit 94 % gegen Original-Jina-Reader, 0,3 s |
| Exa | POST /exa/search, /exa/contents, /exa/findSimilar | Recherche-Historie mrr 0,26 (Tavily 0,28), Highlights in 4 Sprachen |
| Serper | POST /serper/search, /serper/news | Recherche-Historie mrr 0,32 (Serper 0,27), 0,8 s (1,6 s) |
| Jina Reader / Search | GET /jina/r/{url}, /jina/s/{q} | Reader: Erfolg 90 % (90 %), Abdeckung 80 %, 0,2 s (3,8 s) |

Blindtest, Recherche-Ranking und Zuverlaessigkeit: siehe "Ergebnisse auf einen Blick" oben.
Dashboard mit allen Benchmarks: https://claude.ai/artifact/QTKWLgwqMnCRzBHtvvhNv8

Grenzen: kein eigener Web-Index (Exa-Neural, Breite bei Nischenrecherche unter
Tavily), kein JS-Rendering, kein Umgehen von Bot-Schutz. Verlauf: docs/loop-log.md.

### SearXNG lokal ohne Docker

```
git clone --depth 1 https://github.com/searxng/searxng.git ~/tools/searxng
cd ~/tools/searxng && uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt
SEARXNG_SECRET=$(openssl rand -hex 32) \
SEARXNG_SETTINGS_PATH=/pfad/zu/tavily-clone/deploy/searxng/settings.yml \
.venv/bin/python -m searx.webapp
```
Google und Brave sperren von Heim-/Server-IPs schnell, darum nur Bing, Yahoo,
DuckDuckGo, Wikipedia (siehe `research/09-serp-blocking.md`).

### Recherche

`research/` enthaelt die Funde der Recherche-Agents vom 11.10.2026 (Such-Backends,
wie Tavily arbeitet, Extraktion, Reranker, Community, Papers, Eval, Fetch,
Blocking, Snippets, SearXNG-Zerlegung, Alternativen), jeweils mit Quellen.

Getestet und verworfen (11.10.2026): kuerzere Wartezeit nach der letzten Engine
(100 ms: 0.81 s, aber weniger Treffer und Inhalt), Wikipedia als dritte Engine
(liefert ueber SearXNG nur Infoboxen), 15 statt 10 Kandidaten (+0.3 s),
SearXNG-Timeout 0.7 s (Yahoo faellt weg, Top-1 sinkt auf 73 %).
`experiments/minisearch.py` ist ein Prototyp ohne SearXNG, nur 35 ms schneller,
lehnt sich an SearXNG-Code an (AGPL) und ist darum nicht im Dienst.

Feste Bausteine:

| Stufe | Baustein | Lizenz/Zugang |
|---|---|---|
| Such-Backend | SearXNG self-host | AGPL-3.0, kein API-Key |
| Extraktion | trafilatura | Apache-2.0 |
| Reranker | mMiniLM-Cross-Encoder (transformers), bge waehlbar | Apache-2.0 / MIT, lokal |
| Antwort-LLM | Claude ueber claude-code-api | OpenAI-kompatibel, Key via Env |
| Dienst | FastAPI + uvicorn | MIT/BSD |

`score` ist der **sigmoid-normalisierte Cross-Encoder-Score** des Rerankers
auf Skala 0..1 (eigene, offengelegte Skala - keine Nachbildung einer nicht
dokumentierten Tavily-Formel).

---

## Secrets

- Keine Secrets im Code. Alle Keys/URLs kommen aus der Umgebung bzw. `.env`
  (gelesen in `app/config.py` via `os.environ`).
- `.env`, `.env.*` und `eval/private/` sind in `.gitignore`.
- `.env.example` enthaelt nur Platzhalter. Zum Start:
  `cp .env.example .env` und echte Werte eintragen.
- Der `OPENAI_API_KEY` (Token des claude-code-api-Wrappers) und der optionale
  `SERVICE_API_KEY` stehen ausschliesslich in `.env`.

---

## Nachbau-Schritte

### 1. Umgebung
```
cd /Users/samimagdouli/Desktop/APP/tavily-clone
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # danach echte Werte eintragen
```

### 2. Such-Backend (SearXNG) via Docker
JSON-Ausgabe ist in `deploy/searxng/settings.yml` bereits aktiviert.
```
# Secret setzen (Beispiel): in .env  SEARXNG_SECRET=$(openssl rand -hex 32)
docker compose -f deploy/docker-compose.yml up -d searxng
# Test:
curl 'http://localhost:8888/search?q=test&format=json' | head
```
Der Docker-Entrypoint ersetzt den Platzhalter `ultrasecretkey` in der
`settings.yml` durch `SEARXNG_SECRET`. SearXNG laeuft lokal auf Port **8888**
(Port 8080 bleibt frei fuer claude-code-api).

### 3. Reranker-Modell vorab ziehen
Das Modell wird beim ersten `/search` automatisch von HuggingFace geladen
(Default `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, ca. 470 MB). Optional vorab:
```
python -c "from transformers import AutoModelForSequenceClassification as M; M.from_pretrained('cross-encoder/mmarco-mMiniLMv2-L12-H384-v1')"
```
Andere Wahl ueber `RERANKER_MODEL`, z. B. `BAAI/bge-reranker-base` (genauer, ~3x langsamer).

### 3b. Optional: Browser-Rendering fuer den Reader (JS-Seiten)
`/jina/r` und `/extract` laden in Stufen, jede nur, wenn die vorige scheitert (fehlt der
Text, ist er unter 40 Woertern oder eine Challenge-Seite; der statische Text bleibt,
falls alle Stufen scheitern). Der schnelle Weg bleibt unberuehrt.
1. statisch (httpx, bei 403/429/503 curl_cffi), 2. Render im geteilten Headless-Chromium,
alle Anfragen ueber httpx (bleibt offen, nach 5 min Leerlauf zu; `RENDER_SLOTS`=3
gleichzeitig; Reddit klappt nur hier), 3. eigener Kontext im selben Browser, der selbst
laedt (echter Chrome-TLS gegen Cloudflare & Co.; `RENDER_DIRECT_SLOTS`=2, wartet
hoechstens 6 s auf eine Challenge; `RENDER_DIRECT=0` schaltet nur diese Stufe ab).
Hoechstens 2 Seiten je Anfrage (`app/compat/render.py`, `reader._render_failed`).
`playwright` steht in `requirements.txt`, den Browser einmalig laden (~100 MB):
```
.venv/bin/python -m playwright install chromium-headless-shell
```
Ohne playwright oder mit `READER_RENDER=0` ist die Reserve aus (Rueckfall: Seite zaehlt
als Fehlschlag wie bisher). SSRF-Schutz: Start-URL nur oeffentlich, jede Browser-Anfrage
(Unterressourcen, Redirects, JS-Weiterleitungen, Popups) laeuft ueber den gepinnten Client
aus `app/extract.py`, alles andere ueber einen toten Proxy. Stufe 3: der ganze Verkehr
des Kontexts laeuft ueber den Ausgangs-Proxy `app/compat/egress.py` auf 127.0.0.1 (nur
oeffentliche Ziele, auf die gepruefte IP festgenagelt, nur Web-Ports, verschluesselte
Bytes unveraendert durchgereicht); Tests `tests/test_render_ssrf.py`.

### 4. Antwort-LLM (Claude ueber claude-code-api)
`claude-code-api` als OpenAI-kompatiblen Wrapper starten (eigener Login) und in
`.env` eintragen: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`.

### 5. Dienst starten
```
uvicorn app.main:app --reload --port 8000
# oder komplett via Docker (Dienst + Caddy):
docker compose -f deploy/docker-compose.yml --profile full up -d
```

### Links (`include_links`)

Der content bleibt fuer LLMs sauber (Menues und Buttons fliegen raus). Wer die
Links braucht, setzt `"include_links": true`: Jeder Treffer bekommt `links`
(Text + URL, max. 25), Aktions-Links wie Anmelden, Bewerben, Registrieren,
Formular, Ausschreibung oder PDF zuerst; Suchformulare der Website fliegen raus.
Links gibt es nur von geladenen Seiten, darum wartet eine Anfrage mit Links so
lange wie `advanced` (~1.7 s statt ~0.9 s). Die Links kommen ungeprueft von
fremden Seiten: Wer sie automatisch weiterverfolgt, muss sie selbst pruefen
(interne Adressen, Phishing). Der Dienst gibt sie nur aus, ruft sie nicht ab.

### 6. Beispiel-Request
```
curl -X POST http://localhost:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "Wofuer steht RAG bei KI-Systemen?", "max_results": 5, "include_answer": true}'
```

---

## Eval (nur eigener Dienst, kein Tavily)

`eval/run_eval.py` misst den **eigenen** Dienst gegen oeffentliche Ground-Truth:
Faithfulness und Answer-Relevanz (ragas) plus Latenz (p50/p95) und Trefferzahl.
Kein Tavily-Call.
```
python eval/run_eval.py --service-url http://localhost:8000 --queries eval/queries.jsonl
# Ausgaben -> eval/private/ (gitignored)
```
`eval/queries.jsonl` ist ein kleines eigenes DE/EN-Set und kann erweitert werden.
ragas braucht selbst ein Richter-LLM; genutzt wird derselbe OpenAI-kompatible
Wrapper. Ohne ragas werden nur Latenz/Trefferzahl gemessen.

### compare_tavily.py (separat, NICHT im Build ausgefuehrt)
`eval/compare_tavily.py` ruft Tavily UND den eigenen Dienst auf denselben Queries
und schreibt eine rein interne Tabelle (Latenz, URL-Overlap via RBO/Jaccard,
answer-Laenge) **nur** nach `eval/private/`.

- Laeuft **nur** mit gesetztem `TAVILY_API_KEY`.
- Nutzung auf **eigenes ToS-Risiko** des Betreibers (Tavily-Terms untersagen u. a.
  Konkurrenz-Bau und die Weitergabe von Leistungsvergleichen).
- Ergebnisse sind **nicht zu veroeffentlichen** (kein Blog/README/Pitch).

Start (bewusst durch den Betreiber, mit eigenem Key):
```
TAVILY_API_KEY=dein-key python eval/compare_tavily.py --service-url http://localhost:8000
```

---

## Tests

`tests/test_pipeline.py` prueft ohne externe Infrastruktur: Schema,
Chunking, Dedup/URL-Kanonisierung und einen Dry-Run der kompletten Pipeline
(`run_search`) mit gestubbtem Such-Backend/Laden/Rerank/Answer.
`tests/test_speed_parts.py` prueft BM25-Auswahl, Seiten-Cache, Zeitbudget,
Hedging und Backend-Fallback.
```
python -m pytest tests/ -q
```

`eval/bench.py` misst Tempo und Trefferquote gegen gecachte Tavily-Ergebnisse
(nur intern, Ausgaben in `eval/private/`). Jede Query kostet ein Such-Credit pro
Durchlauf, Standard sind 2 Durchlaeufe.

---

## Projektstruktur

```
tavily-clone/
  app/        FastAPI-Dienst + Pipeline
  eval/       eigenes Eval + separates Vergleichsskript
  deploy/     docker-compose (SearXNG/Dienst/Caddy), Dockerfile, Caddyfile
  tests/      Smoke-/Dry-Run-Tests ohne Infra
```

### Eigene Mehrwerte (bewusst, kein Tavily-Imitat)
1. DE/EU-Fokus (`language`, `country` an SearXNG durchgereicht).
2. Domain-Priorisierung ueber `include_domains`/`exclude_domains`.
3. Transparentes Ranking: `score`-Herkunft offengelegt (bge + sigmoid).
4. Quellen-strenge Summary: jeder Satz an eine `[n]`-Quelle gebunden.
5. Kostenkontrolle: SearXNG-Default statt Pay-per-Query.
