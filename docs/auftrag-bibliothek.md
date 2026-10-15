# Auftrag: Themen-Bibliothek + Bauplan (von Sami am 14.10.2026, aus der Bahn)

Erst anfangen, wenn Sami "los" sagt. Einstieg vorher lesen: docs/handoff.md (Stand, Fallen, Messwerkzeuge).

## Ausgangslage

- Groesste Luecke gegen Tavily: Gold-Domains in den Top 30, Nachbau 0,11 gegen Tavily 0,15 (eval/history_bench.py).
- Gold-Links in ~/.claude/skills/research-agent/evals/tasks.json stammen schon aus dem Programm-Tracker (9 Themen, je ~30 Links, Stand 01.10.2026). Der Tracker liefert also kaum neue Trainingsdaten.
- Reranker-Feintuning wurde dreimal verworfen (zu wenige Fragen, nur In-Sample-Gewinn).
- Fokus-Index war am 14.10. vertagt, ist jetzt freigegeben unter Samis Bedingungen: automatische Pipeline mit aktuellen Daten, Live-Suche laeuft immer mit, keine offensichtlichen Wissensluecken.

## Was Sami will (seine Bilder)

"Wie eine Bibliothek, wo er das richtige Buch nimmt":
1. Pro Thema (Hackathon, Planspiel/MUN, Stipendien, Foerderung, Jugenddelegierte, ...) eine eigene kleine Gold-Sammlung statt einem grossen Topf ("weniger Daten-Spaghetti").
2. Die Suche erkennt das Thema der Anfrage und greift nur auf das passende "Regal" zu, zusaetzlich zur Live-Suche.
3. Lernt mit jeder Abfrage und jedem Prompt, wie der Nutzer Informationen haben will (bevorzugte Seitentypen, Anmeldeseite statt Startseite, Sprache, Tiefe).
4. Darf den Scraper auf lange Sicht nicht spuerbar verlangsamen: Lookup im Index muss im einstelligen Millisekundenbereich bleiben, p95 der Suche darf nicht steigen (fair messen wie am 14.10.).

## Messung ohne Zirkelschluss (Pflicht)

- Die Bibliothek darf nur aus Funden bis 01.10.2026 gebaut werden, Messung gegen spaetere Funde (Zeit-Trennung).
- Pruefsatz: neue Programme aus Programme Runde 8 (04.10.), Runde 9 (14.10.) und Hackathon-Radar (13.10.); Daten in den jeweiligen Board-Artifacts bzw. Scratch-/Projektordnern suchen. Schwaecher als echte Gold-Links, weil Sami sie nicht bestaetigt hat: im Ergebnis so ausweisen.
- Zusaetzlich immer: eval/objective.py dev/test, Blindtest-Quellenvertrauen, Fehlerquote, p95.
- Fragen aus dem Test nie im Training/Index.

## Schritte

1. Bauplan als Artifact (siehe unten), damit Sami den Ueberblick hat, bevor gebaut wird.
2. Pruefsatz bauen und Ausgangswert messen.
3. Themen-Bibliothek: Themen-Erkennung (leicht, kein LLM im heissen Pfad), pro Thema Regal mit Domains/Seiten, automatische Auffrischung (Pipeline), Einmischen in die Kandidaten vor dem Reranker.
4. Lernen aus Nutzung: Klicks/uebernommene Links aus research-agent und Tracker als Signal, kleines Sortier-Modell (z. B. LightGBM) auf einfachen Merkmalen. Nur behalten, wenn test steigt.
5. Messen, Skeptiker-Review, Ergebnis in docs/handoff.md und loop-log.md.

## Bauplan-Artifact (Samis Wunsch)

Eine Seite, am Handy lesbar, die den ganzen Scraper zeigt:
- Jedes Bauteil mit Ort (Datei/Ordner, Port, launchd-Dienst), Status (live, experimentell, verworfen, geplant) und was es tut.
- Das "Rezept": der Weg einer Anfrage Schritt fuer Schritt (Spracherkennung, SearXNG-Engines, Fusion, Abruf/Extract, Chunking, Reranker, QUALITY_PRIOR, demote_farms, Cache, Antwort) als Diagramm.
- Je Schritt die Stellschrauben und wo man etwas dazubauen kann ("nach Wahl Sachen adden"), inkl. Platz fuer die Themen-Bibliothek.
- Messwerte gegen die Originale (Tabelle besser/gleich/schlechter aus der README).
- Samis Stil-Regeln beachten: keine Emojis, keine Gedankenstriche, kein Slop.

## Zusatz 15.10.2026: Camoufox als letzte Lade-Stufe (Sami: "Camoufox klingt gut")

- Ziel: Seiten, die nach normal, curl_cffi (FETCH_FALLBACK) und Headless-Browser noch blockiert sind, mit Camoufox (quelloffener getarnter Firefox) laden. Springt nur bei diesen Seiten an, normale Suchen werden nicht langsamer.
- Erst messen: die ~33 Seiten, die am 14.10. blockiert blieben (3,9 % von 856, siehe loop-log 14.10. 01:18), durch Camoufox schicken und zählen, wie viele gerettet werden. Nur einbauen, wenn es spürbar rettet.
- Einbau als eigene Stufe hinter dem Headless-Browser, mit festem Zeitlimit; SSRF-Schutz wie in render.py (nur öffentliche Ziele, jede Weiterleitung prüfen). Version pinnen, vorher vom security-Agent vetten lassen.
- ShardBrowser NICHT einbauen (Chromium-Kern closed source vom Hersteller-CDN).

## Zusatz 15.10.2026 nachmittags (Samis Entscheidungen, Board https://claude.ai/artifact/7EtKvb8UvpbzQ9VmjVHBhs)

- Nur Open Source und kostenlos (Mojeek und andere Bezahl-APIs raus).
- Themen-Bibliothek: jedes Regal bekommt ein eigenes Nutzerprofil (Rocchio) als "Eröffnungsseite" mit Notizen: was Sami in diesem Thema übernimmt, Seitentypen, Sprache. Ältere Beispiele zählen weniger, max. ~50 je Profil.
- Freigegeben: Engine-Messung (Gold-Domains je Engine aus der Kassette), Bandit (fragt immer alle Engines, je Thema eine primär, lernt live), LambdaMART aus Klicks, Verweildauer, Camoufox live schalten. IPS geparkt bis Klick-Lernen läuft.
- README nach dem Live-Schalten von Camoufox: Satz wie "Von 856 Trefferseiten aus echten Suchen bleiben mit allen Lade-Stufen 13 gesperrt (1,5 %); Seiten hinter Login oder Bezahlschranke zählen nicht dazu." Erst eintragen, wenn Camoufox wirklich live ist, Zahlen vorher am Live-Stand nachmessen.
