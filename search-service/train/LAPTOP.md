# Laptop-Paket: Such-Dienst verstehen, messen, Reranker trainieren

Stand 14.10.2026. Dieses Dokument ist der ganze Kontext fuer die Arbeit auf dem Gaming-Laptop
(i7-12900, RTX 3070 Ti Laptop mit 8 GB). Es reicht allein. Eine Claude-Code-Sitzung auf dem
Laptop liest zuerst diese Datei, dann bei Bedarf `docs/handoff.md` und `docs/finetuning-plan.md`.

Ganz ohne Rumtippen: [START.md](START.md), ein Auftrag für Claude Code, der alles allein einrichtet.

## Schnellstart (nur Training, 3 Befehle)

Voraussetzung: Python 3.12, Git, NVIDIA-Treiber. Einrichtung im Detail in Abschnitt 6.

Windows (PowerShell, im Repo-Ordner):

```powershell
py -3.12 -m venv .venv-train; .venv-train\Scripts\python -m pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu126
.venv-train\Scripts\python -m pip install -r train\requirements-train.txt
powershell -ExecutionPolicy Bypass -File train\run_night.ps1
```

Linux oder WSL2 (im Repo-Ordner):

```bash
python3.12 -m venv .venv-train && .venv-train/bin/python -m pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu126
.venv-train/bin/python -m pip install -r train/requirements-train.txt
bash train/run_night.sh
```

Morgens: Ordner `models/reranker-mminilm-<datum>/` per USB-Stick auf den Mac kopieren.
Beurteilt wird das Modell nur dort (Abschnitt 8).

## 1. Was der Dienst ist

Ein lokaler, kostenloser Ersatz fuer bezahlte Such-APIs. Er spricht die Formate von
Tavily (`/search`, `/extract`), Exa (`/exa/...`), Serper (`/serper/...`) und Jina Reader
(`/jina/r/<url>`, `/jina/s/<q>`). Sprachen en, de, es, fr. Ziel: in Qualitaet und Tempo
besser als die Originale.

So arbeitet eine Suche (Code in `app/`):

1. SearXNG fragt Bing, Yahoo, Yandex je Engine getrennt (`app/stream.py`, `app/search.py`).
2. Die Seiten werden parallel geladen und in einem Prozess-Pool zu Text (`app/extract.py`).
3. BM25 waehlt je Seite 2 Abschnitte. Ein Cross-Encoder sortiert sie (`app/rerank.py`).
   Modell: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, 256 Token, umschaltbar ueber `RERANKER_MODEL`.
4. Quellen-Prior, Sprach-Abzug, SEO-Abwertung (`app/quality.py`), Domain-Vielfalt.
5. Antwort im Format des jeweiligen Originals.

Genauer: `docs/technical-notes-de.md` (Bau und Messung), `README.md` (Kurzfassung mit Grafiken).

## 2. Regeln (gelten immer)

- Nie Anfragen an Tavily, Serper oder Exa. Keine Schluessel dafuer eintragen. Vergleiche
  laufen nur gegen gespeicherte Antworten in `eval/private/`.
- SearXNG hoechstens 1 Anfrage pro Sekunde. Sonst sperren Bing und Yahoo die IP.
- Nie `uv sync`. Pakete nur mit `pip install` oder `uv pip install --python <venv>/bin/python`
  in die passende Umgebung.
- Tests: `.venv/bin/python -m pytest -q tests` (Windows: `.venv\Scripts\python -m pytest -q tests`).
  Muessen gruen bleiben. Dazu `pyflakes` auf geaenderte Dateien.
- Schluessel und Passwoerter nur in `.env`, nie im Code, nie im Commit. Vor jedem Commit
  `git diff --cached` auf Schluessel pruefen.
- Das Repo ist privat und bleibt privat. Grund: Tavilys Nutzungsbedingungen verbieten,
  Vergleiche mit Tavily zu veroeffentlichen. `eval/private/` kommt nie auf GitHub und nie in
  eine Cloud (enthaelt gespeicherte Tavily-Antworten).
- Arbeit auf einem eigenen Branch. Nie direkt auf `main` pushen.
- Optimiert wird nur auf `dev`. `test` dient nur der Kontrolle am Ende. Nichts aus den
  Pruefungen darf ins Training.

## 3. Stand gegen die Originale (13.10.2026)

| Bereich | Original | Nachbau |
|---|---|---|
| Quellen-Vertrauen im Blindtest (1 bis 5) | Tavily 3,56 | 3,51 |
| SEO- und Werbeseiten unter 90 Top-Treffern | Tavily 15 | 23 |
| Recherche-Ranking MRR, Tavily-Endpunkt | Tavily 0,28 | 0,25 |
| Recherche-Ranking MRR, Serper-Endpunkt | Serper 0,27 | 0,31 |
| Faktenfragen Platz 1 | Tavily 97 % | 100 % |
| Typische Antwortzeit /search | Tavily 1,33 s | 0,93 s |
| Typische Antwortzeit /serper | Serper 1,60 s | 0,91 s |
| Typische Antwortzeit /exa | (kein Wert) | 1,44 s |
| Typische Antwortzeit Reader | Jina 3,79 s | 0,22 s |

Die Luecke beim Recherche-Ranking (0,25 gegen 0,28) kommt fast nur aus einer Aufgabe:
"politik" mit 0,42 gegen 1,00. Die anderen Aufgaben liegen etwa gleichauf.
Offline-Zielmass `eval/objective.py`: dev 0,9355, test 0,8651.

## 4. Offene Ziele

Jeder Durchschnitt muss das Original schlagen:

1. SEO- und Werbeseiten: hoechstens 15 von 90 (jetzt 23).
2. Recherche-MRR Tavily-Endpunkt: ueber 0,28 (jetzt 0,25). Hebel: Aufgabe "politik".
3. Blindtest Quellen-Vertrauen: ueber 3,56 (jetzt 3,51).
4. Ein feinjustiertes oder neu trainiertes Modell darf nie langsamer sein als vorher.

Leitplanke: Kein anderer Messwert darf fallen (Toleranz 0,002).

## 5. Wie gemessen wird

| Messung | Befehl | Braucht live SearXNG? |
|---|---|---|
| Kern-Zielmass (Fakten + Recherche) | `python eval/objective.py --split dev` (am Ende `--split both`) | nein, Replay |
| Serper, Exa, Reader | `python eval/tool_objective.py --tool serper --split dev --offline` | nein mit `--offline` |
| SEO-Anteil Top 3 (Marker im HTML) | `python eval/seo_share.py` | nein, Replay |
| Recherche-MRR je Engine, bewerten | `python eval/history_bench.py score --cassette eval/private/history-<...>.json` | nein |
| Recherche-MRR je Engine, neu abfragen | `python eval/history_bench.py run --field both` | ja, Dienst auf :8000 |
| Blindtest Quellen-Vertrauen | Vorbereitung wie `eval/private/zuverl-2026-10-13/blind_prepare3.py`, Urteil durch einen blinden Richter-Agenten, der nur `items.json` sieht | ja |
| Antwortzeit | `python eval/endpoint_bench.py`, `eval/load_bench.py` | ja |

Alle Befehle mit dem Python der Dienst-Umgebung (`.venv`). Ergebnisse nach `eval/private/`.
Replay heisst: Suchergebnisse und Seiten sind in `eval/private/replay` aufgezeichnet und werden
abgespielt. Getestet am 14.10.2026 auf dem Mac mit gesperrtem Netz: `objective.py --split dev`
lief vollstaendig offline (dev 0,934, Fakten identisch). Latenz ist im Replay nicht aussagekraeftig.
Fehlt eine Aufnahme, versucht der Code live zu laden. Offline schreibt er dann eine `.fail`-Datei.
Deshalb `eval/private/replay` vom Laptop nie zurueck auf den Mac kopieren.

## 6. Einrichtung auf dem Laptop

Platz: mindestens 25 GB frei. Zwei Python-Umgebungen, weil der Dienst transformers 4.44.2 und
das Training transformers 5.17.0 braucht:

- `.venv-train` nur fuer `train/` (Schnellstart oben).
- `.venv` fuer Dienst, Tests und Messungen: `pip install -r requirements.txt`.

### Windows

1. Python 3.12 von python.org (Haken "Add to PATH"), Git for Windows, aktueller NVIDIA-Treiber.
   Pruefen: `nvidia-smi` zeigt die RTX 3070 Ti.
2. `git clone https://github.com/sami-mag07/tavily-clone.git` (privat, Login per Browser), dann `cd tavily-clone`.
3. Training: Schnellstart. Zum Messen empfehle ich WSL2, weil SearXNG Docker braucht und der
   Dienst nur unter macOS und Linux getestet ist. Natives Windows reicht fuer das Training.
4. Wach halten: `run_night.ps1` verhindert den Ruhezustand selbst, solange es laeuft.
   Netzstecker dran. Deckel offen lassen oder unter Energieoptionen "Beim Zuklappen: Nichts unternehmen".

### Linux oder WSL2

1. WSL2: in PowerShell `wsl --install -d Ubuntu-24.04`, neu starten. Die GPU kommt ueber den
   Windows-Treiber, in WSL keinen NVIDIA-Treiber installieren. `nvidia-smi` in WSL muss die Karte zeigen.
2. Python 3.12: `sudo apt install python3.12 python3.12-venv git zip` (Ubuntu 24.04 hat 3.12).
3. Repo im Linux-Dateisystem klonen (`~/tavily-clone`), nicht unter `/mnt/c` (dort sehr langsam).
4. Wach halten: Unter Linux nutzt `run_night.sh` automatisch `systemd-inhibit`. Unter WSL2 schlaeft
   Windows trotzdem ein. Vorher in Windows-PowerShell: `powercfg /change standby-timeout-ac 0`,
   am Morgen zurueck mit `powercfg /change standby-timeout-ac 30`.

### Messdaten vom Mac (nur fuer Messungen, nicht fuers Training)

`eval/private/` ist gitignored und 1,6 GB gross. Auf dem Mac packen, direkt auf den USB-Stick
(der Mac-Speicher ist knapp):

```bash
train/make_bundle.sh --dry-run                     # Liste, Groesse, Schluessel-Suche
train/make_bundle.sh /Volumes/<STICK>/eval-bundle.zip
```

Inhalt: Replay-Aufnahmen, Reader-Aufnahmen, Jina-Cache, Blindtest, Score- und Tempo-Dateien,
dazu die Recherche-Kassetten und `search_eval.py` aus `~/.claude/skills/research-agent/`.
Ohne `.env`-Dateien und ohne Modelle. Dry-Run am 14.10.2026: 10.045 Dateien, ca. 1,6 GB unkomprimiert,
Schluessel-Suche ohne Fund.

Kopieren nur per USB-Stick, AirDrop oder lokalem Netz. Nie GitHub, nie Cloud. Auf dem Laptop:

```bash
python train/unpack_bundle.py /pfad/zu/eval-bundle.zip
cp train/env.laptop .env            # Windows: copy train\env.laptop .env
```

`unpack_bundle.py` legt `eval/private/` ins Repo und die Kassetten nach
`~/.claude/skills/research-agent/` (Windows: `%USERPROFILE%\.claude\skills\research-agent`).
`train/env.laptop` enthaelt keine Schluessel. Die SearXNG-Adresse darin muss
`http://127.0.0.1:8888` bleiben, sonst passen die Replay-Aufnahmen nicht.

### Ohne Docker und ohne SearXNG

Geht fuer: Training, Tests, `objective.py`, `tool_objective.py --offline`, `seo_share.py`,
`history_bench.py score`. Geht nicht fuer: `history_bench.py run`, Blindtest, Antwortzeiten,
den Dienst auf :8000 selbst. Mit Docker: `docker compose -f deploy/docker-compose.yml up -d searxng`
(SEARXNG_SECRET in `.env` setzen, siehe `train/env.laptop`).

## 7. Training des Rerankers (Phase 3 aus docs/finetuning-plan.md)

### Warum

Drei Feintunings am 12.10. sind gescheitert (`reranker-ft1`, `-ft2-a`, `-distill-1/2`).
Die Uebungsfragen stiegen, die Pruefungsfragen fielen (z. B. 0,858 auf 0,839). Grund: zu wenige
eigene Daten. Das Modell lernte die dev-Fragen auswendig. Jetzt also viele oeffentliche Daten
(ca. 1,6 Mio. Paare), nichts aus den eigenen Pruefungen.

### Daten (prepare_data.py, alles ohne Login, am 14.10.2026 geprueft)

| Quelle auf Hugging Face | Sprache | Menge Standard | Art |
|---|---|---|---|
| `sentence-transformers/msmarco` (bert-ensemble-mse) | en, de, es, fr | 1.125.000 | Lehrer-Scores |
| `sentence-transformers/msmarco-co-condenser-margin-mse-sym-mnrl-mean-v1` (triplet-hard-ids) | en, de, es, fr | 300.000 | harte Negative, 1/0 |
| `unicamp-dl/mmarco` (Uebersetzung, gleiche IDs) | de, es, fr | Texte zu beidem | |
| `lopozz/GermanDPRRetrieval` (GermanDPR) | de | ca. 46.000 | 1/0 |
| `DiscoResearch/germanrag` | de | ca. 8.000 | 1/0 |
| `nthakur/miracl-monolingual-sft` (MIRACL Train) | en, es, fr | ca. 95.000 | 1/0 |

Nicht nutzbar: `deepset/germandpr` und `deepset/germanquad` (Skript-Datensaetze, Quelle 404),
`miracl/miracl` (Skript, Korpus viele GB), `Cohere/miracl-*` (Login). MIRACL hat kein deutsches Train.
Jede MS-MARCO-Frage bekommt genau eine Sprache. 0,4 % der Fragen sind dev und nie im Training.
Fragen aus `eval/*.jsonl` und den Recherche-Aufgaben werden herausgefiltert.

### Training (train_reranker.py)

- Ausgangsmodell wie im Dienst: `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, 256 Token.
- Lehrer-Scores: MSE-Verlust. Vorher auf die Skala des Ausgangsmodells gebracht, damit die festen
  Schwellen im Dienst gueltig bleiben. 1/0-Daten: Binary-Cross-Entropy.
- fp16, Batch 64, Lernrate 1e-5, eine Epoche.
- Messung auf dev (NDCG@10 je Sprache) 12 Mal im Lauf und mindestens jede Stunde, mit Zwischenstand.
  Das Ausgangsmodell wird vorher gemessen. Bestes Modell liegt immer in `train/checkpoints/<name>/best/`.
- Frueher Abbruch nach 3 Messungen ohne Fortschritt.
- Nach Absturz oder Neustart: einfach `run_night` erneut starten. Es setzt am letzten Zwischenstand fort.

### Was ueber Nacht passiert

1. Daten: ca. 15 GB werden gestreamt (vier mMARCO-Sammlungen je 3 bis 3,6 GB), gespeichert wird
   nur, was gebraucht wird. Dauer 45 bis 120 Minuten, je nach Leitung.
2. Training: ca. 25.000 Schritte. Geschaetzt 1 bis 3 Stunden auf der 3070 Ti (nicht gemessen,
   der Mac hat nur den Rauchtest gemacht).
3. Export nach `models/reranker-mminilm-<datum>/` mit `model_card.json` und Ladeprobe wie im Dienst.

Gesamt 2 bis 5 Stunden. Platz: Daten ca. 3 GB, Zwischenstaende ca. 4 GB, `.venv-train` ca. 6 GB.

### Fortschritt pruefen

```bash
.venv-train/bin/python train/train_reranker.py --status     # Windows: .venv-train\Scripts\python ...
```

Zeigt Schritt, Loss, Restzeit, dev-Werte. Logs: `train/logs/night-*.log` (alles),
`train/logs/prepare.log`, `train/logs/train-*.log`, `train/logs/metrics.jsonl`. GPU: `nvidia-smi`.

### Rauchtest vorab (ca. 5 Minuten)

`run_night.ps1 -Quick` bzw. `bash train/run_night.sh --quick`. Laedt ca. 60 MB plus einmal das Ausgangsmodell (470 MB), trainiert 30 Schritte.
Danach `train/data/` loeschen, sonst nimmt der echte Lauf die kleinen Daten nicht neu.
Ergebnis landet in `models/reranker-mminilm-quick-<datum>/` und ist wertlos.

### Fehlerbehebung

| Problem | Loesung |
|---|---|
| `CUDA out of memory` | `run_night` halbiert den Batch selbst (64, 32, 16). Von Hand: `--batch 32 --grad-accum 2` |
| `CUDA verfuegbar: False` | torch ohne CUDA installiert. `pip uninstall torch`, dann Befehl aus dem Schnellstart |
| Platz voll | `train/data/.hf_cache` und alte `train/checkpoints/*/checkpoint-*` loeschen |
| Download bricht ab | `run_night` erneut starten. Fertige Quellen bleiben in `train/data/parts/` |
| Eine Quelle fehlt | steht in `train/data/manifest.json` unter `failed_sources`, der Rest laeuft weiter |
| Neu anfangen | `train/data/` und `train/checkpoints/` loeschen |
| Rechner ging schlafen | Energieeinstellung siehe Abschnitt 6, dann `run_night` erneut starten |

## 8. Zurueck auf den Mac

Nur den Ordner `models/reranker-mminilm-<datum>/` kopieren (ca. 470 MB, USB-Stick oder AirDrop),
nach `models/` im Repo auf dem Mac. Nicht kopieren: `train/data`, `train/checkpoints`, `eval/private`.

Die dev-Werte im Steckbrief stammen aus oeffentlichen Daten und entscheiden nichts. Beurteilt wird
nur auf dem Mac:

1. `RERANKER_MODEL=models/reranker-mminilm-<datum> .venv/bin/python eval/objective.py --split both`
   gegen den Stand ohne die Variable. Ein absoluter Pfad ist am sichersten. Dazu `eval/tool_objective.py` fuer serper, exa, reader.
2. Phase 2b aus `docs/finetuning-plan.md` (grosse echte Aufgaben). Ist noch nicht gebaut.
3. Freigabe-Regeln Phase 4: nichts schlechter (Toleranz 0,002), Antwortzeit hoechstens +0,2 s,
   Blindtest nicht schlechter. Erst dann `RERANKER_MODEL` in `.env` und Dienst neu starten.

## 9. Auftrag fuer Claude Code auf dem Laptop

Zum Einfuegen in eine neue Sitzung im Repo-Ordner:

```text
Lies train/LAPTOP.md komplett, danach docs/handoff.md. Arbeite die ganze Nacht ohne Rueckfragen.

1. Pruefe die Einrichtung (Abschnitt 6). Fehlt etwas, richte es ein. Nie uv sync.
2. Starte das Training im Hintergrund: Windows train\run_night.ps1, Linux/WSL2 bash train/run_night.sh.
   Pruefe stuendlich train/train_reranker.py --status. Bricht es ab, lies das Log, behebe die Ursache,
   starte neu (es setzt fort).
3. Parallel, wenn eval/private entpackt ist: Arbeite an den Zielen aus Abschnitt 4 in dieser
   Reihenfolge: Recherche-MRR (Aufgabe "politik" zuerst), SEO-Anteil, dann alles, was den Blindtest
   heben kann. Fasse app/quality.py, app/rerank.py und app/compat/serper.py nur an, wenn auf dem Mac
   niemand mehr daran arbeitet (in docs/loop-log.md nachsehen).
4. Messen nur offline mit Replay (Abschnitt 5). Optimiere nur auf --split dev. --split test nur
   einmal am Ende zur Kontrolle, nie zum Tunen. Eine Aenderung bleibt nur, wenn kein Messwert
   faellt (Toleranz 0,002) und die Tests gruen sind.
5. Regeln aus Abschnitt 2 gelten immer: keine Calls an Tavily/Serper/Exa, SearXNG hoechstens
   1 Anfrage pro Sekunde, Schluessel nur in .env, eval/private nie committen.
6. Jede uebernommene Aenderung: eigener Commit auf dem Branch laptop-<datum>, Zeile in
   docs/loop-log.md mit Messwerten vorher/nachher. Push nur diesen Branch, nie main.
7. Am Morgen: kurzer Bericht in docs/loop-log.md (was probiert, was uebernommen, alle Messwerte),
   Pfad des trainierten Modells und dessen model_card.json.
```
