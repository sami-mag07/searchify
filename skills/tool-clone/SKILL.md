---
name: tool-clone
description: >
  Clean-room Nachbau der Funktionsweise eines Third-Party-Tools oder einer API,
  besonders LLM- und RAG-Dienste (Such-APIs wie Tavily, Agent-Tools, Endpunkte).
  Black-box: Verhalten aus Eingabe und Ausgabe ableiten, Pipeline rekonstruieren,
  eine eigene, oft bessere und individuellere Implementierung spezifizieren,
  bauen und gegen das Original auf Verhaltens-Paritaet messen. Mit hartem
  Legalitaets-Gate (ToS, clean-room). Auslöser: "tool nachbauen", "reverse
  engineer tavily", "eigene version von", "wie funktioniert dieses tool genau",
  "api nachbauen", "rag-dienst nachbauen", "clone this tool".
license: MIT
---

# Tool-Clone (clean-room Nachbau von Diensten)

Ziel: die Funktionsweise eines fremden Tools oder einer API black-box verstehen
und daraus eine eigene, saubere Implementierung bauen, die gleich gut oder besser
und individueller ist. Leit-Beispiel: ein Tavily-artiger RAG-Such-Dienst.

Das ist zuerst eine Frage der Zulaessigkeit, dann der Methodik. Verhalten
beobachten ist urheberrechtlich frei (EU 2009/24; EuGH SAS v. WPL; Google v.
Oracle), aber ein Vertrag oder ToS kann es trotzdem verbieten. Die Angriffsflaeche
ist fast immer der Vertrag, nicht das Urheberrecht.

## Phase 0: Legalitaets-Gate (vor dem ersten Request, Pflicht)

1. ToS und Doku des Ziels lesen. Klauseln notieren zu: Reverse Engineering,
   Bau eines Konkurrenzprodukts, Veroeffentlichung von Benchmarks/Vergleichen,
   Nutzung der Outputs, Identitaetsverschleierung, Rate-Limits.
2. Verbietet die ToS das Vorgehen (Beispiel Tavily: untersagt RE von "structure,
   algorithms", API-Zugriff zum Bau eines Konkurrenzprodukts und die Weitergabe
   von Leistungsanalysen), dann NICHT unter einem Account des Ziels proben.
   Stattdessen eine dieser Routen:
   - schriftliche Erlaubnis einholen, oder
   - ein Ziel mit offener Lizenz oder permissiver ToS waehlen, oder
   - nur oeffentliche Doku plus eigene Ground-Truth-Benchmarks nutzen und aus
     offenen Bausteinen bauen (der Default fuer Tavily).
3. Entscheidung und Begruendung in eine Datei schreiben (Audit-Spur).
4. ToS-Klausel woertlich plus Fassung und Abrufdatum in die Audit-Datei (eine
   Legalitaetsannahme darf nicht veralten).
5. Das ist keine Rechtsberatung. Fuer einen kommerziellen Nachbau vorher einen
   Anwalt fragen.

**Nicht umgehbar:** Ohne protokollierte Erlaubnis oder permissive ToS wird KEIN
einziger Request an das Ziel gesendet. Verbietet die ToS Probing oder Reverse
Engineering (Tavily-Default), dann faellt Phase 2 komplett weg und der Agent
fragt den Nutzer, statt weiterzulaufen. Kein Default-Weiterlauf ins Probing.

## Phase 1: Spec aus oeffentlichen Quellen

1. Doku, OpenAPI, SDK-Quellen, offizielle Blogposts auswerten, Capability-Map
   bauen (fuer Tavily: search, extract, crawl, map plus Parameter wie
   search_depth, topic, include_answer, Antwort-Felder answer/results/score).
2. Jede Aussage taggen als "dokumentiert" oder "behauptet" (Hersteller-Blogs ueber
   Ranking und Caching sind behauptet, keine Spezifikation).

## Phase 2: Probing (NUR wenn die ToS es erlaubt)

Diese Phase entfaellt vollstaendig, sobald die ToS Probing oder Reverse
Engineering verbietet. Beim Tavily-Default wird keine einzige Query unter einem
Tavily-Account gesendet, der Nachbau laeuft dann nur aus Phase 1 (oeffentliche
Doku) plus eigenen Benchmarks. Ein Golden Master wird nur auf einem
probing-erlaubten Ziel gespeichert und selbst dann NICHT dem bauenden Team als
Referenz vorgelegt (clean-room, siehe Phase 3): er dient allein dem Spec-Team
zum Ableiten der Spec, nie als Trainings- oder Kopiervorlage.

1. Input-Output-Matrix: ein Parameter pro Lauf, plus Grenzfaelle (leere und
   ueberlange Queries, Unicode, Exact-Match, Datumsgrenzen).
2. Jede Query mehrfach wiederholen, um Nichtdeterminismus und Cache-Effekte zu
   messen. Rohantworten als Golden Master nur fuer das Spec-Team speichern.
3. Latenz- und Fehlermuster erfassen: Statuscodes, 400er bei ungueltiger
   Kombination, Rate-Limits, gemeldete gegen eigene Antwortzeit.
4. Aus dem Verhalten nur testbare Hypothesen ableiten. Interne Modelle (Embedding,
   Reranker) bleiben Hypothese, nicht Befund. Rate-Limits respektieren, nie
   massenhaft abfragen um den Index nachzuziehen (das ist Model Extraction).

## Phase 3: Spec schreiben und trennen (clean-room)

1. Spec-Team schreibt eine Verhaltensspezifikation ohne Code und ohne
   Original-Output-Texte.
2. Implementierungs-Team (oder der bauende Agent) sieht nur die Spec, nie die
   Original-Antworten im Kontext.
3. Eigene Mehrwerte getrennt als Anforderung markieren (andere Quellen,
   Domain-Priorisierung, deutsche Sprache, eigenes Ranking).

## Phase 4: Bauen und Verifizieren

1. Aus offenen Bausteinen zusammensetzen (Beispiel RAG-Suche): Such-Backend
   (SearXNG, oder bezahlte Such-API mit eigener ToS), Fetch und Extraktion
   (trafilatura, crawl4ai), Re-Ranking (bge-Reranker, rerankers), Dedup,
   LLM-Summary. Referenz-Pipeline: gpt-researcher.
2. Eval-Set bauen: oeffentliche Benchmarks (SimpleQA, SealQA) plus eigene Queries
   aus echten Nutzungsfaellen.
3. Paritaet messen, nicht behaupten: RBO und nDCG auf den Ergebnislisten,
   Quellen-Overlap bei Top-k, Antwortgenauigkeit mit festem Reader und festem
   Grader, Faithfulness mit ragas, dazu Latenz und Kosten pro Anfrage.
4. Beim LLM-Richter Positionsbias pruefen (Reihenfolge tauschen), das Urteil per
   Zweitlauf oder festem Seed auf Reproduzierbarkeit testen und mit einer kleinen
   menschlichen Stichprobe abgleichen.
5. "Besser" belegen mit Konfidenzintervallen (Bootstrap), vorab festgelegtem
   Eval-Set und offen genannten Schwaechen. Ohne Tests auf den eigenen Domaenen
   bleibt "individueller" eine Behauptung.

## Rechtliche Leitplanken

Do:
- Funktionen, Schnittstellen, Parameter nachbauen (urheberrechtlich frei).
- Eigener Code, eigene Prompts, eigenes Branding, eigene Daten.
- Oeffentliche Doku und eigene Ground-Truth-Daten als Quelle.
- Bei jeder Datenquelle ToS, robots.txt und Nutzungsvorbehalt (Paragraf 44b UrhG)
  pruefen.

Don't:
- Keinen Account beim Ziel anlegen, um es zu analysieren, wenn die ToS das
  verbietet.
- Keine Output-Texte des Ziels (Antworten, Snippets) als Trainings- oder
  Referenzdaten fuer den Nachbau nutzen (Distillation ist der klarste ToS-Bruch).
- Such-Ergebnisse von Anbietern nicht speichern oder als Datenbank aufbauen, wenn
  deren ToS das verbietet (Beispiel Brave).
- Keine Anti-Bot-Schutzmassnahmen umgehen, keine Tarn-Accounts oder Proxies.
- Keine Marke, Domain, Doku-Texte oder Logos kopieren, keine Verwechslung
  erzeugen.

## Harte Refusals

- System-Prompts per Injection herausholen.
- Massenhaftes Abfragen zur Index-Extraktion.
- Vergleichszahlen veroeffentlichen, wenn die ToS das verbietet.

## Bausteine (nur Landschaft, keine Empfehlung ungeprueft)

Such-Backend SearXNG (AGPL); Extraktion trafilatura (Apache), crawl4ai (Apache),
firecrawl (AGPL); Reranking FlagEmbedding/bge (MIT), rerankers (Apache);
Referenz gpt-researcher (Apache); Eval ragas (Apache). Lizenz jeder Komponente
vor Uebernahme pruefen. Bing Search API ist seit 08/2025 eingestellt.

## Grenzen

- Die interne Pipeline eines Dienstes ist aus dem Verhalten nur als Hypothese
  ableitbar, nie als exakte Spezifikation.
- Reine Server-APIs ohne lokale Programmkopie fallen meist nicht unter die
  Dekompilier-Schranken des Urheberrechts, darum dominiert der Vertrag.
