# 01 Such-Backends (Stand 11.10.2026)

## Eigene Messung (lokales SearXNG, 127.0.0.1:8888, JSON)
- 4 Queries: 1,19 s (kalt), dann 0,53 bis 0,55 s. Treffer nur von bing + duckduckgo (je ~10).
- brave: "Suspended: too many requests", google: "Suspended: access denied". Bestätigt Melonslab-Test unten.

## SearXNG: Blockierung (Erfahrung)
- Melonslab, 34 Suchen/30 min, Rechenzentrums-IP: Seznam 34/34, Yahoo 33/34, DDG 32/34 (2 CAPTCHAs, danach wieder ok), Mwmbl 17/24 (Timeouts), Bing 16/24 (leer ohne Fehler), Yep 15/24 (denied), Google 14/24 (CAPTCHA nach ~14 Suchen, 1 h Sperre), Brave 0/24 (429 immer). Startpage + Mojeek: Proof-of-Work-CAPTCHA (Anubis, Difficulty 4, viel CPU), deshalb in SearXNG inaktiv. https://www.melonslab.com/guides/searxng-podman
- SSD Nodes (Sep/Okt 2026): Sperrzeiten Standard: 429 = 180 s, generisches CAPTCHA = 3600 s, reCAPTCHA-503 = 7 Tage, Cloudflare-CAPTCHA = 15 Tage; Timeout/Verbindungsfehler = flach 5 s (ban_time_on_fail). Restart hilft nicht (gleiche IP). https://www.ssdnodes.com/learn/searxng-engines-to-enable-and-disable
- DDG-CAPTCHA-Welle Aug/Sep 2026: Ursache Header-Reihenfolge/TLS-Fingerprint, Fix durch curl_cffi (PR #6620) = SearXNG-Image aktualisieren. Issue #6596 (im Artikel oben); Issue #5541 https://github.com/searxng/searxng/issues/5541 ; #4824 https://github.com/searxng/searxng/issues/4824
- Google liefert teils HTTP 200 mit 0 Treffern, ohne Fehler (Issue #6171, im SSD-Artikel).
- Brave-Engine: Paging max. 10, mehr = Bot-Flag. Wikipedia/Wikidata/GitHub (60 Req/h unauth) antworten auch von Datacenter-IPs.
- Standard request_timeout 3,0 s; Beispiel 3,5/max 10, pro Engine `timeout`. Mehr Timeout = mehr Latenz, weniger Fehler. https://www.ssdnodes.com/learn/searxng-rate-limits-and-429-errors
- Limiter: ~15 Req/20 s, ~150/10 min; JSON-API-Clients nur ~4/h ausser `pass_ip` in limiter.toml (lagindicator, unten). Lokal: limiter aus oder pass_ip.
- Richtwert: unter ~500 Queries/Tag mit gelegentlichen Fehlern komfortabel; darüber bezahlte APIs. https://lagindicator.com/dev-tools/search-gateways
- Reddit-Beleg (r/selfhosted degoog): "7 Engines, Timeouts 1500 ms, perfekte Ergebnisse" (nur Suchergebnis-Snippet gelesen, Reddit blockt Jina): https://www.reddit.com/r/selfhosted/comments/1wxfl0u/degoog_v100_stable_release_search_engine
- r/LocalLLaMA: SearXNG "very fast, robust", Engines rate-limiten/CAPTCHA (Snippet): https://www.reddit.com/r/LocalLLaMA/comments/1ua2w5t/best_harness_for_web_searching
- XDA: SearXNG-Default DDG CAPTCHA + Brave 429 sofort: https://www.xda-developers.com/gave-local-llm-real-time-search-perplexica

## Alternativen
- Brave Search API: Gratis-Tier seit Feb 2026 weg. $5 / 1000 Req, monatlich $5 Guthaben (~1000 Req, nur mit Brave-Attribution), Karte wird belastet, kein Spending-Cap. https://www.implicator.ai/brave-drops-free-search-api-tier-puts-all-developers-on-metered-billing , https://brave.com/blog/most-powerful-search-api-for-ai . Latenz: 669 ms Mittel (AIMultiple Benchmark, schnellste, Qualität top; Tavily 998 ms, Exa 1335 ms) https://aimultiple.com/agentic-search
- Marginalia API (api2.marginalia-search.com): Key `public` ohne Anmeldung, oft Rate-Limit; Parameter count/timeout 50-250 ms. Nischen-/Small-Web-Index, kein Allround. https://about.marginalia-search.com/article/api
- Mojeek API: nur Kontakt-Trial, Preis nach Volumen, Index ~9 Mrd. Seiten; Web-Scraping via SearXNG wegen PoW-CAPTCHA aus. https://serp.fast/tools/mojeek
- Whoogle: eingestellt (Final v1.2.3, 24.07.2026 "no longer returns results"). https://github.com/benbusby/whoogle-search
- DDG html/lite direkt (ddgs-Lib): 202 Ratelimit / 403 bei vielen Requests von einer IP; ddgs "nicht als Produktions-Gateway". https://github.com/open-webui/open-webui/discussions/6624 , https://lagindicator.com/dev-tools/search-gateways
- Bing Search API: von Microsoft eingestellt (Aug 2025). https://cloro.dev/blog/bing-search-api-key
- 4get/LibreX: keine belastbaren 2026-Latenz-/Blockdaten gefunden (nur Verzeichnis-Erwähnungen), nicht empfohlen.
- Hound/master-fetch: laut Audit 05.09.2026 instabil, Original-Repo veraltet. lagindicator-Link oben.

## Lücken
- Keine Latenzzahlen pro SearXNG-Engine aus GitHub-Issues gefunden; Reddit-Threads wegen Jina-Block nur als Snippets. Per-Engine-Latenz selbst über /stats messen.
