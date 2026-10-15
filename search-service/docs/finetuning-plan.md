# Plan: besseres Ranking-Modell und DevOps (Stand 13.10.2026)

Ziel: Die Suche sortiert gute Quellen weiter nach oben, ohne langsamer zu werden.
Messlatte: Recherche-Ranking der Tavily-Suche von 0,25 auf mindestens 0,28 (Original),
Prüffragen nicht schlechter, typische Antwortzeit hoechstens 1,2 s.

## Was wir schon wissen

- Code-Tuning ist ausgereizt: Nacht-Lauf Runde 1 und 2 brachten fast alles, 3 bis 5 kaum etwas.
- Drei Feintuning-Versuche am 12.10. sind gescheitert (`models/reranker-ft1`, `-ft2-a`,
  `-distill-1/2`): Übungsfragen stiegen, Prüffragen fielen (z. B. 0,858 auf 0,839).
  Grund: zu wenige eigene Fragen, das Modell lernt sie auswendig statt allgemein besser zu sortieren.
- Das Lehrer-Modell der Distillation lag selbst unter dem jetzigen Modell. Ein größeres
  Modell ist also nicht automatisch besser, das muss zuerst gemessen werden.
- Offen seit heute: Nach der Feinjustierung wirkten /search und /serper langsamer
  (0,98 s statt 0,82 s), gemessen mit nur einem Lauf. A/B-Test ist vorbereitet
  (Skript im Scratchpad, zweite Instanz auf :8001 mit `MIN_RESULT_SCORE=0 SERPER_WEAK_CE=-1000`).

## Phase 0: Deine Problemliste (du lieferst, ich sortiere ein)

Format pro Fall eine Zeile in `eval/private/feedback/<datum>.jsonl` (bleibt privat):

| Feld | Inhalt |
|---|---|
| `query` | die Anfrage wörtlich |
| `endpoint` | /search, /serper/search, /exa/search, /jina/r |
| `problem` | was schlecht war, ein Satz |
| `gut` | URLs, die oben stehen sollten (falls bekannt) |
| `schlecht` | URLs, die nicht oder weiter unten stehen sollten |

Ein Notizzettel reicht auch, ich baue die Zeilen daraus. Jeder Fall wird zuerst ein
Prüffall (darf nie ins Training), damit wir sehen, ob ein Modell ihn wirklich löst.

## Phase 1: Erst messen, dann trainieren (Mac, ca. 2 h, kein Training)

1. A/B-Test Feinjustierung an/aus, 30 frische Fragen, abwechselnd. Klärt, ob es langsamer wurde.
2. Drei fertige Ranking-Modelle ohne Training auf denselben Prüfungen vergleichen:
   jetziges mMiniLM (118 Mio. Parameter), bge-reranker-base (278 Mio.), bge-reranker-v2-m3 (568 Mio.).
3. Ergebnis: Lohnt ein größeres Modell schon ohne Training? Wenn keins besser ist,
   bringt Training eines größeren Modells vermutlich auch nichts, dann direkt Phase 2 mit mehr Daten.

## Phase 2: Viele Trainingsdaten (öffentliche Daten lädt der Laptop, eigene baut der Mac)

Nutzung ist privat und nicht kommerziell, damit sind auch Datensätze mit
Forschungslizenz erlaubt. Ziel: 1 bis 2 Millionen bewertete Paare statt ein paar Hundert.

| Quelle | Sprache | Menge (genutzt) | Lizenz |
|---|---|---|---|
| MS MARCO Passage mit harten Negativen und Lehrer-Scores (sentence-transformers) | en | ca. 500.000 | nur nicht-kommerziell |
| mMARCO (MS MARCO übersetzt) | de, es, fr | je ca. 200.000 | nur nicht-kommerziell |
| GermanQuAD / GermanDPR | de | ca. 15.000 | CC BY 4.0 |
| MIRACL (Wikipedia-Suche, Fragen von Menschen bewertet) | de, es, fr, en | ca. 30.000 | Apache 2.0 |
| Natural Questions / HotpotQA (mehrstufige Fragen) | en | ca. 100.000 | CC BY-SA |
| Claude bewertet echte Anfragen (Top 30 je Anfrage) | de, en | ca. 2.000 Anfragen = 60.000 Paare | eigene Daten |
| Harte Negative aus eigenen Läufen (SEO, Shopping, Wörterbücher, Namensgleichheit) | de, en | ca. 20.000 | eigene Daten |

Regeln: Nichts aus den Prüfungen (Phase 2b) darf ins Training, Abgleich über Anfrage
und URL. Eigene Daten werden im Training stärker gewichtet als die öffentlichen.

## Phase 2b: Prüfung an großen echten Aufgaben

Kleine Faktenfragen reichen nicht, entscheidend ist der echte Einsatz: eine ganze
Recherche mit vielen Suchen. Jede große Aufgabe hat eine Liste von Funden, die du
schon bestätigt hast (Gold).

| Aufgabe | Gold vorhanden | Gemessen wird |
|---|---|---|
| 9 Recherchen aus dem Programm-Tracker (research-agent tasks.json) | ja, Gold-Links | Gold-Seiten in Top 10/30, Platz des ersten Golds |
| Offshore-Kontakte (8 Suchen, Vergleich mit Claude-Websuche) | ja, Handbewertung | gefundene Personen, themenfremde Treffer |
| Deepwater-Markt (10 Teilbereiche, 161 Firmen) | ja, Firmenliste | Firmen-Abdeckung je Suche |
| Hackathon-Radar Herbst 2026 (95 Hackathons) | ja, Board | Abdeckung, Anmeldeseite statt Startseite |
| Meta Reality Labs (94 Personen) und NVIDIA GTC (28 Leads) | ja, Obsidian | gefundene Personen mit Rolle |
| deine Problemliste (Phase 0) | kommt von dir | Fall gelöst ja/nein |

Pro Aufgabe laufen alle Suchen gegen das alte und das neue Modell, Ergebnis ist eine
Zahl pro Aufgabe plus ein Gesamtwert. Ein Modell muss bei mindestens 4 von 6 Aufgaben
besser und bei keiner deutlich schlechter sein.

## Phase 3: Training auf deinem Laptop (RTX 3070 Ti Laptop, 8 GB Grafikspeicher)

Begrenzung ist der Grafikspeicher (8 GB), nicht der Arbeitsspeicher. Der i7-12900
bereitet nur die Daten vor.

| Modell | Grafikspeicher beim Training | Dauer für 1 Mio. Paare* | Antwortzeit danach |
|---|---|---|---|
| mMiniLM (jetzt, 118 Mio. Parameter) | ca. 3 GB | 2 bis 3 h | gleich |
| bge-reranker-base (278 Mio.) | ca. 6 GB, fp16 | 6 bis 9 h | ca. 1,5x |
| bge-reranker-v2-m3 (568 Mio.) | ca. 7 GB, nur mit LoRA und fp16 | 12 bis 20 h | ca. 3x |

*grobe Schätzung, Abschnitte mit 256 Token, eine Epoche.

Empfehlung: Erst mMiniLM mit allen Daten (eine Nacht). Bringt das die großen Aufgaben
nach vorn, bleibt die Suche gleich schnell. Nur wenn nicht, bge-reranker-base.

Ablauf:
1. Auf dem Laptop: Python 3.12, PyTorch mit CUDA, Repo klonen (unter Windows am
   einfachsten in WSL2).
2. Öffentliche Datensätze lädt der Laptop selbst (ca. 2 bis 4 GB, der Mac-Speicher ist knapp).
   Vom Mac kommt nur das kleine Paket mit den eigenen Daten (Claude-Bewertungen, harte Negative).
3. `train/train_reranker.py` starten (wird gebaut): speichert jede Stunde einen
   Zwischenstand, misst nebenbei auf den Übungsfragen, hört auf, wenn die nicht mehr steigen.
4. Fertiges Modell nach `models/<name>-<datum>/` auf den Mac kopieren, gemessen wird nur hier.

## Phase 4: Freigabe-Regeln (automatisch)

Ein neues Modell geht nur live, wenn alles gleichzeitig stimmt:

- Prüffragen aller vier Tools nicht schlechter (Toleranz 0,002)
- große Aufgaben (Phase 2b): mindestens 4 von 6 besser, keine deutlich schlechter
- deine Problemfälle besser
- typische Antwortzeit hoechstens +0,2 s, langsamste 5 % hoechstens +0,5 s
- 0 Serverfehler in 150 Live-Anfragen
- Blindtest nicht schlechter

## Phase 5: DevOps

1. **CI auf GitHub:** Bei jedem Push laufen Tests und pyflakes (GitHub Actions, ohne Netz).
2. **Modell-Register:** Jedes Modell in `models/` mit Steckbrief (Daten, Datum, alle Messwerte).
   Umschalten und Zurückrollen nur über `RERANKER_MODEL` in `.env` plus Neustart.
3. **Nächtlicher Regressionstest (launchd, 03:00):** Replay-Benchmarks ohne Netz plus 20
   Live-Anfragen. Fällt ein Wert, kommt eine Meldung aufs Handy (über Emil).
4. **Zeitlimit pro Anfrage:** Höchstens 8 s, danach Teilergebnis oder sauberer Fehler
   (heute hing eine Anfrage 32 s).
5. **Tiefer Health-Check:** /health prüft auch SearXNG und den Reranker, launchd startet
   bei Ausfall neu. Log mit Antwortzeiten pro Endpunkt, ohne Anfragetexte.
6. **Release-Ablauf:** Branch, CI grün, Freigabe-Regeln grün, Merge in `main`,
   Neustart, 10 Rauchtest-Anfragen. Schlägt einer fehl, automatisch zurück zum letzten Modell.

## Reihenfolge

| Wann | Was | Wer |
|---|---|---|
| sofort | Phase 1 (A/B-Test, drei Modelle vergleichen) | ich, Mac |
| sofort | Phase 2b große Aufgaben als Prüfung bauen | ich, Mac |
| sofort | Phase 5.1, 5.4, 5.5 (CI, Zeitlimit, Health) | ich, Mac |
| parallel | Phase 2 eigene Daten und Claude-Bewertungen über Nacht | ich, Mac |
| wenn du lieferst | Phase 0 einsortieren | du, dann ich |
| danach | Phase 3 Training, erste Nacht mMiniLM | dein Laptop |
| danach | Phase 4 Freigabe, 5.2, 5.3, 5.6 | ich, Mac |

## Geklärt (13.10.2026)

- Laptop: i7-12900, RTX 3070 Ti Laptop (8 GB Grafikspeicher).
- Nicht kommerziell, MS MARCO und mMARCO sind erlaubt.
- Viele Trainingsdaten, Prüfung an großen echten Aufgaben.

## Noch offen

1. Darf Claude über Nacht ca. 2.000 Anfragen bewerten (Max-Kontingent)?
2. Laptop: Windows oder Linux? Davon hängt die Einrichtung ab.
