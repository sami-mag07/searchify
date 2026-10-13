# 05 Community: Tavily/Exa/Perplexity-Alternativen (Stand 11.10.2026)

Methode: HN Algolia, Arctic Shift (Reddit-Posts + Kommentare), search.py --fan, r.jina.ai. Keine Browser-Tabs. Rohdaten: research/c/ (Seiten) und c/seen.txt.
Belegstufen: [belegt] = Primärquelle selbst gelesen; [Aussage] = Einzelmeinung/Selbstauskunft eines Nutzers, nicht nachgeprüft; [Vendor] = Zahl vom Anbieter.
Lücke: zu Farfalle, MindSearch, OpenPerplex, Morphic, Scira, Jina-Reader-Selfhost fand ich KEINE belastbaren Community-Latenzzahlen. Perplexica/LDR nur Meinungen, keine Zahlen.

## 1. Latenzzahlen (Tavily-Referenz)
- Ritza-Benchmark, 50 Queries, Dez 2025: Exa 1,180 s Mittel, Tavily 1,885 s, beide 100 % Erfolg, SerpAPI ~Platz 4 mit starker Streuung (ms bis >10 s). [belegt, Suchtreffer-Text] https://dev.to/ritza/best-serp-api-comparison-2025-serpapi-vs-exa-vs-tavily-vs-scrapingdog-vs-scrapingbee-2jci ; zitiert in https://coldiq.com/blog/tavily-vs-exa
- Exa-Eigenbenchmark 29.07.2026, 333 Calls: Median vergleichbar, Exa 27 % schneller bei p90, 32 % bei p99. [Vendor] https://exa.ai/versus/tavily
- r/Rag-Post eines Konkurrenten (SERPdive): Tavily Basic ~1,4 s, eigener Dienst ~1,7 s, 1000 blinde Duelle, ~60 % Siege, 20 % weniger Tokens (1001 vs 1255). Selbstbewertung, Repo mit Rohdaten. [Vendor] https://www.reddit.com/r/Rag/comments/1v1kpfv/i_benchmarked_a_tavily_alternative_on_1000_blind/
- Keenable (eigener 100B-Index): p95 <250 ms. [Vendor] https://news.ycombinator.com/item?id=49435555
- Jina Reader: durchschnittlich 7,9 s pro Request laut Firecrawl-Vergleich. [Vendor, Konkurrent] https://www.firecrawl.dev/alternatives/firecrawl-vs-jina-ai
- Reranker: 80-200 ms für 50 Kandidaten (BGE-v2-m3 ~200-400 ms CPU, 50-100 ms GPU); Kontext von 30 auf 7 Chunks senkte Antwortzeit von ~7 s auf ~1,5 s. [Aussage] https://www.reddit.com/r/LangChain/comments/1rdg2f9/is_adding_a_reranker_to_my_rag_stack_actually
- Fazit für Ziel 1,33 s p50: Tavily selbst liegt je nach Test bei 1,4 bis 1,9 s. Ein reiner Snippet-Pfad (SERP + Rerank) kann klar darunter; sobald Seiten geladen werden, hängt p50 an der langsamsten Seite.

## 2. Was Leute tatsächlich bauen
- SearXNG + Crawl4AI schlägt laut Entwickler "manchmal sogar Tavily". [Aussage] https://github.com/searxng/searxng/discussions/4637
- 3-Stufen-Kaskade: SearXNG suchen, Crawl4AI/Trafilatura holen, bei Captcha Camoufox/Chrome-CDP. https://www.reddit.com/r/LocalLLaMA/comments/1ua6xfw/how_do_you_guys_setup_search_with_your_ai_models/ ; https://www.reddit.com/r/LocalLLaMA/comments/1uam3iv/giving_a_local_agent_web_access_without_paid [belegt als Threads, Inhalte Aussagen]
- SearXNG-Konfig mit Brave-API als Engine, Budgetdeckel 5 USD/Monat, Firecrawl dahinter, Camofox als Fallback. [Aussage] gleicher Thread 1uam3iv.
- TrailSearch / tavily-open: Redis-Cache, lokaler SQLite-FTS-Index, SearXNG, optional Brave, gestufte Extraktion (Reader/HTTP/Browser), asynchroner Backfill, damit Vordergrund schnell bleibt; liefert `timings_ms` je Stufe. [belegt, README] https://github.com/jianjungki/tavily-open
- Siftdog: Tavily-API-kompatibel (tavily-python mit api_base_url), SearXNG + trafilatura + BM25, `advanced` lädt Seiten und gibt relevanteste Chunks; selbst eingeräumt: BM25 statt Neural-Reranker, kein JS. [belegt, README] https://github.com/khsarvar/siftdog
- OpenSERP: Go-Binary, Multi-Engine-SERP (Google/Bing/DDG/Baidu/Yandex/Ecosia), Markdown-Extraktion `extract_mode` auto|fast|rendered, Proxy-Pools per Header. https://github.com/karust/openserp
- Metasuche über mehrere Anbieter parallel (Exa + Tavily + SearXNG), dedup/fusion per Heuristik oder Agent; "jeder Anbieter hat viele einzigartige Treffer". [Aussagen, 5 unabhängige HN-Kommentare] https://news.ycombinator.com/item?id=49745701 , https://news.ycombinator.com/item?id=48700475 , https://news.ycombinator.com/item?id=48562888 , https://news.ycombinator.com/item?id=49251992
- LibreChat-Setup mit SearXNG + Crawl4AI + Jina-Rerank, "viel Eigencode zum Zusammenfügen", Suche "besser als OpenAI/Claude Code". [Aussage] https://news.ycombinator.com/item?id=49244098
- Query-Varianten: 3 Varianten in EINEM LLM-Call, parallele Suche, Reciprocal Rank Fusion; hybrid dense+BM25, weil Dense bei Fachbegriffen schwächelt. [Aussage] https://news.ycombinator.com/item?id=45646532
- Firecrawl selbst gehostet: Fehlerrate <2 % laut Nutzer [Aussage] https://news.ycombinator.com/item?id=49968986 ; andere "Firecrawl unzuverlässig, zu Crawl4AI gewechselt" https://www.reddit.com/r/LocalLLaMA/comments/1ozk41e/how_are_you_handling_web_crawling_firecrawl_is/ ; Self-host Firecrawl hat weniger Anti-Bot-Funktionen als Cloud (Grund für Scrapling) [Aussage, 1uam3iv].

## 3. Fallstricke
- Blocking/Rate-Limits sind Problem Nr. 1 bei SearXNG-für-Agenten. Reddit-Frage "SearXNG keeps getting blocked" (20.05.2026), Antworten: lokaler Cache + Fallback (95 % Zuverlässigkeit), "Fetch-Layer entscheidet, nicht die Engine", dünner Wrapper mit Fallback-Pfaden und aggressivem Caching. [Aussagen] https://www.reddit.com/r/AI_Agents/comments/1tijxk8/selfhosted_search_for_llm_agents_searxng_keeps/
- Stilles Versagen: Google liefert nichts, SearXNG gibt nur Brave/Mojeek-Reste ohne Fehler zurück; Lösung: Engine-Fehlerstatistik beobachten und nur Engines pinnen, die Datacenter-IPs nicht captchen. [Aussage] 1uam3iv
- Google-Engine: "Suspended: too many requests" / Captcha durch fehlenden `arc_id`-Parameter, März 2025 gefixt (Update einspielen). [belegt] https://github.com/searxng/searxng/issues/4435
- DDG blockt Rechenzentrums-IPs häufig, Brave blockt schnell, DDG "verzeihender" [Aussage, 1uam3iv]; DDG blockt auch Privat-IP-Instanz https://news.ycombinator.com/item?id=49669124 ; Gegenbeispiel: nie geblockt https://news.ycombinator.com/item?id=49160969
- IP-Rotation (WireGuard-Proxy, alle 5 Min Wechsel) und konsistente Proxy-Sessions für harte Seiten [Aussagen, 1uam3iv]. Öffentliche SearXNG-Instanzen taugen nicht für Produktion (werden geflaggt).
- Timeouts: SearXNG-Default-Timeouts mussten hochgesetzt werden; Alternative degoog streamt Ergebnisse und hatte keine Timeout-Fehler. Entwickler: "Latenz liegt bei den Upstream-Engines, nicht beim Meta-Searcher". [Aussagen] https://www.reddit.com/r/selfhosted/comments/1tzrdd7/searxng_vs_degoog
- Google erkennt Nicht-Menschen und liefert billigere/gecachte Ranking-Pfade, frische Ergebnisse fehlen. [Aussage, 1uam3iv]
- Statische Extraktion (trafilatura/BM25 ohne JS) scheitert an JS-Seiten; Rendering nur als spätes Fallback, "leichter Fetcher erledigt die meisten Seiten" [Aussage, 1uam3iv; Siftdog-README].
- Firecrawl ohne Self-Host-Tuning: "10 Seiten in 1,5 Minuten" [Aussage, 1uam3iv] -> Seitenladen NIE sequenziell/ohne Zeitlimit.
- Reranking repariert keinen schlechten Recall: erst Hybrid (BM25+Vektor), dann Reranker. [Aussage, Thread 1rdg2f9]

## 4. Was war besser als Tavily
- Rohe Treffervielfalt: Fusion mehrerer Quellen > jede einzelne (HN-Kommentare oben).
- Weniger Token: kompakte, entrauschte Chunks (SERPdive 1001 vs 1255 Tokens, 60 % Siege; Vendor).
- Technisches/Nische: Tavily-Ergebnisse "zu dünn oder zu verrauscht" (r/Rag, Vendor-Post).
- Kontrolle/Transparenz (eigene URL-Auswahl statt Vendor-Rerank): https://www.reddit.com/r/LocalLLaMA/comments/1qlwxcu/built_a_tavily_alternative_flow_that_doesnt_hide/ ; Exa meist schneller als Tavily.
- Schwächer: Frische, Anti-Bot-Seiten, Zuverlässigkeit ohne Cache/Fallback, Perplexica "Qualität nicht wie Perplexity" [Aussage] https://news.ycombinator.com/item?id=46563329

## 5. Die 5 umsetzbarsten Tricks (Kurzliste)
1. Mehrstufiger Pfad mit Zeitbudget, Snippet-first (siehe Antwort).
2. Redis/SQLite-Cache plus Stale-Fallback und asynchroner Backfill (tavily-open).
3. SERP parallel aus mehreren Quellen + RRF-Fusion, Engine-Health-Monitor.
4. Fetch-Kaskade: HTTP+trafilatura -> Crawl4AI -> Camoufox/CDP, nur bei Bedarf.
5. Query-Varianten in einem LLM-Call (optional) + Hybrid BM25 + Cross-Encoder auf Top-N, kleine Chunks.
