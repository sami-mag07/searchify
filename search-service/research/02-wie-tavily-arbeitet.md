# Wie Tavily, Exa, Perplexity, Brave, Linkup, You.com, Jina Latenz und Content-Snippets erreichen

Stand 11.10.2026. Nur öffentliche Quellen. [B] = belegt in der Quelle, [S] = meine Schlussfolgerung/Schätzung.
Rohfunde: research/seen.txt

## Kernbefund
Alle schnellen Anbieter laden Seiten NICHT im Anfragepfad. Sie haben Seiteninhalt schon vorab (eigener Index oder Cache) und wählen zur Anfragezeit nur noch Chunks aus. Live-Fetch gibt es nur als Opt-in (You.com full_page, Exa Livecrawl) und ist dann langsam. Euer 300-ms-Fetch-Budget ist der Grund für ~200 Zeichen Content.

## Pro Anbieter

### Tavily (Ziel)
- Chunks ~500 Zeichen (basic/fast), 800 (advanced); content = `<chunk1> [...] <chunk2> [...] <chunk3>`, Standard bis zu 3 Chunks pro Quelle (chunks_per_source), ultra-fast = 1 NLP-Summary pro URL. [B] https://docs.tavily.com/documentation/api-reference/endpoint/search
  Das erklärt ~1150 Zeichen: 2-3 Chunks à ~500. [S]
- Cache-Hierarchie für URL-Inhalte: Redis für ~1 % der URLs (~30 % der Requests), S3 Express One Zone als Zweitcache (single-digit ms, same AZ), S3 Standard als Kaltcache/Backup. Kosten -95 % gegenüber Dokument-DB. Ranking-Fenster "contextually ranked results within 10 ms". [B] https://aws.amazon.com/blogs/storage/how-tavily-reduced-ai-search-caching-costs-by-95-with-amazon-s3-express-one-zone/
  Heißt: Tavily liefert Content aus einem URL-Inhalts-Cache, nicht aus Live-Fetch. [S]
- Website: "real-time search, intelligent caching, and indexing". [B] https://tavily.com
- CEO Rotem Weiss: reine Embedding-Ähnlichkeit reicht nicht (Tabellen/Zahlen), Ranking = Semantik + Domain/Page-Rank-Signale; Snippets über mehrere Quellen dedupliziert zu einem kurzen Snippet; empfiehlt mehrere Chunking-Methoden + Reranker testen. [B] Transkript https://www.youtube.com/watch?v=Gjzf7HpsOW0 (People Who Ship Ep. 5)
- Fast/Ultra-fast-Blog nennt keine Technik, nur Benchmarks und "Information Density per millisecond". [B] https://www.tavily.com/blog/how-we-built-the-fastest-web-search-in-the-world
- Nebius-Podcast, Beschreibung: "prediction engine pre-processes likely ..." (Details nicht geprüft, Transkript nicht abrufbar, YouTube 429). https://www.listennotes.com/podcasts/the-nebius-for/how-tavily-got-to-1m-users-Q6LUesotnJW/

### Exa (eigener Index, Embeddings)
- Eigener Crawl, eigener Parser, Speicher in S3, eigene Vektor-DB: Matryoshka 4096->256 Dim (20x), Binärquantisierung (16x), Float-Query gegen Binär-Docs mit Lookup-Tabelle, 100.000 k-means-Cluster (~1000x Durchsatz), Re-Rank mit unkomprimierten Vektoren, Inverted Index für Filter. Milliarden Vektoren < 100 ms. [B] https://exa.ai/blog/building-web-scale-vector-db
- Exa Fast p50 < 425 ms (50 ms Netz), Instant < 200 ms. Aussage: Wer Google-SERP wrappt, hat >= 700 ms p50. [B] https://exa.ai/blog/fastest-search-api , https://exa.ai/blog/exa-instant
- Highlights: Chunking + Embedding des Volltexts zur Anfragezeit, NICHT gecacht, < 100 ms. 500 Zeichen Highlights = Genauigkeit von 8000 Zeichen Seitenanfang; 4k Highlights > 32k Volltext (SimpleQA). [B] https://exa.ai/blog/highlights-for-agents
- Highlights-Server: Python -> Rust, 4x Durchsatz, 1 Node 8xA100, Pipeline CPU-Preprocessing + GPU-Inferenz getrennt. [B] https://exa.ai/blog/scaling-our-highlights-server
- Hybrid BM25 + Embeddings. [B] https://exa.ai/blog/bm25-optimization
- Nachbau/Analyse Dritter: QueryBurst: Exa-Highlights liefern teils Abschnitts-Blobs statt Sätze; eigener Nachbau mit Satz-Split + Embedding + Ranking: 75 % SimpleQA mit ~397 Tokens vs. 65 % bei ~878 Tokens Volltext. Frische Seiten außerhalb des Index brauchen auch bei Exa ~3 s. [B] https://queryburst.com/blog/exa-highlights
- Napkin-Math (Schätzung eines Dritten, keine Exa-Aussage): Rerank evtl. mit Qwen3-Reranker-0.6B; ~25 % Index im RAM. [S] https://www.kshivendu.dev/blog/exa-napkin-math

### Perplexity (eigener Index, Sub-Dokument)
- 200+ Mrd. URLs getrackt, ML-Modell entscheidet WAS und WANN neu indexiert wird; heiße/kalte Speicher; Hybrid lexikalisch+semantisch, Prefilter, mehrstufiges Ranking, am Ende Cross-Encoder; Dokumente werden in selbstständige Spans zerlegt, Spans werden einzeln gerankt. Parsing-Regeln pro Domain werden per LLM-Schleife selbst verbessert. 200 Mio. Queries/Tag. [B] https://www.perplexity.ai/hub/blog/architecting-and-evaluating-an-ai-first-search-api
- Vespa als Serving-Schicht (Retrieval+Ranking zusammen). [B] https://vespa.ai/case-studies/why-perplexity-chose-vespa-for-ai-search/ , https://blog.vespa.ai/perplexity-show-what-great-rag-takes
- Open Source: Eval-Framework https://github.com/perplexityai/search_evals

### Brave (eigener Index + Echtzeit-Chunking)
- LLM-Context-API: 1) Websuche im eigenen Index, 2) Seiten "in real time" in "smart chunks" umwandeln (Tabellen/Code/Strukturdaten), 3) eigenes trainiertes Ranking der Chunks, 4) Zusammenstellung nach Token-/URL-Budget. Overhead < 130 ms p90 auf normale Suche, gesamt < 600 ms p90. Parameter: Token-Budget, Threshold-Modi strict/balanced/lenient. [B] https://brave.com/blog/most-powerful-search-api-for-ai , https://api-dashboard.search.brave.com/documentation/services/llm-context , https://www.developer-tech.com/news/brave-search-api-revamp-makes-web-search-useful-for-ai-apps
- Wichtig: Brave hat die Seitentexte aus dem eigenen Index/Cache, daher 130 ms. [S]

### Linkup
- Indexiert "atoms of information" (einzelne Fakten mit Quelle, Zeitstempel, Glaubwürdigkeit) statt Seiten, dazu Embeddings; /fast sub-second. [B] https://www.linkup.so/blog/linkup-raises-10m-seed-to-build-web-search-for-ai
- Podcasts mit CEO Philippe Mizrahi: https://hyperengage.io/podcast/ep-146-redefining-web-access-for-ai-agents-ft-philippe-mizrahi-ceo-linkup , https://www.phacetlabs.com/podcast/linkup-eu10m-to-build-the-search-engine-ai-agents-actually-need (nicht im Detail gelesen)

### You.com
- Index-Modus (Datenbank-Lookup, niedrige konstante Latenz, Frische je Domain Minuten bis Wochen) vs. Live-Crawl auf Anfrage (`extraction_mode: full_page`, Latenz = langsamste Seite). Snippets als Array "snippets" pro Ergebnis. [B] https://you.com/resources/real-time-web-search-api , https://you.com/docs/guides/search
- Marketing: "300 ms p99" (Drittseite, ungeprüft) https://www.orthogonal.com/blog/you-com-web-search-apis-for-ai

### Jina
- Reader (r.jina.ai) ist Live-Fetch/Konverter, kein Index; Header für Selektoren/Wait/Token-Budget. [B] https://github.com/jina-ai/reader
- DeepSearch Snippet-Auswahl: Late Chunking (jina-embeddings-v3) über die ganze Seite, ~500-Token-Chunks, Kosinus in-memory (kein Vektor-DB nötig), danach zusammenhängende Chunk-Fenster statt verstreuter Sätze; Reranker zum Vorsortieren der URLs VOR dem Crawlen. [B] https://jina.ai/news/snippet-selection-and-url-ranking-in-deepsearch-deepresearch
- Late Chunking: https://jina.ai/news/late-chunking-in-long-context-embedding-models

### Eigenbau-Referenz
- Wilson Lin: Suchmaschine in 2 Monaten, 280 Mio. Seiten, 3 Mrd. SBERT-Embeddings auf 200 GPUs, sharded HNSW + RocksDB, ~500 ms End-to-End; Sätze werden einzeln eingebettet, "non-leaf"-Sätze markiert. [B] https://blog.wilsonl.in/search-engine

## Was wir mit kleinem Budget nachbauen können
1. URL-Inhalts-Cache (größter Hebel, Tavily-Muster): jede einmal geladene Seite als bereinigter Text + vorberechnete Chunk-Embeddings speichern (SQLite/Disk, LRU, Redis nur für heiße ~1 %). Tiering wie Tavily: RAM -> lokale SSD -> Kalt. TTL nach Domain-Typ (News kurz, Docs lang).
2. Vorab-Crawl der Kopf der Verteilung: Top-N-Domains/URLs aus eigenen Query-Logs und Backend-Treffern (SearXNG-Top-10 beliebter Queries) im Hintergrund holen, bevor jemand fragt. Perplexity macht das ML-gesteuert; wir starten mit Häufigkeit x Alter.
3. Fetch-Budget entkoppeln: Antwort mit Cache-Treffern + Backend-Snippet nach 300 ms ausliefern, nicht gefetchte URLs weiter im Hintergrund laden (füllt Cache für die nächste Anfrage). Cache-Miss kostet nur Qualität, nicht Latenz.
4. Chunk-Auswahl wie Tavily/Jina: Seite in ~500-Zeichen-Chunks (Satzgrenzen), BM25 + Embedding (hybrid), dann Cross-Encoder nur auf Top-~30 Chunks statt auf Seiten; 2-3 beste, zusammenhängende Chunks mit " [...] " verbinden = ~1150 Zeichen. Embeddings schon beim Cachen berechnen (Exa: Highlights ungecacht, wir sparen bei Wiederholung).
5. Rerank vor dem Fetch (Jina): Backend-Titel+Snippet mit dem Cross-Encoder vorsortieren, nur die Top-5 bis 8 laden.
6. Nicht nachbauen: eigener Web-Index im Milliardenmaßstab (Exa/Perplexity/Brave: Cluster, GPUs, Mio. USD). Als Schlüssel-Idee übernehmbar: Matryoshka+Binärquantisierung (Exa) falls ihr einen eigenen Nischenindex (z. B. Docs-Domains) baut; Wilson Lin zeigt 280 Mio. Seiten mit ~500 ms als Obergrenze eines Einzelprojekts.
7. Latenz-Eckwerte zum Vergleich: Exa Fast 425 ms, Instant < 200 ms, Brave < 600 ms p90, Tavily 1,33 s p50 (eure Messung). Wer Google/Serper wrappt, hat laut Exa >= 700 ms Minimum [B, Wettbewerber-Aussage]. Serper/SearXNG-Latenz selbst messen.

## Offen / ungeprüft
- Tavilys genaue Pipeline (eigener Index ja/nein) ist öffentlich nicht beschrieben; Cache-Belege sind eindeutig, Index-Anteil unklar.
- Nebius-Podcast-Transkript (Tavily "prediction engine") nicht abrufbar.
- Patente nicht gesucht (Zeitlimit).
