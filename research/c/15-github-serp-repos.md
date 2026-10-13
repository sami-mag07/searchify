# GitHub-Repos, die Google-SERPs selbst holen (Stand 11.10.2026)

Methode: gh api (Repo-Metadaten, Issues, Commits, README), r.jina.ai fuer git.lolcat.ca, 1x WebSearch. Keine Anfrage an google.com, also KEIN Live-Test. "Funktioniert aktuell" ist deshalb aus Issues/Commits abgeleitet, nicht selbst gemessen.

## Lage Oktober 2026 (belegt)
- Whoogle archiviert, README 24.07.2026: Google hat den letzten funktionierenden No-JS-User-Agent getoetet, "Whoogle no longer returns search results". https://github.com/benbusby/whoogle-search
- SearXNG: Engine "google" ist in settings.yml `disabled: true` (geprueft am master). Nokia-UA-Trick (PR #6546, 20.08.) hielt ~3 Tage, Issue #6570 (23.08.): Google macht jetzt TLS-Inspection, nur curl_cffi mit chrome99_android ging noch. Migration auf curl_cffi am 03.09. (Commit "[mod] network: migrate to curl_cffi"). Default-Ersatz ist "google cse" (Programmable Search, kein echtes SERP).
- /goto-Links: Google liefert als "automatisiert" eingestuften Clients verschluesselte `/goto?url=CAES...` statt Ziel-URLs. openserp #53 (28.09.-08.10.), openserp PR #51 (merged 22.09.), SerpApi public-roadmap #4315 (18.09.: selbst SerpApi hat intermittierend /goto in organic_results.link), 4get-Commits "nice try google" (08.09.) und "google wagies... patched it again" (08.09.).
- `enablejs`: Raw-HTTP-Clients bekommen ein 92-KB-"enable JavaScript"-Geruest ohne Treffer (proxy-benchmark README, 14/14 Zeilen enablejs, keins recaptcha). googlesearch-python Issues #113/#115/#116 "Results is empty / stopped working" seit 10/2025.
- SearchGuard: Googles JS-Challenge-System (seit 01/2025). Google verklagte SerpApi am 19.12.2025 (DMCA 1201); laut Bloomberg Law 21.07.2026 abgewiesen wegen fehlender Aktivlegitimation, Gericht hielt Umgehung von SearchGuard aber fuer ausreichend dargelegt. Rechtslage: ToS-Verstoss sicher, Verfahren nicht rechtskraeftig geklaert. Fuer Eigenbedarf < 500/Tag von Heim-IP geringes praktisches Risiko, trotzdem nicht skalieren/weiterverkaufen.
- Link-Aufloesung: Das /goto-Token laesst sich mit einem einfachen GET auf `https://www.google.com/goto?url=<token>` aufloesen, Ziel steht im Location-Header (links.py in nodemaven, Commit 4get 08.09. "redirect_add_url"). Aus einer separaten Session/IP, nicht aus der aufgewaermten Browser-Session.

## Kandidaten (Stand per gh api am 11.10.2026)

| # | Repo | Sprache | Sterne | Letzter Push | Lizenz | Methode |
|---|---|---|---|---|---|---|
| 1 | karust/openserp | Go | 1461 | 2026-09-28 | MIT | Headless Chromium (go-rod), Browser-Profile, 2captcha optional, Proxy, Cache |
| 2 | nodemaven/google-browser-scraper | Python | 25 | 2026-10-06 | MIT | Patchright headful, sticky Residential-Proxy, Warm-up, /goto-Resolver, HTTP-API + MCP |
| 3 | 4get (git.lolcat.ca/lolcat/4get; Fork cra88y/4get-hijacked, 14 Sterne, 2026-09-19) | PHP | n/a | 2026-10-10 (Repo), google.php zuletzt 03.10. | nicht geprueft | Raw cURL, Firefox-157-UA, `udm=14`, Soft-Captcha-Bypass (sei + 10 s Sleep), eigener /goto-Resolver |
| 4 | searxng/searxng | Python | 38213 | 2026-10-09 | AGPL-3.0 | Raw HTTP, seit 03.09. curl_cffi; google-Engine default AUS |
| 5 | benbusby/whoogle-search | Python | 11566 | 2026-08-14 | MIT | Raw HTTP; ARCHIVIERT, tot |
| 6 | Nv7-GitHub/googlesearch (googlesearch-python) | Python | 784 | 2025-02-03 | MIT | Raw requests; tot seit 10/2025 |
| 7 | Ahwxorg/LibreY | PHP | 322 | 2026-06-10 | AGPL-3.0 | Raw HTTP Metasuche; Issue #233 (31.08.): "No instance works" |
| 8 | gefsikatsinelou/MetaSearchMCP | Python | 59 | 2026-10-10 | MIT | Direct-Google = HTML-Scraping, "best effort, may be blocked"; Fallback auf Serper/SerpBase |
| 9 | brcrusoe72/agent-search | Python | 86 | 2026-09-22 | MIT | Bundelt SearXNG, PR #45 pinnt auf "keep Brave and DuckDuckGo" (kein Google) |
| 10 | HouseofLoops/headwater | Python | 65 | 2026-10-05 | MIT | Maps/News/Trends/Autocomplete, KEINE Web-SERP |
| 11 | NikolaiT/GoogleScraper | Python | 2888 | 2021-07-03 | Apache-2.0 | Selenium/HTTP, seit 2021 tot |
| 12 | D4Vinci/Scrapling | Python | 86785 | 2026-10-11 | BSD-3-Clause | Fetcher-Framework (Stealth/Playwright), keine SERP-Funktion, README bewirbt SerpApi |
| 13 | unclecode/crawl4ai | Python | 85200 | 2026-10-11 | Apache-2.0 | Crawler; nur crawlers/google_search/ als Beispiel, kein gepflegter SERP-Dienst |
| 14 | firecrawl/firecrawl | TS | 190348 | 2026-10-10 | AGPL-3.0 | /search ueber proprietaeres fire-engine bzw. SearXNG im Self-Host, kein eigener Google-Scraper |
| 15 | Engines: daijro/camoufox (12464, MPL-2.0), ultrafunkamsterdam/nodriver (4829, AGPL-3.0), seleniumbase/SeleniumBase (13063, MIT) | Python | siehe | alle < 1 Woche | siehe | Stealth-Browser als Baustein, keine SERP-Parser |

### Serper-aehnliche Felder und Aktualitaet
1. openserp: Einheitliches Schema (results mit type organic, `serp_features`: AI-Summary, Answer Box, People-also-ask, Related Searches laut README; Image Search; Mega-Search). Issue #33 (Juni): Features immer leer, Maintainer: Selector-Drift moeglich. Kein knowledgeGraph belegt. Aktuell: TEILWEISE. Google-Pfad liefert seit ca. 09/2026 `/goto`-URLs; PR #51 rettet nur die Domain via `<cite>`/`span.VuuXrf` (9/9 Domains an einer Live-SERP), nicht die URL. #53 offen. Captcha: #36 (Juli, "Every Google request immediately returns a CAPTCHA, even on residential local network", WSL2/Docker, Fixes in 0.8.10, "not a complete fix"), 28.07.: weiterhin `rate_limited` auch mit Proxy. #34: 2captcha-Pfad repariert (`/sorry/` ohne submitCallback). Kommentar in #53 (28.09.): Bing-Fallback als Workaround. Letzter Release v0.8.12 (22.07.).
2. nodemaven: organic_results, ads, related_questions, related_searches, pagination (SerpApi-Schema); KEIN answerBox/knowledgeGraph. Erst 01.10.2026 gestartet, Early-Status, "Tested on Windows, Linux/Docker experimental". Aktuell: einziger Kandidat, der /goto ausdruecklich loest. Aber vom Proxy-Verkaeufer NodeMaven, Eigeninteresse. `--no-proxy` ausdruecklich erlaubt: "starts answering with captchas sooner", als captcha klassifiziert.
3. 4get: Google-Scraper liefert web, answer, related, spelling, images/videos/news; nutzt `udm=14` (Web-Filter, entfernt AI Overview, PAA, KG weitgehend). Commits 08.09. und 03.10. zeigen laufende Patches gegen Google ("why does google do this"). cra88y-README: "doesn't appear to return results without the use of a residential proxy" (ohne Datum, aus dem Repo-Stand 09/2026), also von Heim-IP am ehesten tauglich.
4. SearXNG: Google-Treffer nur als Metasuche-Ausschnitt (title/url/content), keine Answerbox/PAA/KG von Google.
5-7, 11: nicht funktionsfaehig (Belege oben).

### Benchmarkdaten (nodemaven/proxy-benchmark, Verkaeufer-Quelle, aber Rohdaten committed, 42.743 Zeilen)
- Warm-up (6 Seiten vor der Query): Google-Auslieferung 19,8 % (46/232) kalt -> 86,8 % (409/471), z = 17,5.
- Google: "the exit address dominated everything else we varied". Bei ausgelieferter Seite 83/83 pass.
- Gleicher Code, gleiches Gateway: Windows-Arbeitsplatz 39 % (24/61) vs Linux-VPS 0 % (0/84), p = 3,7e-11. Rechenzentrums-Hosts und Headless sind das Problem.
- TLS-Handshake erklaert Google nicht (Chromium, Patchright identischer ClientHello, Raten 44 Punkte auseinander). Engine-Ranking auf Google nicht aussagekraeftig (alle < 5 % auf dem VPS).
- Chrome sendet pro frischem Profil ~43 MB an Google-Dienste; nur relevant fuer bezahlte Proxies.

## Rangliste fuer 1 Nutzer, < 500 Anfragen/Tag, Heim-IP, Oktober 2026
1. Echter Browser, headful, persistentes Profil, eigene Heim-IP, Warm-up, Query in die Suchbox tippen, Parser ueber DOM, `/goto` per separatem Request aufloesen. Bausteine: Patchright oder echtes Chrome (nicht Headless), Parser-Ideen aus openserp (`cite`, `span.VuuXrf`) und nodemaven (classify.py: Captcha nie als "keine Treffer" werten). Einziger Ansatz mit belegter Trefferquote (Warm-up, Host/IP-Effekt) und einziger, der PAA/answerBox/KG/relatedSearches erreichen kann (volle SERP, nicht udm=14). Bei 500/Tag (~1 alle 3 Min im Wachzeitraum) mit Jitter und Pausen plausibel. Risiko: Captcha-Serien nach Bursts, Selektoren-Drift.
2. Raw cURL nach 4get-Muster als schneller Erstversuch: Firefox-UA, `udm=14`, `hl`/`gl`, bei Zwischenseite "Verifying your request" die `eid`-Variable lesen und `sei` mitschicken (nach 10 s), /goto per GET aufloesen, bei `enablejs`/`Google Search`-Titel/captcha-form Fallback auf Ansatz 1. Billig (kein Browser), aber Katz-und-Maus (Patches 08.09. und 03.10.), nur organic/answer/related. Nicht curl_cffi allein verlassen: SearXNG zeigt, dass TLS-Imitation kurz hilft, nicht dauerhaft.
3. Hosted-Fallback fuer die Luecken: Serper/SerpBase (MetaSearchMCP und googlesearch #119 nutzen dasselbe Muster), plus Bing/Brave/DDG-Pfad (openserp-Workaround #53). Nicht Google-nativ, aber stabil.
Nicht empfohlen: SearXNG-google (default AUS, Metasuche), Whoogle, googlesearch-python, LibreY, GoogleScraper, openserp als fertiges Docker (WSL/Docker-Captcha #36, /goto #53), Camoufox/nodriver/SeleniumBase als Allheilmittel (Benchmark: Engine-Wahl bei Google zweitrangig gegenueber IP/Host/Warm-up).

## Luecken und Unsicherheiten
- Nichts selbst gegen Google getestet (Auftrag). Alle "funktioniert" sind aus Issues/Commits.
- 4get-Lizenz und Sterne nicht geprueft (Gitea). Camoufox/nodriver: keine Google-spezifischen Issues der letzten 3 Monate gefunden (Camoufox #686 "Detection status by site" offen, Inhalt nicht gelesen).
- "Sorry"/429-Raten pro Heim-IP bei 500/Tag sind nirgends gemessen; der Benchmark nutzt Proxy-Pools, nicht Heim-IPs.
- Bloomberg-Meldung zur SerpApi-Abweisung nur ueber WebSearch-Zusammenfassung, nicht im Original gelesen.

## Quellen
- https://github.com/karust/openserp/issues/53 , /36 , /33 , /34 , /pull/51
- https://github.com/serpapi/public-roadmap/issues/4315
- https://github.com/benbusby/whoogle-search (README-Hinweis 24.07.2026)
- https://github.com/searxng/searxng/issues/6570 , /6453 , /6359 , /6567 ; settings.yml (google disabled)
- https://github.com/nodemaven/google-browser-scraper , https://github.com/nodemaven/proxy-benchmark
- https://git.lolcat.ca/lolcat/4get/commit/017c4ba1b0 , /734926240d , /90e0a32366 ; scraper/google.php
- https://github.com/cra88y/4get-hijacked
- https://github.com/Nv7-GitHub/googlesearch/issues (#113, #115, #116, #119)
- https://github.com/Ahwxorg/LibreY/issues/233
- https://github.com/gefsikatsinelou/MetaSearchMCP , https://github.com/brcrusoe72/agent-search/pull/45
- https://ppc.land/google-sues-serpapi-over-search-scraping-in-copyright-lawsuit/ , https://news.bloomberglaw.com/ip-law/google-lawsuit-against-data-scraper-tossed-over-authority-issue
