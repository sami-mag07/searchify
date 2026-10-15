# 14 Serper reverse engineered: Nachbau ja oder nein (Stand 11.10.2026)

Methode: nur oeffentliche Quellen (serper.dev Startseite und Terms, Client-Code von LangChain, CrewAI, Haystack, Onyx, Agno, MCP-Servern), Gerichtsakten auf CourtListener, OpenSERP-Repo und -Issues, eigene Messungen gegen das lokale SearXNG. Kein Request mit Key an Serper und keiner an Google. Ein Sub-Agent hat versehentlich einen GET ohne Key auf google.serper.dev/search geschickt (403, kein Account betroffen).
Rohfunde: `research/c/14-rechtslage.md`, `research/c/14-serper-spec.md`.
Keine Rechtsberatung.

## 1. Ergebnis
- **Google nachbauen wie Serper: nein.** Die Technik ist teuer und laeuft Google hinterher. Seit 09/2026 liefert Google im Browser verschluesselte `/goto`-Links, Whoogle ist seit 07/2026 tot. Rechtlich laeuft die Klage gegen genau dieses Modell noch.
- **Serper-Format nachbauen: ja, fertig.** `experiments/serp_clone/` liefert dasselbe JSON wie `google.serper.dev/search`, aus Bing, Yahoo und Wikipedia ueber das lokale SearXNG. Es misst 0,55 bis 0,80 s und kostet nichts.
- Wer echte Google-Treffer braucht, bezahlt einen Anbieter. Serper kostet 0,30 bis 1,00 $ pro 1000, Brave 5 $ pro 1000 bei 1000 freien Anfragen im Monat.

## 2. Rechtslage (Details in c/14-rechtslage.md)
- **Google v. SerpApi** (4:25-cv-10826-YGR, N.D. Cal.)
  - Die Klage stuetzt sich nur auf den DMCA §1201: Umgehung von "SearchGuard" und Handel mit dem Umgehungswerkzeug. Vertrag, CFAA und Trespass stehen nicht drin.
  - 20.07.2026: Die Klage wurde abgewiesen. Endgueltig gilt das fuer Ergebnisse ohne urheberrechtlich geschuetzten Inhalt. Fuer lizenzierte Inhalte, etwa Bilder im Knowledge Panel oder Reddit, durfte Google nachbessern.
  - 10.08.2026: Amended Complaint.
  - Der zweite Abweisungsantrag ist seit 29.09. vollstaendig geschrieben, eine Entscheidung gibt es noch nicht.
  - Dazu kommt Reddit v. SerpApi/Oxylabs/Perplexity in New York. Dort laufen die DMCA-Ansprueche seit 31.07.2026 weiter.
- **Google-AGB (Fassung 30.07.2026)** verbieten automatisierten Zugriff entgegen maschinenlesbaren Anweisungen. Die `robots.txt` hat `Disallow: /search` fuer alle Bots.
- **Was davon fuer uns zaehlt:**
  - Rein technisch ist Scraping ohne Umgehung nicht entschieden. Der Streit dreht sich um das Umgehen der JavaScript- und Bot-Abwehr.
  - Ein Nachbau mit Browser-Fingerprinting, CAPTCHA-Loesern und Proxy-Rotation ist genau das, was Google als Umgehung angreift.
  - Als Einzelnutzer mit niedriger Rate ist das ein AGB- und Sperrthema. Als gehosteter oder kommerzieller Dienst heisst es "Trafficking", also das Ziel der Klage.
  - DE: §95a UrhG braucht einen Werkbezug. §202a StGB verlangt eine besonders gesicherte Zugangssicherung, die ueberwunden wird. Siehe auch 09-serp-blocking.md, Abschnitt 10.
- **Serper-ToS (Stand 29.05.2024):** verbietet, deren Website und Software zu "decompile, or reverse engineer". Ein Verbot von Konkurrenzprodukten steht dort nicht. Wir nutzen nur oeffentliche Doku und Drittclients, kein Probing.
- **Entscheidung (Audit-Spur):** Kein Prototyp gegen Google. Der Prototyp spricht nur Bing, Yahoo und Wikipedia ueber SearXNG an, so wie das bestehende Backend.

## 3. Serper als Black Box (Spec, Details in c/14-serper-spec.md)
Tags: [D] steht so auf serper.dev, [3] stammt aus Drittclients oder Blogs.

| Thema | Befund |
|---|---|
| Endpunkte | Alle per POST auf `https://google.serper.dev/<typ>`: search, images, news, maps, places, videos, shopping, scholar, patents, autocomplete [D]. Dazu reviews, lens und `scrape.serper.dev` [3]. |
| Auth | Header `X-API-KEY` [3] |
| Batch | Ein Array als Body ergibt ein Array als Antwort, in gleicher Reihenfolge [3]. Die Abrechnung ist unbekannt. |
| Parameter | `q`, `gl`, `hl`, `location`, `num` (1 bis 100, Default 10), `page` (ab 1), `tbs` (`qdr:h/d/w/m/y`), `autocorrect` [3]. Maps zusaetzlich `ll`, `placeId`, `cid`. |
| Antwort search | `searchParameters`, `knowledgeGraph` (title, type, website, imageUrl, description, descriptionSource, descriptionLink, attributes) [D], `answerBox` (title, answer, snippet, snippetHighlighted, link) [3], `organic[]` (title, link, snippet, position, date als Text wie "2 days ago", sitelinks, attributes) [D], `peopleAlsoAsk[]` (question, snippet, title, link) [D], `relatedSearches[]` (query) [D]. `topStories` und `credits` sind nicht belegt. |
| Fehler | Offiziell nichts dokumentiert. Body `{"message": ...}` mit 400, 401/403 und 429 [3]. Bei leerem Guthaben kam bei uns 400 "Not enough credits" (eigene Beobachtung aus app/search.py). |
| Preise | Free: 2500 Credits einmalig. Starter 50 $ fuer 50k (1,00 $ pro 1k, 50 QPS), Standard 375 $ fuer 500k (0,75 $, 100 QPS), Scale 1250 $ fuer 2,5M (0,50 $, 200 QPS), Ultimate 3750 $ fuer 12,5M (0,30 $, 300 QPS) [D]. Credits verfallen nach 6 Monaten. `num` > 10 kostet laut drei Drittquellen 2 Credits [3]. |
| Latenz | Behauptet "1 bis 2 s" [D]. Gemessen hatten wir ~1,0 s, mit `num=20` ~1,7 s (README). |
| Technik | Laut eigener FAQ kein Cache, jede Anfrage geht live an Google [D, Selbstaussage]. Betreiber und Infrastruktur sind nicht oeffentlich. Retries gegen Google deuten auf einen Proxy-Pool [Vermutung]. |

## 4. Wie ein Google-Nachbau aussaehe und warum er sich nicht lohnt
1. **Abruf.** Ohne JavaScript geht nichts mehr. Whoogle ist am 24.07.2026 eingestellt, googlesearch-python ist tot. SearXNG nutzt einen Android/Nokia-Trick und `curl_cffi`, der nach "ein paar hundert" Anfragen drosselt (09-serp-blocking.md). Haltbar ist nur ein echter Chrome pro Anfrage oder ein Pool warmer Browser.
2. **Abwehr.** Google prueft TLS-Fingerprint, JS-Profil, Timing und IP-Ruf. In OpenSERP gibt es dafuer drei Belege:
   - Issue #36 (07/2026): CAPTCHA schon bei der ersten Anfrage, auch ueber eine Heim-IP mit Docker-Chromium.
   - Issue #34: Der eingebaute 2captcha-Loeser zahlte fuer die Loesung, die aber nicht ankam.
   - Issue #53 (28.09.2026): Alle Google-Treffer kommen als verschluesselte `google.*/goto?url=...` ohne Zieldomain. SerpApi hat dasselbe Problem (public-roadmap #4315).
3. **Proxy-Rotation.** Residential kostet 4 bis 15 $ pro GB. Eine gerenderte SERP mit blockierten Bildern hat grob 0,3 bis 0,5 MB [Schaetzung]. Das ergibt etwa 1,50 bis 7,50 $ pro 1000 Anfragen nur fuer Bandbreite, dazu CAPTCHA-Loesungen (2captcha grob 1 bis 3 $ pro 1000 geloeste) und ein Server mit Chrome. Das ist teurer als Serper (0,30 bis 1 $) und etwa gleich teuer wie Brave.
4. **Parser.** Die Google-HTML-Struktur aendert sich staendig (Google-Engine in SearXNG: 403 im Maerz 2026, Bypass, Issue seit 20.08. wieder offen). Die Pflege kostet laufend Stunden.
5. **Heim-IP.** Google sperrt nach OpenSERP #36 schon die erste Anfrage aus Headless-Chrome. Ueber den SearXNG-Android-Weg haelt eine IP einige hundert Anfragen am Stueck, danach kommt `/sorry/` mit 1 h Sperre. Eine offizielle Zahl gibt es nicht.

**OpenSERP als fertige Alternative?** Fuer Google nein, fuer Bing und Yandex ja. Es ist MIT-lizenziert und gut gepflegt (1461 Sterne, Push 28.09.2026), aber Google faellt dort aktuell an CAPTCHA und `/goto`. Bing, DDG und Ecosia laufen, das liefert unser SearXNG aber schon. Das eigene README-Beispiel zeigt `engines_responded: ["bing"]` bei angefragtem `bing,google`.

**Aufwand fuer einen Google-Nachbau:** Ein funktionierender Prototyp braucht 3 bis 5 Tage, danach laufend 2 bis 4 h pro Woche Pflege. Dazu kommen Proxy- und CAPTCHA-Kosten und das Rechtsrisiko aus Abschnitt 2.

## 5. Vergleich
| Weg | Quelle | p50 | Kosten pro 1000 | Risiko |
|---|---|---|---|---|
| SearXNG lokal (Ist) | Bing, Yahoo | 0,93 s mit Pipeline, 0,2 bis 0,6 s roh | 0 | niedrig, AGB-Thema bei Bing/Yahoo |
| `serp_clone` (neu) | wie SearXNG, im Serper-Format | 0,55 bis 0,80 s (5 Fragen) | 0 | wie oben |
| Serper | Google | ~1,0 s | 0,30 bis 1,00 $ | beim Anbieter |
| Brave API | eigener Index | nicht gemessen | 5 $ (1000 frei pro Monat) | keins, offizielle API |
| Google-Eigenbau | Google | geschaetzt 2 bis 5 s mit Browser | geschaetzt 3 bis 10 $ | hoch: technisch und rechtlich |

## 6. Prototyp `experiments/serp_clone/`
- `serp_clone.py`: CLI und Server (`--serve`, Port 8899, nur 127.0.0.1) mit `POST /search` im Serper-Format, Batch per Array.
  - Mapping: `hl`/`gl` → SearXNG-`language`, `tbs` → `time_range`, `page`/`num` → `pageno` plus Folgeseiten.
  - Wikipedia-Infobox → `knowledgeGraph`, Suggestions → `relatedSearches`.
  - Optional `SERP_CLONE_KEY` → `X-API-KEY` Pflicht.
  - Pause zwischen Upstream-Anfragen mindestens 2 s (`SERP_CLONE_MIN_INTERVAL_S`), LRU-Cache mit 256 Eintraegen.
- `test_serp_clone.py`: 7 Offline-Tests (Form, Paging, Cache, 400er), alle gruen.
- Gemessen am 11.10.2026, je eine Anfrage pro Prozess: 0,63, 0,55, 0,65, 0,80 und 0,64 s. Batch, 400 und 404 im Server-Modus geprueft.
- Abweichungen zu Serper:
  - `peopleAlsoAsk` bleibt leer, `answerBox` gibt es nur, wenn SearXNG eine Antwort hat.
  - `date` kommt als ISO-Datum, nicht als "2 days ago".
  - `qdr:h` wird abgelehnt, weil SearXNG keine Stundengrenze kennt.
  - Nur `/search` ist umgesetzt, `/news` fehlt, weil in der settings.yml keine News-Engines aktiv sind.
- Nicht in `app/` eingebaut. Um es zu nutzen, reicht es, in `serper_search` die Basis-URL konfigurierbar zu machen. Lohnt sich aber nur, wenn ein fremdes Tool Serper-JSON erwartet. Das eigene Backend fragt SearXNG ohnehin direkt.

## 7. Offen
- Ob `credits` im Search-Body steht, wie Batch abgerechnet wird und was `topStories` enthaelt, laesst sich nur mit einem bezahlten Account pruefen.
- Brave-API-Latenz selbst messen, bevor Geld fliesst.
- Entscheidung im zweiten Abweisungsantrag Google v. SerpApi beobachten (Docket 72059948).

## 8. Nachtrag 11.10.2026 nachmittags
- SearXNG-Google-Engine (master f4822b3 vom 09.10.) live von der Heim-IP getestet: schon die erste Anfrage HTTP 403, danach 180 s Suspension. Der eingebaute Google-Weg ist damit fuer uns tot.
- GitHub-Suche (Details in c/15-github-serp-repos.md):
  - Was im Oktober 2026 noch geht, ist nur ein getarnter, sichtbarer Browser (Patchright, Warm-up, persistentes Profil) plus eigener Parser und das Aufloesen der `/goto`-Links. Beispiel: nodemaven/google-browser-scraper (25 Sterne, Autor verkauft Proxies).
  - Bei 4get kommt ohne Residential-IP nichts zurueck, OpenSERP liefert nur `/goto`-Links.
  - Wir bauen das nicht: Es ist genau die Umgehung der Bot-Abwehr, um die es in Google v. SerpApi geht.
- Skeptiker-Review:
  - Ob Google fuer unsere Fragen besser ist als Bing/Yahoo, wurde nie gemessen. Die 97 % Top-1 messen die Pipeline, nicht die Trefferliste.
  - Serper kostet als Einzelnutzer effektiv 50 $ pro 6 Monate (Mindestpaket, Verfall).
- Naechster sinnvoller Schritt: A/B-Messung Google gegen Bing/Yahoo auf 100 oder mehr Fragen (DE Long-Tail, News). Google kaeme dabei aus dem kleinsten Serper-Paket, Brave Free laeuft als dritter Arm mit.
