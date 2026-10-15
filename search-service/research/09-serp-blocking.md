# 09 SERP ohne Blockierung (Stand 11.10.2026)

Methode: search.py (Tavily), r.jina.ai, Rohdateien von raw.githubusercontent.com, eigene curl-Messung. Kein Browser.
Selbst geprueft: master-settings.yml und master-Quellcode (searx/engines/google.py, searx/network/*), requirements.txt, Wikimedia-Latenzen.

## 1. Befund in einem Satz
Die Blockade haengt an der IP und am Verhalten, nicht an settings.yml. Haltbar wird es nur mit: wenige Engines pro Anfrage, eigener Cache, API-Engines (Wikipedia/Wikidata/Marginalia) als Schnellpfad, ddgs als Fallback mit Rotation.

## 2. TLS-Fingerprint / curl_cffi (geprueft)
- SearXNG-master nutzt curl_cffi bereits als HTTP-Client: requirements.txt pinnt `curl_cffi==0.16.3`; searx/network/client.py setzt `DEFAULT_IMPERSONATE = "chrome"`, `NO_IMPERSONATE = "none"`. Du musst nichts nachinstallieren, nur pruefen dass `pip show curl_cffi` im venv auf master-Stand ist (veraltetes venv = alter Fingerprint).
  - https://raw.githubusercontent.com/searxng/searxng/master/requirements.txt
  - https://raw.githubusercontent.com/searxng/searxng/master/searx/network/client.py
- Pro Netzwerk gibt es `impersonate` und `curl_options` (Network._CLIENT_KWARGS in searx/network/network.py). In der settings.yml nicht dokumentiert, nur im Code. Ob ein Engine-Eintrag `network:` mit `impersonate:` annimmt, vor Einsatz im Code gegenpruefen.
  - https://raw.githubusercontent.com/searxng/searxng/master/searx/network/network.py
- Bei Python-Clients ausserhalb SearXNG: curl_cffi/primp/tls-client liefern denselben echten Chrome-JA4, requests/urllib nicht.
  - https://apiserpent.com/blog/python-tls-fingerprint-impersonation-tested
  - https://github.com/lexiforest/curl_cffi
- Grenze: TLS-Fingerprint hilft gegen "sieht aus wie Skript", nicht gegen IP-Reputation oder Rate. DDG blockt laut SearXNG-Doku die IP, nicht die Session.
  - https://docs.searxng.org/dev/engines/online/duckduckgo.html

## 3. Google 2026
- 17.03.2026: Google lieferte pauschal HTTP 403 (Issue #5867). GSA-iPhone-UA-Trick tot. 22./23.03. fand ein Contributor einen Bypass ueber die Android-Google-App/Nokia-UAs, Fix in PR #5892 gemerged. Der Android-Weg hat laut Autor eigene Rate-Limits ("ein paar hundert aufeinanderfolgende Requests").
  - https://github.com/searxng/searxng/issues/5867
- Im master-Code: URL `https://www.google.com/wml/search?...`, zufaelliger Nokia-User-Agent, `params["impersonate"] = "chrome99_android"`. Also ist Google in master wieder moeglich; dein "access denied" heisst: master aktualisieren oder die IP ist gerade gedrosselt (Suspension 180 s).
  - https://raw.githubusercontent.com/searxng/searxng/master/searx/engines/google.py
- Maintainer sehen langfristig nur "Browser-Sidecar" (echter Browser) als Loesung. Als Dauerloesung nicht planen.
- Google-Sperrseiten: Redirect `sorry.google.com`, 302 `/sorry/index`, HTML-Stub mit `/sorry/` -> SearxEngineCaptcha (1 h Suspension).
  - https://docs.searxng.org/dev/engines/online/google.html

## 4. Engines im Vergleich (Upstream-Defaults aus master-settings.yml, 11.10.2026)
| Engine | Default | Anmerkung |
|---|---|---|
| wikipedia, wikidata | an | API, stabil. wikidata weight 2, timeout 3.0 |
| bing | disabled | nach Rework (PR #5793) zeitweise Start-Fehler "language 'en' not supported" (#5905); master aktuell halten |
| yahoo | disabled | Bing-Index, ohne Begruendung deaktiviert |
| duckduckgo | an | IP-Block ohne Session; CAPTCHA ab ~100 Anfragen passt |
| brave | an | Paging max 10, mehr = Bot-Flag. Dein 429 passt |
| qwant | disabled | CAPTCHA von Server-IP, Doku empfiehlt manuelles Loesen ueber SOCKS |
| mojeek | inactive | Proof-of-Work-CAPTCHA, teuer in CPU |
| startpage | inactive | Anubis-PoW |
| yandex | disabled | Issue #5415: Parsing-Fehler, wackelig |
| marginalia | inactive | braucht Key; Key `public` gibt es, wird oft gedrosselt |
| mwmbl | disabled | Open-Source-Index, klein, API |
| yep | disabled | ungetestet |
Presearch und Ecosia: kein SearXNG-Engine im master-File (grep auf `name: presearch|ecosia` ohne Treffer). Ecosia liefert ohnehin Bing/Google-Ergebnisse. Presearch hat nur Keyed-API. Nicht weiter verfolgt.
- Quellen: https://raw.githubusercontent.com/searxng/searxng/master/searx/settings.yml , https://www.ssdnodes.com/learn/searxng-engines-to-enable-and-disable , https://www.ssdnodes.com/learn/fix-searxng-engine-captcha-errors , https://github.com/searxng/searxng/issues/5415

## 5. Rate-Limits und Suspension (master)
Defaults search.suspended_times: AccessDenied 180 s, TooManyRequests 180 s, Captcha 3600 s, cf_Captcha 15 d, recaptcha 7 d, cf_AccessDenied 1 d. ban_time_on_fail 5, max 120. Suspension liegt im Prozessspeicher, Neustart ohne IP-Wechsel bringt nichts (Engine blockt sofort wieder).
- Eigener Limiter (ip_limit.py): 15 Req/20 s, 150 Req/10 min je Client. Fuer eine reine localhost-Nutzung `limiter: false` lassen (Default), sonst blockst du dich selbst.
- Faustregeln pro Engine (Schaetzung aus Fehlerberichten, nicht offiziell): DDG ~100 Anfragen bis CAPTCHA (deine Messung), Google-Android ein paar hundert, Brave kein Paging >10. Offiziell nur bei Wikimedia dokumentiert (Abschnitt 8).
- Quellen: https://www.ssdnodes.com/learn/searxng-rate-limits-and-429-errors , https://docs.searxng.org/admin/settings/settings.html

## 6. settings.yml-Snippet (Vorschlag, nicht live getestet)
```yaml
use_default_settings: true
server:
  limiter: false
  public_instance: false
  secret_key: "$SEARXNG_SECRET"
search:
  formats: [html, json]
  ban_time_on_fail: 5
  max_ban_time_on_fail: 60
  suspended_times:
    SearxEngineAccessDenied: 600       # nicht die Default-180 s: sonst Dauerfeuer auf eine gesperrte IP
    SearxEngineTooManyRequests: 900
    SearxEngineCaptcha: 3600
outgoing:
  request_timeout: 1.5                 # Ziel <0,8 s: langsame Engines lieber verlieren
  max_request_timeout: 2.5
  useragent_suffix: "tavily-clone (contact: DEINE-MAIL)"   # nur API-Engines (Wikimedia)
  enable_http2: true
engines:
  # Schnell + stabil
  - {name: wikipedia,  disabled: false, timeout: 1.5}
  - {name: wikidata,   disabled: false, timeout: 1.5}
  # Hauptmotoren, bewusst nicht alle gleichzeitig (siehe Rotation, Abschnitt 7)
  - {name: bing,       disabled: false, timeout: 2.0, weight: 1.5}
  - {name: google,     disabled: false, timeout: 2.0}
  - {name: duckduckgo, disabled: false, timeout: 2.0}
  - {name: brave,      disabled: false, timeout: 2.0}
  - {name: yahoo,      disabled: true}
  - {name: qwant,      disabled: true}
  - {name: mojeek,     inactive: true}
  - {name: startpage,  inactive: true}
```
Pruefen: `/stats/errors` als JSON, wenn eine Engine dauerhaft Fehler zeigt, per `remove:` rausnehmen. Jeder Request wartet auf die langsamste Engine.

## 7. Rotation und Cache (Architektur, kein fertiges Tool)
- Pro Query nur 2 bis 3 Engines abfragen, nicht alle: SearXNG Kategorie/Bangs (`!bi !ddg`) oder `engines=bing,duckduckgo` als Parameter. Round-Robin ueber ein Paar-Set, bei Fehler Paar wechseln. Weniger Anfragen je Engine = spaeter CAPTCHA.
- Eigener Layer vor SearXNG: je Engine Token-Bucket (z. B. DDG max 1/s, 60/h), Circuit-Breaker (nach 429/403/CAPTCHA Engine 10 bis 60 min aus), danach ddgs-Backends `bing, brave, mojeek, yahoo, yandex, grokipedia, wikipedia` (laut Readme, `backend="auto"` oder kommagetrennt) als Fallback.
  - https://github.com/deedy5/ddgs
- Cache: Schluessel = normalisierte Query (lowercase, getrimmt, Sprache, Zeitfilter). TTL 6 bis 24 h allgemein, 5 bis 30 min fuer News/Zeitkritisches. SQLite oder Valkey; plus "stale-while-revalidate": abgelaufenen Treffer sofort liefern, im Hintergrund erneuern. Das ist der einzige Hebel, der <0,8 s verlaesslich macht und die Blockrate halbiert.
- Heim-IP ist ein Vorteil: Wohn-IPs werden weit weniger als Rechenzentren geblockt (SSD-Nodes-Guides). Nicht zusaetzlich ueber VPN/Rechenzentrums-Proxy schicken.
  - https://www.ssdnodes.com/learn/fix-searxng-engine-captcha-errors

## 8. Schnellpfad Wikipedia/Wikidata (selbst gemessen)
Fresh-Connection-Latenz vom Mac, 11.10.2026: Wikidata wbsearchentities 0,24 s, REST page/summary 0,26 s, REST search 0,37 s, Action-API search 0,34 s. Alle HTTP 200.
Limits ab 2026: ohne Identifikation 10 Req/min je IP; mit konformem User-Agent (Kontakt-URL/Mail) 200 Req/min; max. 3 parallel; `Retry-After` beachten.
  - https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits
  - https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy
Umsetzung: Query-Klassifikator (Wer/Was/Wann/"Hauptstadt von", Entitaet erkannt) -> parallel Wikipedia-Search + Wikidata-Entity; Antwort als Faktenkarte, SERP aus Cache/Engines nachladen. 200/min bei Cache sind weit mehr als ein Einzelnutzer braucht.
Marginalia: api2.marginalia-search.com, Header `API-Key`, Key `public` zum Testen, oft limitiert; freier eigener Key per Mail anfragbar.
  - https://about.marginalia-search.com/article/api

## 9. Common Crawl / CC-News als lokale Ergaenzung
- Realistisch nur fuer Nischen, nicht als SERP-Ersatz: Common Crawl hat einen URL-Index (CDX/Parquet), keinen Volltext-Suchindex. Ein Monatsarchiv hat ca. 2,1 Mrd. Seiten / 360 TiB (Aug 2026).
  - https://commoncrawl.org/blog
- Der oeffentliche CDX-Server ist "frequently abused and heavily rate limited", IP-Sperren moeglich, nur HTTPS.
  - https://commoncrawl.org/faq
- Nutzbar: (a) CC-News (WARC je Monat) fuer eigenen News-Volltextindex ausgewaehlter Domains, (b) Domain-Lookups ueber ccrawl-cli (`ccrawl news search <domain>`), (c) Bulk-Parquet per DuckDB/Athena-Muster statt CDX-API. Aufwand: Tage, Index lokal mit SQLite-FTS/Tantivy. Fuer ein <0,8-s-SERP-Ziel nicht der erste Schritt.
  - https://github.com/tamnd/ccrawl-cli

## 10. Rechtliche Lage DE/EU (kurz, keine Rechtsberatung)
- Google/Bing-AGB verbieten automatisierte Abfragen. Verstoss ist ein Vertragsthema, kein Straftatbestand; Folgen: IP-Sperre, ggf. Unterlassung bei gewerblichem Massenbetrieb.
- EuGH C-30/14 Ryanair/PR Aviation (2015): Betreiber einer nicht datenbankrechtlich geschuetzten Datenbank duerfen Scraping per AGB wirksam untersagen. BGH I ZR 224/12 (2014, Flugboerse): Screen Scraping ohne technische Schutzmassnahme war zulaessig (Wettbewerbsrecht), kein Freibrief. Technisches Umgehen von CAPTCHAs/Sperren ist heikler: wer gezielt Zugangsschutz aushebelt, verlaesst die sichere Seite (BGH-Argument stuetzt sich auf fehlende Schutzmassnahmen).
  - https://irights.info/artikel/eugh-fluggesellschaft-darf-buchungsportal-an-screen-scraping-hindern/24747
  - https://www.lto.de/recht/hintergruende/h/bgh-urteil-izr22412-screen-scraping-flugdaten-automatisiert-auslesen-ryanair-reiseportal
- Urheberrecht: SERP-Snippets/Titel/URLs kurz anzeigen ist wenig riskant; Volltexte speichern und weitergeben unterliegt UrhG, TDM-Schranke 44b UrhG gilt mit Maschinen-Opt-out (LG Hamburg 2024, Laion).
  - https://www.mofo.com/resources/insights/241004-to-scrape-or-not-to-scrape-first-court-decision
- Praxis: privat/Forschung mit niedriger Rate + Cache + robots.txt der Zielseiten beim Seiten-Fetch ist am unproblematischsten. Als oeffentlicher Dienst mit Google-Scraping steigt das Risiko. Offiziell sauber: Wikimedia-APIs (mit UA), Marginalia, Mwmbl, Common Crawl.

## 11. Offene Punkte
- curl_cffi-`impersonate` pro Engine in settings.yml: nicht dokumentiert, im Code vorhanden, Syntax testen.
- Google-Android-Weg: Lebensdauer unbekannt (Issue seit 20.08.2026 wieder mit Label bug).
- Yandex, yep, Presearch, Ecosia nicht live getestet.
- Konkrete Raten pro Engine nur gemessen/berichtet, nicht offiziell.
