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

## Phase 2: Trainingsdaten (Mac, ca. 1 Tag Rechenzeit, nebenbei)

1. Deine Problemfälle aus Phase 0 als harte Prüfung.
2. Harte Negative aus eigenen Läufen: SEO-Seiten, Shopping-Sender, Wörterbücher, Namensgleichheit.
3. Bewertungen durch Claude: für ca. 2.000 echte Recherche-Anfragen die Top 30 bewerten lassen
   (über claude-code-api, kostet nur Max-Kontingent, läuft gedrosselt über Nacht).
4. Offene Datensätze nur mit passender Lizenz: MS MARCO und mMARCO sind nur nicht-kommerziell.
   Solange der Dienst privat bleibt ok, für ein Produkt brauchen wir andere Quellen.
5. Ziel: 20.000 bis 50.000 bewertete Paare, sauber getrennt in Training, Übung, Prüfung.

## Phase 3: Training auf deinem Laptop (11 GB frei)

| Modell | Speicher beim Training | Erwartete Dauer* | Antwortzeit danach |
|---|---|---|---|
| mMiniLM (jetzt) | ca. 3 GB | 1 bis 2 h | gleich |
| bge-reranker-base | ca. 5 GB | 3 bis 5 h | ca. 1,5x |
| bge-reranker-v2-m3 mit LoRA | ca. 8 GB | 6 bis 10 h | ca. 3x, nur mit kürzeren Abschnitten tragbar |

*grobe Schätzung, hängt von Chip und Grafikkarte ab, die ich noch nicht kenne.

Ablauf: Repo auf den Laptop klonen, `train/train_reranker.py` (wird gebaut) mit dem
Datensatz starten, Abbruch sobald die Übungsfragen nicht mehr steigen. Fertiges Modell
nach `models/<name>-<datum>/` auf den Mac kopieren. Gemessen wird nur auf dem Mac,
sonst sind die Zeiten nicht vergleichbar.

## Phase 4: Freigabe-Regeln (automatisch)

Ein neues Modell geht nur live, wenn alles gleichzeitig stimmt:

- Prüffragen aller vier Tools nicht schlechter (Toleranz 0,002)
- Recherche-Ranking und deine Problemfälle besser
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
| sofort | Phase 5.1, 5.4, 5.5 (CI, Zeitlimit, Health) | ich, Mac |
| wenn du lieferst | Phase 0 einsortieren | du, dann ich |
| danach | Phase 2 Daten, Nacht-Läufe | ich, Mac |
| danach | Phase 3 Training | dein Laptop |
| danach | Phase 4 Freigabe, 5.2, 5.3, 5.6 | ich, Mac |

## Offene Fragen an dich

1. Welcher Laptop (Chip, Grafikkarte)? Davon hängen Modellwahl und Dauer ab.
2. Soll der Dienst irgendwann kommerziell laufen? Dann fallen MS MARCO und mMARCO weg.
3. Darf Claude über Nacht ca. 2.000 Anfragen bewerten (Max-Kontingent)?
