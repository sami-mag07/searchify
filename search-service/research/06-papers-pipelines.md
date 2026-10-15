# 06 Papers 2023-2026: Retrieval-Pipelines fuer suchmaschinengestuetzte LLMs

Stand 11.10.2026. Alle Zahlen habe ich selbst im Volltext der Papers (arXiv-HTML/PDF) nachgelesen, nicht aus Abstracts uebernommen. "Vendor" = Herstellerbehauptung, nicht peer-reviewed.
Fundliste: research/seen.txt

## 1. Snippets vs. Volltext

| Befund | Zahl | Quelle |
|---|---|---|
| FreshPrompt nutzt NUR Snippets (plus Datum, Titel, Answer Box, Related Questions), kein Volltext. GPT-4 FreshQA strict | 28.6 -> 75.6 % (+47 Punkte); mit 15 Evidenzen 77.6 % | [FreshLLMs 2310.03214](https://arxiv.org/abs/2310.03214) |
| Anzahl Snippets (GPT-4, strict) | 1: 61.4 / 5: 70.6 / 10: 75.6 / 15: 77.6 % | ebd. |
| Reihenfolge: Top-Treffer ans Ende des Prompts oder nach Datum sortiert (neu am Ende) | bis +2.2 Punkte gegenueber Zufall | ebd. |
| Answer Box weglassen | 75.6 -> 74.2 | ebd. |
| Agenten mit nur Snippets (BrowseComp, 3 Modelle) vs. bester Dokumentzugriff (Fetch-then-Explore) | 27.5 -> 44.5 / 37.5 -> 52.5 / 62.0 -> 70.5 (+17 / +15 / +8.5 Punkte). WideSearch (Tabellen): Snippet-only nur 23-43 Row-F1, "essentially unsolvable from snippets" | [Fetch-then-Explore 2608.02097](https://arxiv.org/abs/2608.02097) |
| Snippet-only Agent stellt am meisten Suchen (70-101 vs. 58-81 mit Seitenzugriff) | mehr Queries, wenn Seiten nicht lesbar | ebd. |
| Volltext hilft nicht immer: Volltext kann den Ranker bei navigationalen Queries verschlechtern, hilft bei Tail-/langen Queries | 12k Query-Dokument-Paare aus Bing-Logs | [Less is Less, 2201.08721](https://arxiv.org/abs/2201.08721) (2022, Grenzfall) |
| Search-o1: Agentic RAG vs. normales RAG (Multi-Hop, EM) | +23.2 Punkte; Reason-in-Documents (Seite vor dem Einspeisen verdichten) +4.7 % gegenueber rohen Seiten (RAgent: erst Top-10-Snippets, dann URL-Fetch) | [Search-o1 2501.05366](https://arxiv.org/abs/2501.05366) |
| Einzelnes Dokument reicht in Search-o1 schon, um Direct Reasoning und RAG mit 10 Dokumenten zu schlagen | qualitativ | ebd. |
| Snippet-Qualitaet entscheidet das Verhalten: Brave/Tavily/Firecrawl mit gleicher Genauigkeit (25/25/26 von 100), aber unterschiedliche Evidenz vor dem Fetch | | [2607.10198](https://arxiv.org/abs/2607.10198) |
| Vendor: Exa Highlights, 500 Zeichen = Genauigkeit der ersten 8000 Zeichen Seite (SimpleQA), 16x weniger Tokens; 4k Zeichen Highlights besser als 32k Volltext | nicht unabhaengig geprueft | [Exa Blog](https://exa.ai/blog/highlights-for-agents) |
| Blog-Nachbau: Highlights 75 % bei ~397 Tokens vs. Volltext 65 % bei ~878 Tokens (SimpleQA) | Einzelversuch, Blog | [QueryBurst](https://queryburst.com/blog/exa-highlights/) |

Lesart: Fuer Faktenfragen und Aktualitaet tragen Snippets den Grossteil. Volltext bringt Mehrwert bei schweren Multi-Hop-Fragen und Sammelaufgaben, und dann gezielt (Passagen, nicht ganze Seiten). Das stuetzt "Snippets sofort ranken + Seiten nur als Zusatz".

## 2. Latenz und Kosten des Seitenladens

- WebGLM: Seiten parallel laden, 98 % in 5 s; synchron wuerden 2-3 Minuten anfallen. Gesamtzeit Retrieval Mittel 5.3 s, Median 4.07 s, P90 10 s. Haupt-Engpass ist das Fetchen. [WebGLM 2306.07906](https://arxiv.org/abs/2306.07906)
- Folge fuer ein 300-ms-Budget: ein Teil der Seiten kommt nicht an. Das Design (Snippets als Basis, Seiten als Bonus) ist deshalb richtig.
- HtmlRAG: echte HTML-Seite im Schnitt >80K Tokens, davon >90 % CSS/JS/Kommentare. 20 Seiten = 1.6M Tokens -> 135K nach Cleaning -> 8K nach Embedding-Pruning -> 4K nach Generativ-Pruning. Struktur (Ueberschriften, Tabellen) erhalten schlaegt Plain Text in 6 QA-Datensaetzen; Block-Baum mit 256 Woertern, fein 128. [HtmlRAG 2411.02959](https://arxiv.org/abs/2411.02959)
- Search Best Practices: Reranking weglassen kostet deutlich (MS MARCO MRR@10: BM25 11.65 -> monoT5 31.78), monoT5 4.5 s pro Query bei 1000 Kandidaten (Hardware nicht angegeben), TILDEv2 0.02 s bei MRR@10 27.83. Fuer wenige Kandidaten (Dutzende) liegt monoT5 entsprechend weit darunter. [2407.01219](https://arxiv.org/abs/2407.01219)
- MindSearch: 300+ Seiten in 3 Minuten ueber Multi-Agent; 10 komplexe Fragen: Menschen 19h17min, MindSearch 23 min. Einzelsuche ("single-turn", was die meisten KI-Suchen nutzen) reicht fuer leichte Fragen und ist am schnellsten. [MindSearch 2407.20183](https://arxiv.org/abs/2407.20183)

## 3. Query-Rewriting und Multi-Query

- MindSearch-Ablation: Multi-Query-Erzeugung hat von den WebSearcher-Bausteinen den groessten Einfluss. Mehrere Engines (Google/Bing/DDG), Ergebnisse nach URL zusammengefuehrt, dann LLM waehlt Seiten. [2407.20183](https://arxiv.org/abs/2407.20183)
- DMQR-RAG: 4 Rewrite-Strategien mit unterschiedlicher Informationsmenge. P@5 +14.46 % gegenueber bester Baseline auf FreshQA, ca. +8 % auf HotpotQA. Alle 4 Rewrites zusammen bringen auf FreshQA Rauschen; adaptive Auswahl nutzt im Schnitt 2.48 statt 4 Rewrites (-40 %) und verbessert Acc um +2.17 (Llama3-8B). Industrie-Test (15 Mio. Nutzerqueries): H@5 +2.0 %, P@5 +10.0 %. Pseudo-Antwort-Rewrite (PAR) traegt am meisten bei. [2411.13154](https://arxiv.org/abs/2411.13154)
- Gegenstimme: Auf TREC DL19 bringt reines Query-Rewriting (nDCG@10 67.89 vs. 70.20) und Decomposition (66.10) keine Verbesserung, kostet aber 3-6x Latenz (7.8 s / 15 s vs. 2.6 s). HyDE +5.2 nDCG@10 (75.44), Hybrid (BM25+Dense) bei nur 3.2 s. [2407.01219](https://arxiv.org/abs/2407.01219)
- Vereinigung verschiedener Rewrite-Strategien: +12.5 HIT@10 (51.70 vs. 39.22) auf Enterprise-Daten; auf einfachen Sets (AmbigNQ) schlechter als die beste Einzelmethode (-2.5), RRF hilft dort auch nicht, weil die Listen fast identisch sind (Jaccard@10 0.55-0.99). Ein kostenbewusster Router vermeidet den Verlust. [Better Together 2609.05637](https://arxiv.org/abs/2609.05637)
- Rewrite-Retrieve-Read: Rewriter vor der Websuche, trainierbar per RL, konsistente Gewinne bei offener und Multiple-Choice-QA. [2305.14283](https://arxiv.org/abs/2305.14283)

## 4. Fusion mehrerer Engines

- Retrospektive Vereinigung von Brave, Tavily, Firecrawl: 44 von 100 Fragen mit Evidenz abgedeckt gegenueber 26 bei bestem Einzelanbieter, also 18 Punkte Spielraum. [2607.10198](https://arxiv.org/abs/2607.10198)
- Tavily hat laut selber Studie den groesseren Rang-1-Anteil an stuetzenden Treffern, Brave die groessere Snippet-Flaeche, Firecrawl wird mit breiterer Exploration verbunden. ebd.
- RRF (k=60) ist scorefrei und braucht kein Training. Bei Lexikal+Dense: Convex Combination schlaegt RRF (MS MARCO NDCG@1000 0.454 vs. 0.425, NQ 0.542 vs. 0.514; RRF lag auf MS MARCO sogar unter reinem Dense 0.441). RRF ist parameterempfindlich und generalisiert out-of-domain schlechter. Bei Web-Engines gibt es keine vergleichbaren Scores, also bleibt RRF oder Rang-Mittel, am besten mit eigenem k und Engine-Gewichten pro Fragetyp. [Fusion Functions 2210.11934](https://arxiv.org/abs/2210.11934)
- RAG-Fusion (Multi-Query + RRF): Antworten umfassender, driften aber ab, wenn generierte Queries zu weit von der Originalfrage liegen. Originalquery immer mitfuehren (auch DMQR-RAG betont das). [2402.03367](https://arxiv.org/abs/2402.03367)

## 5. Diversitaet (MMR)

- Frisch (07.10.2026): MMR/Diversifizierung schadet auf sauberen Kandidaten-Pools (Relevanz, Coverage, Antwortqualitaet), hilft bei Multi-Evidenz-Fragen, wenn der Pool Beinahe-Dubletten enthaelt. Regel: nur diversifizieren, wenn die effektive Zahl verschiedener Dokumente in den kNN-Top-k unter dem Evidenzbedarf der Frage liegt. [2610.09412](https://arxiv.org/abs/2610.09412)
- Kontrollexperiment: Duplikate und Paraphrasen verbessern die Antwort nicht, Dokumente verschiedener Genres (News, Blog usw.) mit relevanter Info verbessern die Korrektheit um 17-47 %. [2608.13956](https://arxiv.org/abs/2608.13956)
- MMR/FPS erhoehen den Recall der ausgewaehlten Saetze/Chunks vor dem LLM. [2502.09017](https://arxiv.org/abs/2502.09017)
- Lesart fuer "Merge je Quelle": Pro-Domain-Deckel plus Dubletten-Erkennung (near-duplicate) ist wichtiger als globales MMR. Webtreffer syndizieren und kopieren viel.

## 6. Chunk-Groesse fuer Web-Passagen

- Fixe Chunks, mehrere Datensaetze: 64-128 Tokens optimal bei kurzen Fakten-Antworten, 512-1024 bei Aufgaben mit breitem Kontext; Embedding-Modelle reagieren verschieden. [2505.21700](https://arxiv.org/abs/2505.21700)
- Best Practices (Lyft-Korpus, Faithfulness / Relevancy): 2048: 80.4 / 91.1; 1024: 94.3 / 95.6; 512: 97.6 / 97.4; 256: 97.2 / 97.8; 128: 95.7 / 97.2. Optimum 256-512 Tokens. Sliding Window 97.41 / 96.85 vs. small2big 96.67 / 95.37 (Retrieval). [2407.01219](https://arxiv.org/abs/2407.01219)
- Semantisches Chunking lohnt den Rechenaufwand nicht gegenueber fixer Groesse. [2410.13070](https://arxiv.org/abs/2410.13070)
- Kontext-Erhalt: Contextual Retrieval wahrt Zusammenhang besser, kostet aber mehr; Late Chunking effizienter, aber weniger relevant. [2504.19754](https://arxiv.org/abs/2504.19754)
- Lesart: 100 Woerter (grob 130-150 Tokens) liegen im Bereich 64-256, passend fuer Fakten. HtmlRAG zeigt Bloecke an Ueberschriften auszurichten statt blind alle 100 Woerter zu schneiden.

## 7. Weitere Lehren aus den Papers

- Vorauswahl-Qualitaet: WebGLM fand, dass bis zu 30 % der Top-Kontexte eines Dense-Retrievers irrelevant sind (Contriever: nur 68.6 % relevant; LLM-In-Context-Auswahl 90.2 %). Retriever-Genauigkeit fuer "nuetzliche Referenz": TF-IDF 46.85, BM25 40.33, Contriever 18.54, LLM-destilliert 69.36 %. BM25 allein ist also als Vorauswahl schwach; ein Cross-Encoder dahinter ist richtig. [2306.07906](https://arxiv.org/abs/2306.07906)
- Gegenpol 2026: Auf BrowseComp-Plus reicht in Agenten-Schleifen ein gut eingestellter lexikalischer Retriever bei genug Tiefe. [Pi-Serini 2605.10848](https://arxiv.org/abs/2605.10848) (nur Abstract gelesen)
- Reranker-Reihenfolge fuer den Prompt: "reverse" (relevantester Treffer ans Ende) lieferte in den Best Practices den besten Avg (0.483) und stimmt mit FreshPrompt ueberein (Top-Treffer am Ende).

## Offene Punkte
- Keine Studie vergleicht direkt "Snippet vs. 100-Wort-Chunks aus 300-ms-Fetch" bei gleicher Latenz. Das muss ein eigener Test (SimpleQA/FreshQA-Teilmenge) klaeren.
- Exa-Highlights-Zahlen sind Herstellerangaben.
- Search-o1: Tabellenwerte pro Datensatz nicht einzeln gegengeprueft, nur die im Text genannten Durchschnitte.
- MindSearch Table 3 (Ablation) Zahlen nicht extrahiert, nur die Aussage "Multi-Query hat den groessten Einfluss".
