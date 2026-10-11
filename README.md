# RAG-Such-Dienst (clean-room)

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

### Tempo (Stand 11.10.2026)

Vorher lief alles nacheinander: 10 bis 20 Seiten einzeln geladen (je bis 8 s
Timeout), dann bge-reranker-base auf der CPU ueber ~100 Chunks a 350 Woerter.
Ergebnis: ~14 s pro Anfrage. Was es jetzt schnell macht:

1. Seiten parallel laden mit Zeitbudget je `search_depth` (fast 0 ms, basic 300 ms,
   advanced 1500 ms). Verspaetete Seiten laufen im Hintergrund fertig und landen
   im Seiten-Cache (`PAGE_CACHE_*`).
2. Ranken ueberlappt mit dem Laden: Snippets sofort, Seiten buendelweise bei Ankunft.
3. Cross-Encoder direkt ueber transformers auf MPS/CUDA, fp16, 100-Wort-Chunks,
   BM25-Vorauswahl auf 3 Chunks je Seite, mMiniLM statt bge-base (~3x schneller).
4. Serper mit `num=10` und versetzter Zweitanfrage nach 900 ms gegen Ausreisser.

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

### 4. Antwort-LLM (Claude ueber claude-code-api)
`claude-code-api` als OpenAI-kompatiblen Wrapper starten (eigener Login) und in
`.env` eintragen: `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`.

### 5. Dienst starten
```
uvicorn app.main:app --reload --port 8000
# oder komplett via Docker (Dienst + Caddy):
docker compose -f deploy/docker-compose.yml --profile full up -d
```

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
