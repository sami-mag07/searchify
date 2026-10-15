# 08 Fetch: schnellster Weg, 10-20 beliebige Seiten parallel zu laden (Stand 11.10.2026)

## Eigener Live-Test (Mac, Heimnetz, 15 Seiten: wikipedia, bbc, nytimes, medium, stackoverflow, dev.to, HN, reuters, guardian, python docs, github, cnn, spiegel, zeit, imdb; Chrome-Header, 5 s Timeout, Python 3.9)
Skript: scratchpad/bench.py. Gesamtzeit bis alle 15 fertig / Erfolg (HTTP 200):
| Client | kalt | warm | Erfolg 200 |
|---|---|---|---|
| httpx sync, 48 Threads (euer Setup) | 683 ms | 664 ms | 9/15 |
| aiohttp (limit=0, ttl_dns_cache=300) | 500 ms | 530 ms | 10/15 |
| curl_cffi AsyncSession impersonate=chrome | 544 ms | 522 ms | 11/15 |
| curl_cffi AsyncSession ohne impersonate | 557 ms | 493 ms | 7/15 |
| niquests | Lauf hing (>100 s), nicht gemessen | | |
Codes: 200, 202, 401, 403. Fazit: Laufzeit ist von der langsamsten Seite dominiert (p50 pro Seite 107-164 ms, Gesamt ~500 ms). Client-Wechsel bringt ca. 20-25 % Gesamtzeit, impersonate bringt +2 bis +4 Seiten Erfolg (7 -> 11 von 15). Einzelmessung, kleine Stichprobe, Heimnetz statt Server.

## Client-Benchmarks (Dritte)
- curl_cffi Repo-Benchmark, lokaler Server, 1k-Body, 1000 Requests (Sekunden, weniger ist besser): pycurl 0.129, curl_cffi_raw 0.141, aiohttp 0.292, curl_cffi_async 0.310, curl_cffi_sync 0.353, httpx_sync 0.714, requests 1.043, httpx_async 1.760. Bei 200k: aiohttp 0.44, curl_cffi_async 0.84, httpx_async 3.44. https://github.com/lexiforest/curl_cffi/blob/main/benchmark/multiple_workers.csv
- httpx async ist ca. 5-14x langsamer als aiohttp bei hoher Nebenläufigkeit (Overhead in httpcore): https://github.com/pydantic/httpx2/issues/827 , https://github.com/encode/httpx/discussions/3457 (2.01 s vs 0.36 s), https://dev.to/0012303/requests-vs-httpx-vs-aiohttp-i-benchmarked-all-3-results-surprised-me-4gco (0.74 s vs 0.12 s)
- Bei nur 10-20 Requests ist der Unterschied klein (Oxylabs: 1.22 s httpx vs 1.19 s aiohttp bei wenigen, 10.22 vs 3.79 s bei 1000): https://oxylabs.io/blog/httpx-vs-requests-vs-aiohttp
- niquests (Herstellerangabe, 1000 Requests an httpbingo.org, ein Host, HTTP/2): niquests 0.551 s, aiohttp 1.351 s, httpx 2.087 s. Multiplexing hilft nur bei vielen Requests an DENSELBEN Host; bei 10-20 verschiedenen Hosts entfällt der Vorteil. https://deepwiki.com/jawah/niquests/11.2-performance-comparison , https://dev.to/romdevin/pythons-http-client-gap-addressing-requests-library-stagnation-with-modern-alternatives-1a81
- httpx Semaphore(20): 0.558 s vs ohne Limit 0.367 s bei 200 Requests, Pool-Limit sollte >= Anzahl URLs sein: https://scrapingant.com/blog/requests-vs-httpx
- Weitere: https://github.com/perodriguezl/python-http-libraries-benchmark , https://decodo.com/blog/httpx-vs-requests-vs-aiohttp

## Bot-Blocking und Erfolgsquote
- Cloudflare bewertet JA3/JA4-TLS- und HTTP/2-Fingerprint; requests/httpx/aiohttp haben bekannte Fingerprints, deshalb 403 bei gleichem Header. https://developers.cloudflare.com/bots/additional-configurations/ja3-ja4-fingerprint , https://scrapfly.io/blog/posts/ja3-ja4-tls-fingerprinting-guide-to-detection-and-evasion
- Ian Paterson, Mai 2026, 31 Cloudflare-/Anti-Bot-Ziele, 3 Läufe, Heim-IP: curl_cffi 0.15 impersonate=chrome = 26 OK / 3 gated / 2 blocked (84 % OK); gleich gut wie CloakBrowser (130 MB Chromium-Fork); nodriver 28 OK/0 blocked. Ohne JS bleibt curl_cffi an Turnstile-Seiten (canadianinsider, glassdoor) hängen. https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi
- Warnung: Cloudflare erkennt curl_cffi-Spoofing teils später (Reddit-Thread Juni 2026, Seite von mir nicht lesbar, nur Snippet): https://www.reddit.com/r/webscraping/comments/1tqtqct/curl_cffis_tlsspoofing_detected_by_cloudflare
- Beim Async-Fall impersonate nicht vergessen (Issue 476 war genau das): https://github.com/lexiforest/curl_cffi/issues/476
- Legal reduzieren: echte Browser-Header komplett und konsistent (UA, Accept, Accept-Language, Sec-Fetch-*, Sec-CH-UA passend zur Chrome-Version), impersonate-Profil passend zum UA, robots.txt/ToS respektieren, pro Host rate-limiten, kein Umgehen von CAPTCHA/Turnstile. Danach Fallback: Jina Reader / Snippet des Suchindexes statt Retry-Sturm.

## DNS, Happy Eyeballs, TLS
- aiohttp cacht DNS standardmäßig (ttl_dns_cache=10 s Default, per TCPConnector setzbar, 300 s sinnvoll); aiodns macht Auflösung asynchron statt getaddrinfo im Threadpool. Lookup kostet je nach Resolver Mikrosekunden (lokaler Cache) bis zig ms. https://www.async-concurrency.com/network-io-protocol-handling/connection-pooling-and-keepalive/caching-dns-lookups-in-async-http-clients/ , https://docs.aiohttp.org/en/v3.8.1/client_advanced.html
- Happy Eyeballs: Connection-Attempt-Delay 150-250 ms (RFC 8305); kaputtes IPv6 kostet sonst bis zu 250 ms. Kürzer setzen: aiohttp happy_eyeballs_delay (Default 0.25), oder nur IPv4 (family=AF_INET), wenn Server kein IPv6 braucht. https://blog.apnic.net/2025/10/07/analysing-the-state-of-happy-eyeballs-implementations , https://github.com/aio-libs/aiohappyeyeballs/
- TLS-Resumption/0-RTT spart 1 RTT bei Wiederverbindung zum selben Host; bei beliebigen Seiten selten relevant, Keepalive-Pool (hast du: 120 s) bringt mehr. https://blog.cloudflare.com/introducing-0-rtt
- Zahl: Cloudflare nennt ca. 60 % der Verbindungen als Erstbesucher (kein Resumption-Vorteil): selber Link.

## Kompression
- Brotli ca. 25 % der Textantworten, gzip/br je gut 40 % Adoption, zstd ca. 2 % (Web-Almanac-artige Messungen). Accept-Encoding "gzip, deflate, br" (+ zstd) senden, Pakete brotli und zstandard installiert halten, sonst ist br/zstd Antwort nicht dekodierbar oder wird gar nicht angeboten. https://www.debugbear.com/blog/http-compression-gzip-brotli , https://www.corewebvitals.io/state-of-cwv/explorer/metrics.network.compression , https://webspeed.pro/en/research/is-your-html-compressed/
- Brotli macht Seiten ca. 17-19 % kleiner als gzip-6: https://paulcalvano.com/2024-03-19-choosing-between-gzip-brotli-and-zstandard-compression

## Early-Abort / Extraktion
- Streaming lesen, nach </body> oder 1 MB stoppen (habt ihr bei 1 MB); Abbruch "nach Haupttext" ist ohne Parsing unzuverlässig, besser nach dem Ende von <body>/ersten 300-500 KB.
- trafilatura ca. 20-45 ms pro Seite im Vergleich: rs-trafilatura 44 ms/Seite, go-trafilatura ca. 21 ms; Python-trafilatura ist langsamer und der eigentliche Engpass neben dem Netz. https://arxiv.org/html/2605.21097v1 , https://github.com/markusmobius/content-extractor-benchmark ; selectolax ca. 2x lxml bei reiner Extraktion: https://dataresearchtools.com/best-python-html-parsers-2026-lxml-vs-beautifulsoup-vs-selectolax/

## Offen / nicht belegt
- niquests-Lauf im Live-Test hing (Python 3.9 + uv); mit 3.12 neu testen.
- Keine unabhängige Zahl für TLS-Resumption in Python-Clients gefunden.
- Alle Live-Zahlen sind Einzelläufe.
