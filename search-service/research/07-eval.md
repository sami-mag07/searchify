# 07 Eval: Wie Such-APIs fuer LLMs fair gemessen werden (Stand 11.10.2026)

Alle Zahlen aus den verlinkten Seiten gezogen (r.jina.ai / HF- und GitHub-API). Vendor-Zahlen sind mit "V" markiert (Anbieter bewertet sich selbst).

## 1. Tavilys Zahlen in den oeffentlichen Benchmarks

| Quelle | Setup | Tavily | Konkurrenz |
|---|---|---|---|
| [tavily-search-evals](https://github.com/tavily-ai/tavily-search-evals) (V, 2025) | volle SimpleQA, Snippets -> gpt-4.1 -> offizieller Grader | 93,3 % | Perplexity Search 85,9, Serper 82,2, Brave 76,1, Exa 71,2 |
| selbe Repo, Document Relevance (QuotientAI, dynamisches Set) | LLM-Relevanz | 83,0 % | Perplexity 71,2, Serper 58,1, Brave 56,2, Exa 51,3 |
| [Tavily Blog 11.08.2026](https://www.tavily.com/blog/tavily-ranks-1-on-sealqa-and-simpleqa) (V) | max_results=10, Snippets -> gpt-5.4 mini -> gpt-4.1-mini mit SealQA-Grader | SealQA-0 49,5 %, SealQA-Hard 55,7 %, SimpleQA Verified 97,6 % | "Platz 1" vor Exa Auto, Parallel Advanced, Perplexity, Brave, You.com (Konkurrenzwerte nur im Bild) |
| [Parallel Benchmarks](https://parallel.ai/articles/how-we-evaluate-web-search-apis) (V, 09.09.2026), Agent + LLM-Judge | SimpleQA Verified (100) / BrowseComp (50) / WideSearch (100) | Frontier: 92 % / 66 % / 55,9; Low-cost: 94 % / 32 % / 47,9 | Parallel Adv. 97/74/57,6; Perplexity 95/74/53,5; Exa Auto 91/70/55,9 |
| [Tavily-Blog Exa vs Parallel](https://www.tavily.com/blog/exa-vs-parallel-benchmarking-retrieval-apis-for-ai-agents-in-2026) | von Parallel uebernommene Tabelle, ohne Tavily | keine Tavily-Werte | Exa/Parallel: FRAMES 87/87, WebWalker 74/73, BrowseComp 40/51, HLE 57/56, FreshQA 78/79, SealQA 41/41 |
| [AIMultiple agentic search](https://aimultiple.com/agentic-search) (unabhaengig, Dez 2025) | 100 Queries, Top-5, GPT-5.2 Judge | Platz 5 von 8, Agent Score 13,67; 998 ms | Brave 14,89 (669 ms), Firecrawl 14,58, Exa 14,39, Parallel Pro 14,21; nur Brave signifikant besser als Tavily |
| [Openbenchmarks](https://openbenchmarks.com/web-search/exa-vs-tavily-vs-brave-vs-perplexity) (unabhaengig, 26.09.2026), 300 Firmen-News-Fragen | Antwort-Recall@5 + Accuracy | advanced 93,0 % (4,2 s, 16 $/1k), basic 87,7 % (1,7 s) | Exa fast 99,3 % (569 ms), Perplexity low 97,3, Brave LLM-Context 94,0 |
| selbe Seite, Coding-Doku-Tickets | Task Completion | advanced 60,0 %, basic 59,0 % | Exa deep 83,0, Perplexity high 77,7 |
| [Openbenchmarks Best Tools](https://openbenchmarks.com/web-search/best-search-tools-for-ai-agents) (15.09.2026) | 100 Dev-Fragen | basic 51,0 % (Platz 8 von 14) | Perplexity low 77,3, Firecrawl 70,3, Exa fast 66,3 |
| selbe Quelle, Multi-Turn Firmensuche | F1 | advanced 41,1 (Platz 3), basic 36,5 | Exa deep 45,4, Exa instant 43,3 |
| [Artificial Analysis Search Index](https://artificialanalysis.ai/agents/search-api) (unabhaengig, 08/2026) | DeepSearchQA 900 + BrowseComp 200 + Omniscience 600, Provider-Swap | in den angezeigten 12 von 26 Zeilen nicht enthalten; [Decoder](https://the-decoder.com/new-benchmark-ranks-search-apis-for-ai-agents-on-quality-cost-and-speed) nennt Tavily in der Startliste, Spanne 65 bis 75 | Perplexity 80, Parallel Adv. 75, Brave 75, Exa Auto 74; ohne Suche 33 |

Lesart: Auf Single-Shot-Faktfragen ist Tavily vorn oder gleichauf (SimpleQA 91 bis 98 %, also gesaettigt wie bei uns). Auf schweren Aufgaben (BrowseComp, Coding-Doku, Frischefakten) liegt es in unabhaengigen Boards im Mittelfeld hinter Exa/Perplexity. Genau dort muss unser Set diskriminieren.

## 2. Methodik der Boards

- **Antwort-Genauigkeit end-to-end (Standard 2026).** Parallel begruendet es: Recall@k sagt nur, ob eine relevante Seite erschien; ein Agent kann trotzdem scheitern, weil der Ausschnitt den einen Satz nicht enthaelt ([Parallel](https://parallel.ai/articles/how-we-evaluate-web-search-apis)). Variante "Single-Step" (Tavily SealQA): Top-10-Snippets -> fester Reader -> fester Grader; nur der Anbieter variiert.
- **Provider-Swap mit fixem Agent** (Artificial Analysis, [Methodik](https://artificialanalysis.ai/methodology/search-api)): ein Modell (GPT-5.6 Luna medium) als Kandidat und Grader, 25 Turns, 10 Ergebnisse, native Payload, Contamination-Filter fuer BrowseComp-/SimpleQA-Leaks, No-Search-Baseline (33) als Referenz.
- **Pointwise LLM-Relevanz** (AIMultiple: relevant bool, Quality 1-5, Noise, Quelltyp; Exa: [api-evals](https://exa.ai/blog/api-evals) und [evals-at-exa](https://exa.ai/blog/evals-at-exa)). Exa diskutiert Pointwise vs Pairwise (Elo) vs Listwise, NDCG, "Verification gap" (LLM bewertet, was es nicht weiss) und loest ihn mit erwarteten Fakten/Kriterien im Grader-Prompt. Exa weist auf falsche Negative bei MS MARCO (Gold-URL-Labels) hin: bei offenem Index sind Gold-URLs unvollstaendig.
- **Gold-URL-Recall nur im festen Korpus**: [BrowseComp-Plus](https://github.com/texttron/BrowseComp-Plus), ca. 100K Dokumente, Evidence- und Gold-qrels, Metriken Recall@5/100/1000 und nDCG@10 (trec_eval). Fair, aber nicht gegen Live-APIs wie Tavily anwendbar.
- **Statistik**: AIMultiple Paired Bootstrap (10 000 Resamples, 95 %-CI); Parallel rechnet vor, dass 50 Fragen etwa +-14 Punkte Rauschen bedeuten, 46 vs 44 ist ein Gleichstand. Das gilt auch fuer unsere 30 Fragen: +-15 Punkte.
- **Linkup** ([Studie](https://www.linkup.so/blog/evaluating-ai-search-systems-on-complex-queries), [Repo](https://github.com/LinkupPlatform/standard-benchmark)): 600 Queries (150 echter Traffic + 450 synthetisch), Blind-Judge GPT-4o-mini, Faithfulness (RAGAS-artig, claim-weise gegen Snippets), Entity-Coverage, Quellen-Diversitaet. Vendor-Studie.
- **Openbenchmarks**: offener Code, oeffentliches Beispielset, geheimes Held-out-Set; Fragen zu Fakten nach dem Cutoff der Modelle, damit Parametrik nicht hilft ([Parallel-Zusammenfassung](https://parallel.ai/articles/how-we-evaluate-web-search-apis)).
- Schwaechen, die alle nennen: kleine Samples, Vendor-Auswahl der Suites, Leaks von BrowseComp/SimpleQA ins Web, Latenz vom Messort abhaengig, Judge nicht publiziert (Parallel gibt das selbst zu).

## 3. Frei nutzbare Datensaetze (Lizenz per HF-/GitHub-API geprueft)

| Datensatz | Lizenz | Nutzen fuer uns |
|---|---|---|
| [SimpleQA Verified](https://huggingface.co/datasets/google/simpleqa-verified) (1000, DeepMind) | MIT | gesaettigt (alle >= 91 %), nur Regressionstest |
| [SealQA](https://huggingface.co/datasets/vtllms/sealqa) (Seal-0, Seal-Hard, "fast-changing", Update-Zyklus, letzter 21.09.2026) | Apache-2.0 | beste Wahl: widerspruechliche Suchergebnisse, Single-Step messbar, Tavily 49,5/55,7 % als Referenz |
| [FRAMES](https://huggingface.co/datasets/google/frames-benchmark) (824 Multi-Doc-Fragen, Wikipedia-Gold-Links) | Apache-2.0 | Gold-URL-Recall moeglich (Wikipedia-URLs), nur EN |
| [FreshQA](https://github.com/freshllms/freshqa) (Google Sheets, regelmaessig aktualisiert) | Apache-2.0 laut GitHub | tagesaktuelle Antworten, "fast-changing/never-changing/false-premise" |
| [BrowseComp-Plus](https://huggingface.co/datasets/Tevatron/browsecomp-plus) (830 Fragen, Korpus 100K) | MIT | Gold-URL-Recall im festen Korpus, Rerankertest fuer unser Clone-Retrieval |
| BrowseComp (OpenAI, simple-evals) | MIT, Daten verschluesselt (Canary) | zu schwer/leaked fuer 30 Fragen |
| [MKQA](https://huggingface.co/datasets/apple/mkqa) (10K Fragen, 26 Sprachen inkl. de) | CC-BY-3.0 laut HF (Repo sagt CC BY-SA 3.0) | einzige grosse DE/EN-parallele Faktfragen-Quelle; Antworten aus 2020, teils veraltet, Long-Tail wenig |
| [GermanQuAD/GermanDPR](https://arxiv.org/html/2104.12741v1) | CC BY 4.0 (germanrag-Variante: CC BY-SA 4.0) | DE-Retrieval gegen Wikipedia-Passagen, kein Live-Web |
| [Tavily eval generator](https://github.com/Eyalbenba/tavily-web-eval-generator) und tavily-search-evals | MIT | dynamisches Set zu Themen nach Wahl; Runner gleich fuer Exa/Brave/Serper |

Nicht verifiziert: Lizenz von WebWalkerQA und DeepSearchQA (nicht geprueft).

## 4. Bauplan fuer ein kleines, diskriminierendes DE/EN-Set

1. **Metrik wechseln.** Regex auf Top-k-Content bleibt als billiger Vorfilter, aber normalisieren (Whitespace, Zero-Width und Soft-Hyphen raus, NFKC, casefold; "H yper T ext" wird so "hypertext"). Das Artefakt ist ein Extraktionsfehler, kein Treffer. Zusaetzlich Treffer nur zaehlen, wenn Antwort UND mindestens ein Frage-Ankerbegriff im selben 300-Zeichen-Fenster stehen.
2. **LLM-Judge nach dem Tavily-Muster**: fixer Reader (Top-5-Content -> kurze Antwort), fixer Grader mit Gold-Antwort, CORRECT/INCORRECT/NOT_ATTEMPTED (SimpleQA-Grader-Prompt, MIT). Alles gecacht, ein Modell.
3. **Schwierigkeit kalibrieren**: Jede Frage zuerst ohne Suche an ein LLM; was das Modell ohne Suche richtig beantwortet, fliegt raus (AA nutzt die No-Search-Baseline: 33 %). Rest soll bei Tavily 40 bis 70 % liegen.
4. **Mix** (50 Fragen statt 30, sonst +-14): 15 Long-Tail (Antwort tief im Seitentext, nicht im Snippet), 15 frisch (< 6 Wochen alt, nach Modell-Cutoff), 10 Widerspruch/veraltet (SealQA-Stil, alte Seite gewinnt im Snippet), 10 Multi-Hop (FRAMES-Stil). Paarweise je DE und EN pro Sachverhalt.
5. **Gold-URL-Spalte zusaetzlich**: pro Frage 1 bis 3 akzeptierte Domains. Metrik "Antwort im content UND URL-Domain erlaubt" trennt Zufallstreffer von echten. Exa warnt: Gold-URLs sind bei offenem Index unvollstaendig, nur als Zusatz.
6. **Paired Bootstrap** (AIMultiple) fuer Vergleiche Clone vs Tavily; Unterschiede unter 10 Punkten bei n=50 nicht berichten.
7. **Frische monatlich erneuern**: Fragen aus aktuellen Meldungen, FreshQA-/SealQA-Updates uebernehmen; alte Fragen archivieren (Leak-Gefahr, siehe AA-Contamination-Filter).

## 5. Zehn schwere Testfragen (Antworten am 11.10.2026 gegen Quellen geprueft)

Regex gilt nach der Normalisierung aus 4.1; `(?s)(?=...)` heisst: alle Teile muessen im content stehen.

| # | Sprache | Frage | Regex | Beleg |
|---|---|---|---|---|
| 1 | EN | Who won the 2026 Nobel Prize in Physics and for what? | `(?i)Halzen.*IceCube|IceCube.*Halzen` | [nobelprize.org](https://www.nobelprize.org/prizes/physics/2026/summary) |
| 2 | DE | Auf welchen Betrag steigt der gesetzliche Mindestlohn zum 1. Januar 2027? | `14,60` | [DGB](https://www.dgb.de/service/ratgeber/mindestlohn) |
| 3 | DE | Wie hoch ist 2026 die monatliche Beitragsbemessungsgrenze in der gesetzlichen Krankenversicherung? | `5\.?812,50` | [TK](https://www.tk.de/firmenkunden/versicherung/beitraege-faq-und-mehr/zahlen-und-grenzwerte/sozialversicherungs-rechengroessen-2026-2203234) |
| 4 | DE | Wie hoch ist die monatliche Beitragsbemessungsgrenze der Rentenversicherung 2026 (West)? | `8\.?450` | [LV1871](https://www.lv1871.de/private-rentenversicherung/wiki/beitragsbemessungsgrenze) |
| 5 | EN | What is the default value of io_method in PostgreSQL 18? | `(?i)io_method.{0,80}\bworker\b|\bworker\b.{0,80}default` | [pgPedia](https://pgpedia.info/i/io_method.html), [Crunchy](https://www.crunchydata.com/blog/get-excited-about-postgres-18) |
| 6 | EN | Which PEP defines the criteria for supported status of free-threaded Python, and in which release was the experimental tag removed? | `(?s)(?=.*PEP 779)(?=.*3\.14)` | [PEP 779](https://peps.python.org/pep-0779) |
| 7 | EN | Which company agreed in February 2026 to acquire Tavily, and who is its CEO? | `(?s)(?=.*Nebius)(?=.*Volozh)` | [Bloomberg](https://www.bloomberg.com/news/articles/2026-02-10/nebius-agrees-to-buy-ai-agent-search-company-tavily-for-275-million), Wikipedia Nebius |
| 8 | EN | Which grader and candidate model does the Artificial Analysis Search Index use? | `(?i)GPT-5\.6 Luna` | [AA Methodik](https://artificialanalysis.ai/methodology/search-api) |
| 9 | DE | Wie viele Dokumente umfasst der feste Korpus von BrowseComp-Plus ungefaehr? | `(?i)100\s?(K|000|\.000|tsd)|100\.?195` | [GitHub](https://github.com/texttron/BrowseComp-Plus) ("~100K") |
| 10 | EN | At which price did Bloomberg report Nebius would buy Tavily? (Widerspruchsfrage, Wikipedia nennt ca. 400 Mio) | `275` | [Bloomberg](https://www.bloomberg.com/news/articles/2026-02-10/nebius-agrees-to-buy-ai-agent-search-company-tavily-for-275-million) vs [Wikipedia](https://en.wikipedia.org/wiki/Nebius_Group) |

Hinweis zu Frage 9: genaue Dokumentzahl (100195) nicht belegt, deshalb breite Regex. Frage 10 mit "Bloomberg" in der Frage; Treffer `400` ohne `275` zaehlt als falsch. Fragen 1, 7, 10 sind Frischefakten, die in Modellen ohne Suche meist fehlen; 2 bis 4 haben viele veraltete Vorjahres-Snippets (12,82 / 8.050 / 5.512,50 sind die Fallen). Fragen 2 bis 4 sind in den Top-Snippets oft schon enthalten und taugen daher eher als "Veraltungs-Falle" denn als Tiefen-Test; 5 und 6 brauchen Fliesstext aus Doku-Seiten.

## 6. Quellenindex

Primär: [AIMultiple](https://aimultiple.com/agentic-search), [Tavily evals repo](https://github.com/tavily-ai/tavily-search-evals), [Tavily SealQA-Blog](https://www.tavily.com/blog/tavily-ranks-1-on-sealqa-and-simpleqa), [Parallel Methodik](https://parallel.ai/articles/how-we-evaluate-web-search-apis), [Parallel Benchmarks](https://parallel.ai/benchmarks), [Artificial Analysis](https://artificialanalysis.ai/methodology/search-api), [The Decoder zu AA](https://the-decoder.com/new-benchmark-ranks-search-apis-for-ai-agents-on-quality-cost-and-speed), [Openbenchmarks](https://openbenchmarks.com/web-search/exa-vs-tavily-vs-brave-vs-perplexity), [Linkup](https://www.linkup.so/blog/evaluating-ai-search-systems-on-complex-queries), [Exa evals](https://exa.ai/blog/evals-at-exa), [FreshQA](https://github.com/freshllms/freshqa), [BrowseComp-Plus](https://github.com/texttron/BrowseComp-Plus).

Offene Luecken: Tavily-Wert im AA Search Index nicht gesehen; Konkurrenzwerte im Tavily-SealQA-Blog nur als Bild; kein DE-spezifischer oeffentlicher Such-API-Benchmark gefunden (nur MKQA/GermanQuAD als Rohmaterial).
