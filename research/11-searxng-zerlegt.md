# 11 SearXNG zerlegt: wo die 0,6 s hingehen

Stand 11.10.2026. Gemessen auf dem Mac gegen das laufende SearXNG (127.0.0.1:8888, Settings `deploy/searxng/settings.yml`), 15 Queries aus `eval/queries.jsonl`, 1 s Pause zwischen allen Anfragen. Rohdaten: `experiments/bench_minisearch_result.json`, Skript: `experiments/bench_minisearch.py`.

## Ergebnis in einem Satz

Die Suchzeit ist zu ~99 % reine Wartezeit auf Yahoo. SearXNG selbst kostet etwa 6 ms. Abschalten von Plugins und Co. bringt nichts Messbares, und ein eigener Prototyp ist nicht schneller, solange Yahoo dabei ist.

## Wie SearXNG eine Suche abarbeitet (Code)

- `searx/webapp.py` `search()`: Query parsen (`get_search_query_from_webapp`), dann `SearchWithPlugins(...).search()`, danach JSON (`webutils.get_json_response`); bei `format=json` wird kein Template gerendert (`render;dur=0`).
- `searx/search/__init__.py` `Search.search_multiple_requests()`: ein Thread pro Engine, danach `th.join(remaining_time)` auf ALLE Threads. Die Antwort kommt also erst, wenn die langsamste Engine fertig ist oder `actual_timeout` (hier 1,0 s = `outgoing.request_timeout`, Obergrenze `max_request_timeout: 2.0`) abläuft.
- `searx/search/processors/online.py`: HTTP über `searx/network` (curl_cffi, `impersonate="chrome"`, Connection-Pool), danach Engine-Parser.
- `searx/results.py` `ResultContainer`: Merging/Dubletten/Scoring nach `close()`, im Hauptthread.
- Answerer (`searx.answerers`) laufen vorab nur auf die Query-Zeichenkette (random/statistics), Plugins per `pre_search`/`on_result`/`post_search`.
- Der Header `Server-Timing` liefert `total`, `render` und pro Engine `total_*` (inkl. Parser) und `load_*` (nur HTTP). Darauf beruhen die Zahlen unten.

## Messung SearXNG (alle Engines, wie app/search.py)

| Teil | p50 | p95 |
|---|---|---|
| Wanduhr Client | 639 ms | 751 ms |
| SearXNG `total` | 635 ms | 748 ms |
| Yahoo (gesamt / nur HTTP) | 634 / 626 ms | 747 / 740 ms |
| Bing (gesamt / nur HTTP) | 161 / 156 ms | 257 / 250 ms |
| Wikipedia (Infobox) | 21 ms | 25 ms |
| DuckDuckGo | suspendiert ("access denied"), 0 ms | |
| Overhead in SearXNG (`total` minus langsamste Engine: Plugins, Answerer, Merging, JSON) | 2 ms | 2 ms |
| Overhead außerhalb (Flask/werkzeug/Socket, Wanduhr minus `total`) | 4 ms | 5 ms |
| Parser pro Engine (`total` minus `load`) | 3 bis 9 ms, parallel | |

Folgerungen:

1. Kritischer Pfad = Yahoo. Yahoo braucht serverseitig 530 bis 960 ms bis zum ersten Byte (direkt per curl_cffi nachgemessen, mit und ohne `de.`-Subdomain, Redirect-Hops für das YBV-Cookie nur beim ersten Aufruf).
2. Bing ist mit ~160 ms viermal schneller, Wikipedia läuft parallel und kostet effektiv nichts.
3. Flask, Plugins, Botdetection (Limiter ist aus), Result-Merging, Answerer, Locale-Erkennung, Templates zusammen ~6 ms, also ~1 %.
4. Ein Ausreißer (1012 ms) entsteht, wenn Yahoo das Timeout von 1,0 s reißt: SearXNG wartet bis zum Ende und markiert Yahoo dann als "Suspended: timeout".

## Was man in SearXNG abschalten kann

Sinnvoll (Traffic, Robustheit, kaum Zeit):

- `duckduckgo` raus (`keep_only` und `engines`), solange die IP gesperrt ist; er liefert nichts und erhöht nur das Sperr-Risiko.
- Plugins aus, die für JSON-Konsum nichts tun (spart ≤ 2 ms): unter `plugins:` jeweils `active: false` für `searx.plugins.calculator`, `hash_plugin`, `self_info`, `unit_converter`, `ahmia_filter`, `time_zone`, `hostnames`; `tracker_url_remover` kann bleiben (säubert URLs).
- `search.default_lang: ""` statt `"auto"`, wir schicken `language` ohnehin mit.

Das, was wirklich Zeit spart, ist eine Entscheidung über Yahoo:

- Pro-Engine-Timeout: in `engines:` bei `yahoo` `timeout: 0.5` setzen; dann wartet SearXNG höchstens 0,5 s, Yahoo fällt aber fast immer aus (nur 1 von 14 Yahoo-Antworten kam unter 500 ms).
- `outgoing.request_timeout: 0.7` als globale harte Grenze kappt die p95-Ausreißer (1,0 s) auf 0,7 s bei wenig Verlust.
- Bing allein: ~160 ms p50, aber Bing liefert bei deutschen W-Fragen oft Müll (Beispiel "Welche Hauptstadt hat Australien?" ergibt Duden-/Wiktionary-Seiten zu "welche"). Gleiches Verhalten in SearXNG und im Prototyp, liegt also an Bing, nicht am Parser.

## Prototyp `experiments/minisearch.py`

- Bing + Yahoo (DDG-HTML optional mit `--ddg`), parallel per `asyncio` über eine wiederverwendete `curl_cffi.AsyncSession(impersonate="chrome")`; eigene Parser mit lxml, Herkunft der Selektoren aus den SearXNG-Engines im Dateikopf vermerkt (AGPL).
- Reciprocal Rank Fusion (k = 60) mit URL-Normalisierung, Ausgabe `{title,url,content,engine}`.
- Harte Deadline 700 ms (`asyncio.wait(timeout=...)`), zu späte Engine wird verworfen.
- Dependency: `curl_cffi==0.16.3` in `.venv` installiert (noch nicht in `requirements.txt`).

### Vergleich (15 Queries)

| | p50 | p95 | max | Treffer Ø |
|---|---|---|---|---|
| A SearXNG, alle Engines | 639 ms | 751 ms | 816 ms | 16,0 |
| B SearXNG, nur bing,yahoo | 579 ms | 773 ms | 1012 ms | 15,5 |
| C minisearch, Deadline 700 ms | 604 ms | 702 ms | 703 ms | 16,0 |

- URL-Überlappung Top 10: C vs A 0,69, C vs B 0,64; über alle Treffer C vs B 0,71. Die Differenz kommt vor allem von der Reihenfolge der Fusion (SearXNG gewichtet anders als RRF) und von der Tagesform der Engines, nicht von fehlenden Treffern.
- Deadline-Ausfall: 1 von 15 (Yahoo), dann nur Bing-Treffer (10 statt 17).
- Engine-Zeiten im Prototyp: Bing p50 172 ms, Yahoo p50 602 ms, also dieselben Werte wie in SearXNG.

### Bewertung

- Schneller im Median: nein, 604 vs 639 ms (−5 %, innerhalb der Streuung). Der Engpass ist Yahoos Serverzeit, die kein Client verkürzt.
- Besser im Tail: ja, p95/max sind hart bei ~700 ms gedeckelt statt bis 1,0 s. Das gleiche erreicht man in SearXNG mit `request_timeout: 0.7`.
- Gleich gut: Trefferzahl gleich, ~70 % der Top-10-URLs identisch.
- Ein Umbau weg von SearXNG lohnt sich nur, wenn man Latenz gegen Vollständigkeit tauschen will (z. B. Bing nach ~200 ms sofort zurückgeben und Yahoo nur nachschieben, falls er in der Deadline kommt), oder um eine Python-Abhängigkeit/einen Prozess zu sparen. Für reine Geschwindigkeit reicht SearXNG mit angepassten Timeouts.

## Nächste Schritte

1. In `settings.yml`: `duckduckgo` raus, `outgoing.request_timeout: 0.7`, Plugins aus (siehe oben).
2. Wenn unter 300 ms gewollt: "Bing first"-Modus testen (Bing sofort, Yahoo nur bei Ankunft unter z. B. 350 ms) und mit `eval/run_eval.py` prüfen, wie stark die Antwortqualität bei deutschen Fragen fällt.
3. Bei Bedarf `curl_cffi` in `requirements.txt` aufnehmen, falls minisearch weiterverfolgt wird.
