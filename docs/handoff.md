# Handoff: Stand 11.10.2026 abends

Fuer eine frische Session: Alles, was man wissen muss, um am lokalen Such-Dienst
weiterzuarbeiten. Details in README.md, docs/loop-plan.md, docs/loop-log.md.
Arbeit auf dem Gaming-Laptop (Training, Offline-Messung, Auftrag fuer Claude Code): train/LAPTOP.md.

## Was es ist

Lokaler, kostenloser Ersatz fuer alle API-Key-Dienste in Samis Setup:
Tavily Search (/search) + Extract (/extract), Exa (/exa/search|contents|findSimilar),
Serper (/serper/search|news), Jina Reader/Search (/jina/r/<url>, /jina/s/<q>).
Mehrsprachig en/de/es/fr. Ziel von Sami: ueberall besser als die Originale, vor
allem Qualitaet und Zeit bis zur Gold-Antwort.

## Betrieb

- launchd: com.sami.rag-searxng (SearXNG :8888, Code ~/tools/searxng) und
  com.sami.rag-service (Dienst :8000), Start ueber deploy/run-local.sh (liest .env als Daten).
  Neustart: `launchctl kickstart -k gui/$(id -u)/com.sami.rag-service`
- Logs: ~/Library/Logs/rag-search/. Kein Access-Log (Nutzerdaten im Pfad).
- Tests: `.venv/bin/python -m pytest -q tests` (Stand 138 gruen).
- research-agent (~/.claude/skills/research-agent/references/search.py) fragt zuerst
  own/own-exa, bezahlte Engines nur als Reserve mit Key.
- Emil auf dem VPS nutzt noch Tavily (Umzug offen, RAM knapp).

## Pipeline (app/)

Suche: SearXNG je Engine getrennt (stream.py, Bing zuerst; es/fr: Yandex+Yahoo;
Seite 2 nur yahoo/yandex; Zweitanfrage nach 1 s) -> Seiten parallel laden (extract.py,
SSRF-Schutz mit gepinnter IP, 4 MB Reader / 1 MB Suche) -> Extraktion im Prozess-Pool
(resiliparse, sonst trafilatura) -> BM25 2 Chunks/Seite -> Cross-Encoder
(cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 auf MPS) + Quellen-Prior + Sprach-Abzug
(rerank.py) -> Domain-Vielfalt -> Antwort. Caches: Trefferlisten (stale-while-revalidate),
Seiten, Dokumente (Bytes-Grenze).

## Messungen (alle in eval/, Ausgaben in eval/private/, gitignored)

- eval/bench.py: Faktenfragen gegen gecachte Tavily-Antworten (queries.jsonl, 30) und
  eigene Sets (queries_big 50 geprueft, queries_hard 15, queries_fresh 10, queries_multi 20 ES/FR).
- eval/history_bench.py: echte Recherche-Kassetten des research-agent (Gold-Links von Sami),
  `--base` fuer eigene Instanz; Bench-Instanzen mit SEARCH_CACHE_TTL_S=0 PAGE_CACHE_TTL_S=0.
- eval/reader_bench.py: Reader/Extract gegen Original-Jina (Cache eval/private/jina_cache).
- eval/blind_prepare.py + Richter-Agent: anonymer Blindtest Quellenqualitaet.
- eval/load_bench.py, eval/endpoint_bench.py, eval/dashboard_data.py (Dashboard-Daten).
- Dashboard: https://claude.ai/artifact/QTKWLgwqMnCRzBHtvvhNv8 (12 Datensaetze als Dateien).

## Stand gegen Originale

| Bereich | Original | Nachbau |
|---|---|---|
| Faktenfragen Platz 1 / Median | Tavily 97 % / 1,33 s | 100 % / 0,94 s |
| Recherche-MRR (27 Fragen) | Tavily 0,28, Serper 0,27 | Tavily-N. 0,24, Serper-N. 0,32, Exa-N. 0,26 |
| Gold-Domains Top 30 | Tavily 0,15 | 0,11 (groesste Luecke) |
| Blindtest Quellen-Vertrauen | Tavily 3,50 | 3,14 (doppelt so viele SEO-Seiten) |
| Reader Abdeckung / Tempo | Jina 3,8 s | 80 % / 0,2 s |
| Last | - | 0 Fehler bis 10 parallel, Durchsatz ~3,7/s (ein Reranker) |

## Fallen (bitte lesen)

- Mit `engines=` nie `categories=` an SearXNG senden (hebt den Filter auf).
- Bing: kein Paging, `cc=es/fr` -> 0 Treffer, behandelt Heim-IP sehr deutsch.
- Yahoo-Timeout nie unter 1,0 s (sonst Sperre und nur Bing).
- Cross-Encoder bevorzugt Englisch -> Sprach-Abzug noetig.
- Quellenqualitaet nie mit der eigenen Abwertungsliste messen (zirkulaer), nur Blindtest.
- Messrauschen: Recherche-MRR +-0,03 je Lauf; zwei Laeufe mitteln.
- Keine Calls an Tavily/Serper/Exa (Tavily-Plan 1508/1000 ueberzogen, Serper leer, Exa-Key ungueltig).
- Mehr-Agenten-Arbeit: Datei-Zustaendigkeit und eigene Ports wie in docs/loop-plan.md.

## Optimierungs-Runden (gestartet 11.10.2026 19:37)

- Zielmass: `eval/objective.py --split dev|test|both` (Replay in eval/private/replay,
  deterministisch; dev = queries_big + Recherche "queries", test = queries/hard/fresh/multi
  + Recherche "queries_niche"). Basis: dev score 0.8808, test 0.8346 (eval/private/objective-baseline-1.json).
- Workflow "search-service-optimize" (Run wf_16f52ab7-69c abgebrochen beim /clear, neu gestartet 19:58 als wf_fa22e268-f70 in Session 77437a21, nur in dieser Session fortsetzbar): 10 Spezialisten in Kopien unter
  dem Scratchpad (tracks/<name>, Patches tracks/<name>.patch), Join 1, 4 Tracks Runde 2,
  Join 2, Blindtest 2 (tracks/blind2), Review. Sicherungen vor jedem Join: tracks/backup-join1|2.
  Fortsetzen nach Abbruch: Workflow({scriptPath: "~/.claude/projects/-Users-samimagdouli-Desktop-APP-tavily-clone/9f2dc6f1-4ac4-4cf3-8099-2848095eac3d/workflows/scripts/search-service-optimize-wf_16f52ab7-69c.js", resumeFromRunId: "wf_fa22e268-f70"}).
- Uebernommene Aenderungen stehen in docs/loop-log.md (ab 19:30). Danach den launchd-Dienst neu
  starten, damit er den neuen Code nutzt, und das Dashboard mit eval/dashboard_data.py aktualisieren.

## PAUSIERT 11.10.2026 20:35 (so geht es weiter)

- Workflow gestoppt mitten in Runde 1, KEIN Patch im Original uebernommen. Original-Code unveraendert.
- Zwischenstand dauerhaft: eval/private/pause-2026-10-11/tracks (Arbeitskopien fusion, finetune, bigmodel,
  breadth, chunking, seo, latency + zwischenstand/<track>.patch) und .../agent-scratch (trainiertes Modell
  ft-a, Messskripte). Nicht angefangen: tuning, reader, highlights.
- Zwischenwerte dev (Basis 0.8808): bge-reranker-v2-m3 als 2. Stufe 0.8987, aber +1,3 bis 1,9 s (zu langsam);
  Feintuning-Modell trainiert, noch nicht gemessen; Breite mit Yandex site30 0.106 -> 0.115 (Rauschen?).
- Fortsetzen (beliebige Session): Workflow({scriptPath: "~/.claude/projects/-Users-samimagdouli-Desktop-APP-tavily-clone/9f2dc6f1-4ac4-4cf3-8099-2848095eac3d/workflows/scripts/search-service-optimize-wf_16f52ab7-69c.js"})
  Das Skript ist umgestellt: Agenten arbeiten in den vorhandenen Kopien weiter statt neu. Vorher RAM frei machen
  (Spotify, Safari, vite :5190 waren das Problem; Swap war 6,4/7,2 GB). Max. 6 Agenten laufen gleichzeitig.
- Der Such-Dienst :8000 laeuft wieder (launchd).

## 5-Stunden-Lauf 12.10.2026 ab 08:45

Run wf_dd3e8b0d-565 (Session 77437a21), Skript ~/.claude/projects/-Users-samimagdouli-Desktop-APP-tavily-clone/77437a21-f212-4b2d-8786-b1d475c13057/workflows/scripts/search-service-optimize-5h-wf_dd3e8b0d-565.js.
Ablauf: Runde 1 (8 Tracks, setzt pausierte Kopien fort) -> Join 1 -> Feintuning-Schleife (max 3, weiter nur bei dev >= +0.003 und test stabil)
-> Tools Serper/Exa/Jina-Reader -> Abschlussmessung + Review. Kopien unter eval/private/pause-2026-10-11/{tracks,loop1-3,tools}, Sicherungen backup-<join>.
Danach: launchctl kickstart -k gui/$(id -u)/com.sami.rag-service und Dashboard neu veroeffentlichen.

## PAUSIERT 12.10.2026 13:15 (nach Feintuning-Schleife, vor Tools-Join)

- Im Code (Dienst :8000 neu gestartet): Join1 (Fusion, Tuning, Chunking, Latenz), Loop1 engine_trust, Loop2 SPLIT_PAGES_WIDE=3,
  Loop3 harvest-Fix. dev 0.8808 -> 0.9355, test 0.8346 -> 0.8651. 145 Tests gruen.
- Feintuning/Destillation dreimal verworfen: dev steigt nur In-Sample, test faellt (zu wenige Trainingsfragen, Gold-Labels verrauscht).
  Weiterer Gewinn braucht fremde Trainingsdaten, nicht mehr Durchgaenge.
- Offen: Tools-Join (fertige Patches eval/private/pause-2026-10-11/tools/{serper,exa,reader}.patch; reader nutzt playwright),
  dann Abschlussmessung + Review. Halber Tools-Join wurde zurueckgerollt (Stand = backup-tools).
- Fortsetzen in derselben Session: Workflow({scriptPath: "~/.claude/projects/-Users-samimagdouli-Desktop-APP-tavily-clone/77437a21-f212-4b2d-8786-b1d475c13057/workflows/scripts/search-service-optimize-5h-wf_dd3e8b0d-565.js", resumeFromRunId: "wf_dd3e8b0d-565"})
  In neuer Session: nur Join-Agent auf die drei Tool-Patches + Abschluss von Hand starten (Prompts im Skript).
- 13:25: .venv hatte nach dem Reader-Track (playwright-Install 12:26) Pakete verloren (openai, uvloop, websockets ...),
  per `uv pip install -r requirements.txt` wiederhergestellt, Tests gruen, Dienst laeuft (0,85-1,2 s).
- Offen, vorher schon so: /search mit Standard include_answer=true gibt 500, weil in .env kein OPENAI_* steht
  (answer.py braucht ein LLM). Tavily-Default ist include_answer=false -> Default angleichen oder sauber 400/ohne answer.

## Nachtlauf 12./13.10.2026 ab 17:08 (Tools Serper/Exa/Reader)

Run wf_2165dcfb-8a7 (Session 77437a21), Skript ~/.claude/projects/-Users-samimagdouli-Desktop-APP-tavily-clone/77437a21-f212-4b2d-8786-b1d475c13057/workflows/scripts/tools-overnight-wf_2165dcfb-8a7.js.
Ablauf: Tools-Join der 3 fertigen Patches (+ SSRF-Pruefung Browser-Rendering) -> eval/tool_objective.py (dev/test je Tool, Reader-Referenz auf 80+ Seiten erweitert)
-> bis 5 Runden je Tool 2 Agenten + Join, Tool faellt raus nach 2 Runden ohne Gewinn -> Abschlussmessung, Review.
Ablage eval/private/nacht-2026-10-12 (r1..r5, backup-*). Danach: Dienst neu starten, Dashboard neu veroeffentlichen.
- 17:15 gestoppt (Sami will es ueber Nacht laufen lassen). Halber Tools-Join zurueckgerollt, Code = Stand vor dem Nachtlauf, Tests gruen.
  Neu starten mit demselben Skript (ohne resumeFromRunId, faengt sauber beim Tools-Join an).
- 22:05 Nachtlauf neu gestartet als wf_b9edec9c-af9 (Fortsetzen nur in Session 77437a21 mit resumeFromRunId wf_b9edec9c-af9).
- Nach dem Nachtlauf direkt anschliessend: Zuverlaessigkeits-Lauf (Fehlerquote, leere Antworten, Streuung bei Wiederholung, p95, Engine-Ausfall,
  include_answer-500 beheben) + neuer Blindtest Quellenvertrauen gegen Tavily (alt 3,50 vs 3,14). Samis Ziel: zuverlaessiger und hochwertiger als Tavily.

## Nachtlauf fertig 13.10.2026 03:10, Zuverlaessigkeits-Lauf ab 03:15

Nachtlauf (35 Agenten, 5 Runden): serper 0.727/0.639 -> 0.833/0.705, exa 0.946/0.951 -> 0.956/0.951, reader 0.784/0.870 -> 0.815/0.885,
Kern unveraendert 0.9355/0.8651. Messgeruest eval/tool_objective.py (Reader-Referenz 97 Seiten). Review FREIGABE (Sollte: Render-Timeout, pytest.ini).
Live-Tempo: /search 0.82 s, /serper 0.77 s, /exa 1.16 s, /jina/r 0.45 s (eval/private/live-tempo-2026-10-13.json).
Zuverlaessigkeits-Lauf: Run wf_0944d022-74e, Ablage eval/private/zuverl-2026-10-13. Dienst :8000 laeuft noch mit altem Code -> danach kickstart.

## Stand 13.10.2026 vormittags
- Zuverlaessigkeits-Lauf fertig und live: /search ohne LLM nie 500 (include_answer Standard false), ueberlange/leere Anfragen abgefangen, Exa-Frist 2,4 s, Antwort-Cache 900 s, fester Gleichstands-Bruch.
- QUALITY_PRIOR (app/quality.py) an, aber nur fuer /search; QUALITY_PRIOR_COMPAT=1 schaltet ihn auch fuer Serper/Exa ein (kostet dort test-Punkte).
- Blindtest 13.10.: Tavily 3,56, Nachbau 3,51 (ohne Filter 3,32). Leichte Zirkularitaet: Filter mit frueherem Richter auf denselben 30 Fragen kalibriert.
- Aktuelle Werte: eval/private/stand-2026-10-13/. Offen: SearXNG-Ausfall-Rueckfall, Konstanz kalter Laeufe, Serper-Recall, Mozilla Readability, VPS READER_RENDER=0, Emil-Umzug.

## Laptop-Paket 14.10.2026
- train/LAPTOP.md ist der vollstaendige Bericht fuer den Laptop: Dienst, Regeln, Stand, Ziele, Messung, Einrichtung,
  Training (prepare_data.py, train_reranker.py, run_night.ps1/.sh) und ein Auftrag fuer Claude Code.
- Messdaten: train/make_bundle.sh <ziel.zip> auf dem Mac (nur USB/AirDrop/lokales Netz), train/unpack_bundle.py auf dem Laptop.
- Ergebnis kommt als models/reranker-mminilm-<datum>/ zurueck, gemessen wird nur auf dem Mac.
