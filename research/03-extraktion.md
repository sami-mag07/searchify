# 03 Haupttext-Extraktion: schnell und gut (Stand 11.10.2026)

Frage: Welche Main-Content-Extraktion ist bei guter Qualität am schnellsten? Ist-Zustand: trafilatura 1.12.2, favor_precision, no_fallback, ThreadPool, ca. 10 Seiten pro Anfrage, 300 ms Budget.

## Ergebnis in 5 Zeilen

1. Sofort: `favor_precision=True` rausnehmen (Standardmodus, `no_fallback=True` behalten). Eigene Messung: F1 0.915 auf 0.958, dabei nicht langsamer (8.6 gegen 10.5 ms/Seite).
2. Schnellster brauchbarer Extraktor: resiliparse `extract_plain_text(main_content=True)`: 1.2 ms/Seite, F1 0.878 bis 0.897 (eigene Messung), also etwa 8x schneller als trafilatura.
3. Bester Kompromiss bei knappen 300 ms: erst resiliparse, trafilatura nur bei dünnem Ergebnis. Eigene Messung: F1 0.90 bei 2 ms/Seite (nur Artikel; auf Foren/Produkt-Seiten ist resiliparse laut WCXB eher besser als trafilatura).
4. HTML abschneiden bringt fast nichts: 400 KB Cut spart 8 %, 100 KB Cut kostet 0.04 bis 0.30 F1, weil der Artikel hinter großem Inline-Skript-Müll liegt. Besser: Download-Limit nicht für Extraktion nutzen, sondern Zeitbudget pro Seite.
5. Neuronale Extraktoren (ReaderLM-v2, MinerU-HTML) sind mit 1.6 bis 10 s/Seite auf A100 für 300 ms ausgeschlossen.

## Eigene Messung (reproduzierbar)

Setup: Apple M3, Python 3.9 bzw. 3.12, trafilatura 1.12.2, resiliparse 1.0.9, 181 ScrapingHub-Artikelseiten (Median 115 KB, p90 289 KB), Maß: Wort-Bag-F1 gegen `articleBody` (grob, nicht identisch mit dem offiziellen Shingle-F1; Rechner war mit Load 7 bis 13 belegt, absolute ms sind eher pessimistisch, Verhältnisse stabil). Bestwert aus 3 Läufen je Seite. Skripte: `/private/tmp/claude-501/-Users-samimagdouli/9f2dc6f1-4ac4-4cf3-8099-2848095eac3d/scratchpad/ext/h.py` (nur Hybrid und Threads; der Rest lief als Wegwerf-Skripte).

| Variante | F1 | ms/Seite Mittel | p90 | p99 | 10 Seiten seriell |
|---|---|---|---|---|---|
| trafilatura 1.12.2 favor_precision + no_fallback (Ist) | 0.915 | 10.5 bis 12.8 | 17 bis 22 | 38 bis 54 | 105 bis 128 ms |
| trafilatura Standard + no_fallback | 0.958 | 8.6 bis 10.4 | 15 bis 18 | 36 bis 43 | 86 bis 104 ms |
| trafilatura Standard + no_fallback, Tabellen aus | 0.954 | 10.5 | 18 | 37 | 105 ms |
| trafilatura favor_precision mit Fallback (Default) | 0.956 | 25 (Lauf 1) | 42 | 106 | 250 ms |
| trafilatura Standard mit Fallback | 0.956 | 17.7 | 31 | 59 | 177 ms |
| resiliparse main_content | 0.878 | 1.2 | 2.0 | 4.2 | 12 ms |
| resiliparse main_content, links/list_bullets/alt_texts/form_fields aus | 0.897 | 1.2 | 2.0 | 4.2 | 12 ms |
| readability-lxml 0.9 | 0.953 | 17.4 bis 23.7 | 26 bis 42 | 118 bis 143 | 174 ms |
| justext 3.0.2 | 0.718 | 11 bis 16 | 18 bis 27 | 31 bis 43 | 113 ms |
| inscriptis (kein MCE) | 0.653 | 10 | 17 | 27 | 101 ms |
| selectolax-Text nach Entfernen von nav/footer/script (kein MCE) | 0.774 | 0.7 | 1.3 | 3.2 | 7 ms |
| Hybrid: resiliparse, bei unter 120 Wörtern trafilatura Standard | 0.899 | 2.1 | 3.4 | - | 21 ms |
| Hybrid: Schwelle 300 Wörter | 0.902 | 2.6 | 5.4 | - | 26 ms |

Parsing allein: lxml.html.fromstring 1.2 ms (Median), selectolax/Lexbor 0.5 ms. Das Parsen ist also nur etwa 12 bis 15 % der trafilatura-Zeit; der Rest sind XPath-Kaskade und Bereinigung.

Abschneiden (trafilatura Standard, no_fallback), F1 / Median ms:
- unbegrenzt: 0.958 / 9.0
- erste 400 KB roh: 0.916 / 8.1
- erste 300 KB roh (fp): 0.854 / 7.9
- erste 150 KB roh (fp): 0.777 / 7.0
- erste 100 KB roh (fp): 0.653 / 6.0
- script/style/svg per Regex entfernen, dann 200 KB: 0.958 / 9.2 (Strip kostet 0.6 ms, spart nichts)
- script/style per Regex oder selectolax vorab entfernen: keine Beschleunigung (D/H: nicht schneller als B)
Fazit: Abschneiden lohnt nicht. Wenn Speicher/Download begrenzt werden muss, dann nach Strip von script/style/svg/Kommentaren und nicht unter 200 KB.

Parallelität, 10 zufällige Seiten pro Batch, 30 bis 40 Batches, Rechner unter Last:
- trafilatura seriell 92 bis 143 ms; ThreadPool 8: 47 bis 51 ms (ca. 2 bis 2.8x); ProcessPool 8 (warm): 41 ms (3.5x); 4 Threads 50 bis 92 ms (stark lastabhängig).
- resiliparse seriell 13 ms; 4 Threads 5 ms; 8 Threads 4 ms.
- Threads skalieren also brauchbar (lxml und Lexbor geben die GIL während Parsen/Serialisierung frei), ProcessPool ist nur ca. 20 % besser, kostet aber Pickle und Startzeit. Pool einmal beim Start anlegen und warm halten.
- Mit Resiliparse ist die Extraktion von 10 Seiten (ca. 5 bis 12 ms) vernachlässigbar gegenüber dem Netzwerk; das 300-ms-Budget gehört dem Fetch.

## Fremde Benchmarks (Links)

### Trafilatura-Evaluation, Stand 02.10.2026 (990 Dokumente, Python 3.13, Segment-F1, "Diff." = Zeit relativ zur Baseline)
Quelle: https://trafilatura.readthedocs.io/en/latest/evaluation.html und https://github.com/adbar/trafilatura/blob/master/docs/evaluation.rst

| Paket | F1 | Zeit relativ |
|---|---|---|
| trafilatura 2.3.0 standard | 0.926 | 2.9x |
| trafilatura 2.3.0 precision | 0.922 | 3.1x |
| trafilatura 2.3.0 fast | 0.920 | 2.2x |
| trafilatura 2.3.0 recall | 0.919 | 2.0x |
| magic-html 0.1.8 | 0.889 | 3.5x |
| justext 3.0.2 (custom) | 0.862 | 2.2x |
| readability-lxml 0.9 | 0.853 | 3.0x |
| news-please 1.6.16 | 0.836 | 20.9x |
| Baseline (Text-Markup) | 0.814 | 1x |
| resiliparse 1.0.9 | 0.811 | 0.3x (schneller als die Baseline) |
| goose3 3.1.22 | 0.810 | 10.6x |
| boilerpy3 | 0.807 | 1.6x |
| newspaper4k 0.9.6 | 0.803 | 6.8x |
| inscriptis, bs4, html_text, html2text | 0.66 bis 0.69 | 0.6x bis 2.9x (reiner Text, keine Extraktion) |

Wichtig: resiliparse 0.3x gegen trafilatura 2.0x bis 3.1x ist ein Faktor 7 bis 10 in der Zeit, passt zu meiner Messung (8x). Ältere Stände (2022: trafilatura 1.2.2 fast 4.8x, precision 9.4x, standard 7.1x) zeigen, dass 2.x deutlich schneller ist. Die Dokumentation sagt: trafilatura 2.3.1 braucht Python >= 3.10 (PyPI), unser Pin 1.12.2 ist alt.

### Bevendorff, Gupta, Kiesel, Stein 2023 (SIGIR, 8 Datensätze, ca. 3985 Seiten, 14 Extraktoren, ROUGE-LSum)
Paper: https://dl.acm.org/doi/10.1145/3539618.3591920, PDF https://downloads.webis.de/publications/papers/bevendorff_2023c.pdf, Code https://github.com/chatnoir-eu/web-content-extraction-benchmark
- Macro-Mean F1: Trafilatura 0.883 (bester Einzel-Extraktor im Mittel), Readability 0.861 (bester Median, 0.970), Resiliparse 0.859, DOM Distiller 0.858, Web2Text 0.841, Boilerpipe 0.834. Ensembles ("Best weighted") 0.899.
- Resiliparse: Median-Recall 0.993, aber Median-Precision nur 0.940, d.h. liefert zu viel Rand mit.
- Das Paper hat keine ms-Tabelle; es nennt Resiliparse als auf "Präzision und Geschwindigkeit" ausgelegt. Aussage "no single extractor performs best on all types" wird in WCXB wiederholt.

### WCXB (Foley, arXiv 2605.21097, 20.05.2026; 1497 Dev-Seiten, 7 Seitentypen)
Paper: https://arxiv.org/abs/2605.21097, HTML https://arxiv.org/html/2605.21097v1, Daten http://webcontentextraction.org, Blog https://murroughfoley.com/web-content-extraction-benchmark

| System | F1 | ms/Seite |
|---|---|---|
| rs-trafilatura (Rust, Autor ist der Benchmark-Autor, Quelle laut Hydrafetch nicht mehr auffindbar) | 0.859 | 44 |
| MinerU-HTML 0.6B (A100, vLLM) | 0.827 | 1,570 |
| Resiliparse | 0.797 | 28 |
| Trafilatura | 0.791 | 97 |
| dom-smoothie | 0.762 | 26 |
| ReaderLM-v2 1.5B (A100, vLLM) | 0.741 | 10,410 |
| Newspaper4k | 0.720 | 1,825 |
| jusText | 0.707 | - |
| Readability | 0.674 | 785 |

- Nach Seitentyp (F1, Trafilatura / Resiliparse): Artikel 0.924 / 0.871, Dokumentation 0.888 / 0.883, Service 0.751 / 0.815, Forum 0.575 / 0.758, Collection 0.518 / 0.586, Produkt 0.562 / 0.608, Listing 0.550 / 0.620. Für Websuche (gemischte Seitentypen) ist Resiliparse also nicht schlechter, bei Nicht-Artikeln besser; Preis: mehr Boilerplate (22.8 % gegen 6.6 % ohne-Snippet-Einschluss).
- Held-out-Test (511 Seiten): Trafilatura 0.841, Resiliparse 0.817.
- Neuronal rechnet sich nicht: ReaderLM-v2 liegt unter 6 Heuristiken, braucht ca. 10 s/Seite.
- Achtung: Einzelautor-Preprint, rs-trafilatura vom Autor selbst gebaut und auf dem Dev-Set getunt.
- Gegenprüfung durch Dritte (Hydrafetch, Vendor, 23.08.2026, WCXB-Dev, Wort-F1): rs-trafilatura 0.847, trafilatura 0.813, trafilatura recall 0.788, resiliparse 0.771, jusText 0.691, readability 0.617. Link: https://github.com/Hydrafetch/extraction-benchmark. Ranking deckt sich (trafilatura vor resiliparse vor jusText vor readability), allerdings zeigt dieses Bild Trafilatura vor Resiliparse, das WCXB-Paper umgekehrt (0.791 gegen 0.797). Beides innerhalb der Streuung.

### ScrapingHub article-extraction-benchmark (181 Artikel, 4-Gramm-Shingle-F1)
https://github.com/scrapinghub/article-extraction-benchmark: trafilatura 0.5.1 F1 0.945 (Precision 0.925, Recall 0.966), go_readability 0.943. Jüngere Zahl aus WCXB-Paper: trafilatura 0.958, rs-trafilatura 0.967. Artikel sind weitgehend gelöst (alle Systeme >= 0.87 in WCXB).

### Mehrsprachig: SIGIR 2025 "Multilingual Benchmarking of Main Content Extractors"
https://maurelf.users.greyc.fr/docs/conferences/SIGIR_2025_paper_1968.pdf. Readability schlägt die meisten Modelle in der Mehrheit der Sprachen (F1 0.962 Griechisch, 0.862 Polnisch, 0.840 Russisch, 0.672 Chinesisch). Relevant, falls Suchergebnisse nicht nur Deutsch/Englisch sind; Details nicht gegengeprüft.

### Java-Vergleich (OSTI), nur Hinweis
https://www.osti.gov/servlets/purl/2429881: Trafilatura F1 0.937 (P 0.978, R 0.92) und Readability als beste Python-Bibliotheken.

### Neuronal
- ReaderLM-v2, Jina: https://arxiv.org/abs/2503.01151, https://huggingface.co/jinaai/ReaderLM-v2 (1.5B, bis 512K Token). Für uns zu langsam (WCXB: 10.4 s/Seite, F1 0.741).
- MinerU-HTML / AICC: https://arxiv.org/abs/2511.16397. 0.6B-Modell, auf MainWebBench (7887 Seiten) ROUGE-N F1 81.8 % gegen Trafilatura 63.6 %; Stärke bei Code, Formeln, Tabellen (Markdown). Offline-Pipeline für Korpusbau, keine 300-ms-Option; Eigenangabe der Autoren.

### Parser-Geschwindigkeit
Resiliparse-Doku, Benchmark auf 42015 Common-Crawl-Dokumenten (Titelextraktion): Resiliparse/Lexbor 1150 Dok/s, Selectolax/Lexbor 1122 Dok/s, Selectolax/MyHTML 781 Dok/s, lxml etwa 24x langsamer. https://resiliparse.chatnoir.eu/en/latest/man/parse/html.html. Eigene Messung bestätigt: Lexbor-Parse 0.5 ms gegen lxml 1.2 ms (Median 115 KB).

## Hinweise zu Alternativen
- jusText: schlechter und nicht schneller (F1 0.72 bei 11 ms, eigene Messung); nur sinnvoll für Korpusbau.
- readability-lxml: F1 gut, aber 2x trafilatura-Zeit und p99 bis 143 ms (Ausreißer bei großen Seiten); WCXB: 785 ms/Seite, F1 0.674. Nicht als Hauptweg.
- newspaper4k: langsam (1.8 s/Seite in WCXB), Fehler auf manchem HTML (trafilatura-Doku). Raus.
- inscriptis, html2text, markdownify, bs4: machen keine Haupttextwahl (F1 0.58 bis 0.69), nur Konvertierung. markdownify/html2text wären nur für das Formatieren eines schon gewählten Hauptknotens nützlich.
- boilerpy3: F1 0.807 bei 1.6x, kein Gewinn gegen resiliparse.
- resiliparse-Installation: Wheels erst ab Python 3.10 (macOS arm64/x86_64 und manylinux vorhanden, pypi.org/project/resiliparse); unter Python 3.9 baut es nicht (eigener Versuch gescheitert). Ihr Stack hat 3.9.6 lokal: Python >= 3.10 nötig (trafilatura 2.3.1 verlangt das auch).

## Empfehlung für den Dienst (mit Zahlen)

1. `favor_precision` weg, `no_fallback=True` bleibt: F1 0.915 auf 0.958 (eigene Messung, Artikel), Zeit gleich oder besser. Falls Boilerplate-Reinheit wichtiger ist als Recall, `favor_precision` nur für Nicht-Artikel-Domains testen; Foren/Produkt-Seiten nicht pauschal beurteilen (siehe WCXB).
2. Zweistufig: resiliparse (1.2 ms) als erste Stufe, trafilatura Standard (9 ms) nur wenn Ergebnis unter 120 Wörter oder Zeit übrig ist. Gesamt 2.1 ms/Seite statt 10.5, F1 0.90. Mit Qualitätsmaß prüfen: eure eigenen Fragen in `eval/` laufen lassen, bevor das live geht, denn mein F1 ist nur Wort-Bag auf Artikeln.
3. Trafilatura auf 2.3.x anheben (Python >= 3.10): Doku 2026 zeigt F1 0.926 bei 2.9x gegen 1.2.2 standard F1 0.909 bei 7.1x (verschiedene Testsets, Richtung klar: schneller und besser). Nicht selbst nachgemessen, vor dem Umstieg kurz mit bench gegenprüfen.
4. ThreadPool beibehalten, Größe 8, einmal warm anlegen: 143 auf 51 ms für 10 Seiten (trafilatura), 13 auf 4 ms (resiliparse). ProcessPool nur 20 % besser, nicht lohnend. Pro Seite ein hartes Zeitlimit (z. B. p99 trafilatura 38 bis 54 ms, readability bis 143 ms) und mit dem, was fertig ist, antworten.
5. Kein HTML-Abschneiden unter 400 KB und kein Vorab-Strip: 100 KB Cut kostet bis 0.30 F1, Strip spart keine Zeit. Wer den Speicher begrenzen will, kappt bei 1 MB. Neuronale Extraktoren (ReaderLM-v2 10.4 s, MinerU-HTML 1.57 s je Seite auf A100) nur offline für Qualitätsvergleich.

## Lücken
- Eigene Messung nur Artikel (ScrapingHub), Wort-Bag-F1; Foren/Produkt/Doku nicht gemessen. WCXB-Daten (CC-BY-4.0, 300 MB) sind der nächste Schritt.
- Keine Messung von trafilatura 2.3 und von rs-trafilatura.
- SIGIR-2025-Mehrsprachigkeit und Java-Paper nur aus Suchtreffern, nicht im Detail gelesen.
- ScrapingHub-Seitengröße: Median 115 KB; Live-Seiten aus der Websuche sind oft kleiner oder durch Cookie-Wände leer.
