# Loop-Plan: Nachbauten gegen die Historie messen und verbessern (11.10.2026)

Ziel: Jeder Dienst mit API-Key (Tavily Search, Tavily Extract, Exa, Serper, Jina
Reader/Search) laeuft lokal und kostenlos mindestens so gut wie das Original.
Gemessen wird gegen historische Ergebnisse der Originale bzw. gegen das Original,
wo es ohne Key erreichbar ist.

## Messlatten

| Dienst | Messlatte | Quelle |
|---|---|---|
| Tavily Search, Serper | Kassette 01.10.2026 00:09, 27 echte Recherche-Queries, Gold-Links von Sami (site@30, page@30, mrr, Sek.) | ~/.claude/skills/research-agent/evals/ (tasks.json, cassettes/, search_eval.py) |
| Exa | keine Historie (Key war schon ungueltig): gegen Gold-Links und gegen Tavily/Serper derselben Kassette | dito |
| Jina Reader | Original r.jina.ai ohne Key (gedrosselt, max. 15 Abrufe/Minute), Antworten in eval/private/jina_cache/ | live, einmalig |
| Tavily Extract | Text-Abdeckung gegen den Original-Jina-Reader auf denselben URLs | dito |
| Tavily Search (Fakten) | eval/bench.py mit gecachtem Tavily (30 Fragen) + queries_big/hard/fresh/multi | eval/private/tavily_cache.json |

Historische Werte (Kassette 00:09, Ranking count): Tavily site@30 0.15 / page@30 0.02 / mrr 0.28 / 5.7 s;
Serper 0.12 / 0.01 / 0.27 / 1.6 s.

## Laufende Instanzen

- SearXNG: 127.0.0.1:8888 (~/tools/searxng, Settings deploy/searxng/settings.yml)
- Dienst-Instanzen, je Eigentuemer eine (nur der Eigentuemer startet/stoppt seine):
  - 8000 Kern (Haupt-Session), 8001 Agent Exa, 8002 Agent Serper, 8003 Agent Reader
  - Start im Projektordner: `set -a; source .env; set +a; EXTRACT_WORKERS=3 nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port <PORT> > /tmp/svc-<PORT>.log 2>&1 &`
  - Stoppen nur per PID der eigenen Instanz (`lsof -ti tcp:<PORT> | xargs kill`), nie `pkill -f uvicorn`.
  - Benches mit `--base http://127.0.0.1:<PORT>` (eval/history_bench.py). Bench-Instanzen ohne Server-Cache starten (`SEARCH_CACHE_TTL_S=0 PAGE_CACHE_TTL_S=0`), sonst misst ein zweiter Lauf nur den Cache.
  - Kern-Aenderungen (app/*.py ausser compat) gelten erst nach Neustart der jeweiligen Instanz.

## Datei-Zustaendigkeit (nur der Eigentuemer schreibt)

| Bereich | Dateien | Eigentuemer |
|---|---|---|
| Kern-Pipeline | app/main.py, app/search.py, app/stream.py, app/extract.py, app/pages.py, app/rerank.py, app/config.py, app/schema.py, app/lang.py, app/auth.py, deploy/, .env*, README.md | Kern (Haupt-Session) |
| Exa | app/compat/exa.py, tests/test_compat_exa.py, research/15-exa-spec.md | Agent Exa |
| Serper | app/compat/serper.py, tests/test_compat_serper.py | Agent Serper |
| Jina + Extract | app/compat/jina.py, app/compat/tavily_extract.py, tests/test_compat_jina.py, tests/test_compat_tavily_extract.py | Agent Reader |
| Such-Historien-Bench | eval/history_bench.py (Kern pflegt), eval/private/history-* (jeder darf eigene Laeufe ablegen) | Kern |
| Reader-Bench | eval/reader_bench.py, eval/private/reader-*, eval/private/jina_cache/ | Agent Bench-Reader |
| Bestehende Benches | eval/bench.py, eval/queries_*.jsonl | Kern |

Brauchst du eine Aenderung in einer fremden Datei: Nachricht an den Eigentuemer
(bzw. an die Haupt-Session), nicht selbst aendern.

## Regeln

- Upstream schonen: zwischen zwei Anfragen an den Dienst/SearXNG >= 1 s Pause in Benches,
  r.jina.ai max. 15 pro Minute. Google/Brave nicht anfragen.
- Keine Credits ausgeben: keine Calls an Tavily/Serper/Exa.
- Skripte mit Prozess-Pool brauchen `if __name__ == "__main__"` (spawn).
- Tests ohne Netz; alle Tests muessen gruen bleiben: `.venv/bin/python -m pytest -q tests`.
- Ergebnisse knapp in docs/loop-log.md eintragen (eine Zeile je Runde: Datum, Bereich, Aenderung, Messwerte vorher -> nachher).
- Abbruch je Bereich: 3 Runden ohne messbaren Gewinn.
