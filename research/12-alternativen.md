# 12 Schlanke SERP-Quellen statt SearXNG (Stand 11.10.2026)

Methode: search.py --fan, gh api (Sterne/Push/Lizenz), README per curl/r.jina.ai, eigene Messungen (curl, ddgs). Kein Browser.
Selbst gemessen = [M]. Herstellerangabe = [H]. Nicht geprueft = [?].

## 1. Befund
Der Google-Weg ist 2026 fuer schlanke Scraper tot: Whoogle ist am 24.07.2026 eingestellt (Google hat die letzte nutzbare User-Agent-Kennung ohne JavaScript gesperrt, Repo archiviert). Wer Google will, braucht einen echten Browser plus Proxy. Alles ohne Browser liefert Bing/Brave/DDG/Yahoo/Mojeek, also dieselben Quellen wie SearXNG. Eine schlankere Huelle beschleunigt nur den Overhead, nicht die Upstream-Latenz.
- https://github.com/benbusby/whoogle-search

## 2. Tabelle
| Projekt | Sprache | Pflege 2026 | Sterne | Lizenz | Engines | Latenz | Block-Erfahrung |
|---|---|---|---|---|---|---|---|
| OpenSERP (karust) | Go | ja, Push 28.09.2026 | 1461 | MIT | Google, Bing, Yandex, Baidu, DDG, Ecosia, Megasearch, `fast`-Modus | README-Beispiel: took 720 ms, Ecosia 866 ms [H]; Browser-Modus langsamer [?] | Google per Chrome-Rendering, Proxy-Pools, X-Use-Proxy-Header; Blockrate nicht belegt |
| ddgs (deedy5) | Python | ja, Push 26.08.2026 | 3012 | MIT | bing, brave, duckduckgo, google, grokipedia, mojeek, startpage, yandex, yahoo, wikipedia | [M] heute: auto 3,5 s (7 Treffer), bing 2,2 s, duckduckgo Timeout nach 7,8 s, brave 0,26 s "No results" | DDG 202-Ratelimit bekannt; ab ca. 50 Anfragen/Std Blocks berichtet |
| websurfx | Rust | ja, Push 08.10.2026 | 1214 | AGPL-3.0 | upstream DDG, Brave, Searx u. a. (Liste nicht geprueft) | "extrem schnell" nur Selbstaussage Reddit 2023 [H], keine Zahl | braucht Redis; README bittet selbst um bessere Bot-Evasion |
| SearXNG (Ist) | Python | ja, 38214 Sterne, Push 09.10.2026 | 38214 | AGPL-3.0 | 200+ | [M] lokal 0,63 s fuer eine Anfrage | siehe 09-serp-blocking.md |
| LibreY / LibreX | PHP | maessig, Push 10.06.2026 | 322 | AGPL-3.0 | Google, DDG, Brave, Ecosia, Yandex, Mojeek | [?] | Scraper ohne JS, anfaellig wie Whoogle fuer Google |
| 4get | PHP | Repo auf git.lolcat.ca, aktiv [?] | n/a | AGPLv3 | DDG, Brave, Yandex, Google, Google CSE, Startpage, Baidu, Marginalia u. a. | [?] | rotierende Proxies pro Scraper eingebaut |
| Marginalia | Java | ja, Push 09.10.2026 | 2187 | NOASSERTION (AGPL-nah) | eigener Index, Nischen/Blogs | [M] 0,15 bis 0,19 s mit Key "public" (limitiert) | echte API statt Scraping, nur nichtkommerzielles Web |
| Mwmbl | Python | ja, Push 11.10.2026 | 1871 | AGPL-3.0 | eigener, kleiner Index | [?] | Index zu klein fuer Allgemeinfragen |
| Stract | Rust | archiviert, letzter Push 24.03.2025 | 2372 | AGPL-3.0 | eigener Index | tot | nicht nutzen |
| Whoogle | Python | eingestellt 24.07.2026, archiviert | 11566 | MIT | nur Google | tot | nicht nutzen |
| googlesearch-python | Python | letzter Push 02/2025 | 784 | MIT | Google-HTML | tot (Google-JS-Pflicht) | nicht nutzen |
| search-engines-scraper | Python | [?] | n/a | [?] | Google, Bing, Yahoo | [?] | gleiches Google-Problem |
| Wikipedia-API | HTTP | ja | n/a | CC | Wikipedia | [M] 0,28 s | kein Blocking (09-serp-blocking.md) |

Links:
- OpenSERP https://github.com/karust/openserp , https://openserp.org , Vergleich https://openserp.org/blog/open-source-serpapi-alternatives-2026
- ddgs https://github.com/deedy5/ddgs , Rate-Limit-Diskussion https://github.com/deedy5/ddgs/discussions/348 , DDG-Unzuverlaessigkeit https://scavio.dev/blog/duckduckgo-api-unreliable-alternatives-2026
- websurfx https://github.com/neon-mmd/websurfx , Reddit-Vergleich https://www.reddit.com/r/selfhosted/comments/134wm9k/websurfx_vs_searx_vs_searxng_comparison_of_the/
- LibreY https://github.com/Ahwxorg/LibreY
- 4get https://git.lolcat.ca/lolcat/4get
- Marginalia API https://about.marginalia-search.com/article/api/ , https://github.com/MarginaliaSearch/MarginaliaSearch
- Mwmbl https://github.com/mwmbl/mwmbl
- Stract https://github.com/StractOrg/stract
- googlesearch-python https://github.com/Nv7-GitHub/googlesearch
- search-engines-scraper https://github.com/tasos-py/Search-Engines-Scraper
- SearXNG-Latenzdiskussion (1 s Schnitt, 2 s P99 beim Fragesteller, 2023) https://github.com/searxng/searxng/discussions/2445

## 3. Wie Serper technisch arbeitet
Serper ist laut Dritt-Doku ein Dienst zwischen Client und google.com: eigene Infrastruktur fuehrt die Suche aus, parst HTML und liefert JSON (organic, PAA, Knowledge Graph, related). Der Aufruf laeuft durch eine Proxy- und Parse-Schicht. Interna (Proxy-Pools, Cache) sind nicht veroeffentlicht, die ~1 s kommen vermutlich aus warmen Verbindungen und Caching [Vermutung]. Preise: 50 / 375 / 1250 / 3750 USD Pakete [H].
- https://webscraping.cc/blog/how-to-use-serper-api/
- https://serper.dev/
Warum DIY schwer ist: Google bewertet TLS-Fingerprint (JA3/JA4), Header-Reihenfolge, JS-Profil und Timing, nicht nur die IP. Residential-Bandbreite 4 bis 15 USD/GB, unter ca. 50 Mio. Anfragen/Monat rechne sich DIY nicht (Anbieter-Blog, parteiisch).
- https://cloro.dev/blog/proxies-for-serp-scraping
Rechtslage: Google verklagte SerpApi im Dezember 2025, am 20.07.2026 wurde SerpApis Antrag auf Abweisung stattgegeben (Google darf nachbessern). Risiko fuer gehostete Google-Scraper bleibt.
- https://serpapi.com/blog/google-v-serpapi-the-court-granted-our-motion-to-dismiss/
Open-Source-Nachbau: OpenSERP ist der einzige echte (Go, MIT, Chrome-Rendering plus Proxy). Kein Nachbau erreicht die Serper-Infrastruktur.

## 4. Bezahlte/halbfreie Alternativen mit echter API
- Brave Search API: 5 USD pro 1000 Anfragen, 5 USD Freiguthaben im Monat (= 1000 Anfragen), laut Anbieterseite, Free-Tier sonst gestrichen. https://brave.com/search/api
- DataForSEO queued Google-SERP ab 0,0006 USD, schnelle Modi 0,0012 / 0,002 USD [H, openserp-Blog].

## 5. Empfehlung
1. Wechsel von SearXNG auf eine andere Meta-Huelle lohnt kaum: Upstream-Engines und Blocking bleiben gleich, SearXNG misst lokal 0,63 s [M].
2. Sinnvoll: OpenSERP als zusaetzlicher Google/Bing-Pfad testen (Chrome noetig, Docker `karust/openserp`), Marginalia + Wikipedia als Schnellpfad (0,15 bis 0,28 s [M]), ddgs nur als Fallback mit Rotation.
3. Nicht pruefbar in der Zeit: OpenSERP-Latenz im Google-Browser-Modus, websurfx-Engineliste, 4get-Pflege.
