# Eval-Set queries_big: Belege

Stand 2026-10-11. Jede Regex (case-insensitive) wurde gegen den abgerufenen Text der Quelle getestet und trifft dort. Spalten: id, Kategorie, Frage, Antwort, Quelle, Treffer-Ausschnitt.

## b-de-01 (LT)
- Frage: Welches Architekturbüro entwarf den Berliner Hauptbahnhof?
- Antwort: von Gerkan, Marg und Partner (Meinhard von Gerkan)
- Regex: `Gerkan`
- Quelle: https://de.wikipedia.org/wiki/Berlin_Hauptbahnhof
- Beleg: "... Mai 2006"},"Stilllegung":{"wt":""},"Architekt":{"wt":"[[Meinhard von Gerkan]] ([[Gerkan, Marg und Partner/gmp]])"},"Architekt_Bezeichnung":{"wt":..."

## b-de-02 (LT)
- Frage: In welchem Jahr fand der erste Agentenaustausch auf der Glienicker Brücke statt?
- Antwort: 1962 (Powers gegen Abel)
- Regex: `1962`
- Quelle: https://de.wikipedia.org/wiki/Glienicker_Br%C3%BCcke
- Beleg: "...rücke“ Unterabschnitt „Agentenbrücke“ umschalten 3.1 Agentenaustausch 1962 3.2 Agentenaustausch 1985 3.3 Agentenaustausch 1986 4 Die Glienicker ..."

## b-de-03 (LT)
- Frage: Wie lang ist der Mittellandkanal in Kilometern?
- Antwort: rund 325 km (325,3 km laut Wikipedia)
- Regex: `325[,.]\d\s?(km|Kilometer)`
- Quelle: https://de.wikipedia.org/wiki/Mittellandkanal
- Beleg: "...g MLK Lage Nordrhein-Westfalen , Niedersachsen , Sachsen-Anhalt Länge 325,3 km [ 1 ] Erbaut 1906 bis 1942 (Einstellung der Arbeiten an der Elbequeru..."

## b-de-04 (LT)
- Frage: Welcher Baumeister errichtete Schloss Sanssouci in Potsdam?
- Antwort: Georg Wenzeslaus von Knobelsdorff
- Regex: `Knobelsdorff`
- Quelle: https://de.wikipedia.org/wiki/Schloss_Sanssouci
- Beleg: "...rde 1745 bis 1747 durch Friedrich den Großen von Georg Wenzeslaus von Knobelsdorff im Rokokostil erbaut. Hervorzuheben sind das Konzertzimmer , die Schl..."

## b-de-05 (LT)
- Frage: Wie tief ist der Bodensee an seiner tiefsten Stelle?
- Antwort: 251 m
- Regex: `25[01](,\d+)?\s?(m\b|Meter)`
- Quelle: https://de.wikipedia.org/wiki/Bodensee
- Beleg: "...gnach ) [ 3 ] Volumen 48 km³ [ 3 ] Umfang 273 km [ 3 ] Maximale Tiefe 251,14 m [ 4 ] Mittlere Tiefe 90 m Einzugsgebiet 11.487 km² [ 5 ] Besonderheit..."

## b-de-06 (LT)
- Frage: Wie hoch ist der Kölner Dom?
- Antwort: 157 m
- Regex: `157([,.]\d+)?\s?(m\b|Meter)`
- Quelle: https://de.wikipedia.org/wiki/K%C3%B6lner_Dom
- Beleg: "...] Der Dom ist eine der größten Kathedralen im gotischen Baustil , mit 157,22 Metern die vierthöchste Kirche der Welt und nach dem Ulmer Münster der zwei..."

## b-en-01 (LT)
- Frage: Who designed the Sydney Opera House?
- Antwort: Jørn Utzon
- Regex: `J(ø|o|ø)rn Utzon`
- Quelle: https://en.wikipedia.org/wiki/Sydney_Opera_House
- Beleg: ".../MR Hornibrook]] (level 2 and 3 and interiors)"},"architect":{"wt":"[[Jørn Utzon]]<br />[[Peter Hall (architect)/Peter Hall]]"},"structural_engineer":..."

## b-en-02 (LT)
- Frage: Which scientist discovered Pluto's moon Charon in 1978?
- Antwort: James Christy
- Regex: `(James|Jim) (W\. )?Christy`
- Quelle: https://en.wikipedia.org/wiki/Charon_(moon)
- Beleg: "...the New Horizons spacecraft on July 14, 2015. Discovery Discovered by James W. Christy Discovery date June 22, 1978 Designations Designation Pluto I [ 1 ] P..."

## b-en-03 (LT)
- Frage: What was the first commercially available single-chip microprocessor?
- Antwort: Intel 4004 (1971)
- Regex: `Intel 4004`
- Quelle: https://en.wikipedia.org/wiki/Microprocessor
- Beleg: "...truments TMS 1802NC (1971) 3.1.6 Pico/General Instrument (1971) 3.1.7 Intel 4004 (1971) 3.2 8-bit designs 3.3 12-bit designs 3.4 16-bit designs 3.5 32..."

## b-en-04 (LT)
- Frage: How tall is the Burj Khalifa in metres?
- Antwort: 828 m
- Regex: `\b828\s?(m\b|metres|meters)`
- Quelle: https://en.wikipedia.org/wiki/Burj_Khalifa
- Beleg: "...5 billion AED 5.5 billion Owner Emaar Properties Height Architectural 828 m (2,717 ft) Tip 829.8 m (2,722 ft) Antenna spire 242.5 m (796 ft) Roof..."

## b-en-05 (LT)
- Frage: In what year was the Voyager 1 space probe launched?
- Antwort: 1977
- Regex: `launched.{0,60}1977|1977.{0,60}launched`
- Quelle: https://en.wikipedia.org/wiki/Voyager_1
- Beleg: "...o sidebar hide From Wikipedia, the free encyclopedia NASA space probe launched in 1977 For other uses, see Voyager 1 (disambiguation) . {{Cite web /title=Vo..."

## b-en-06 (LT)
- Frage: Who was the first person to reach the geographic South Pole?
- Antwort: Roald Amundsen (Dec 1911)
- Regex: `Amundsen`
- Quelle: https://en.wikipedia.org/wiki/South_Pole
- Beleg: "...phy subsection 1.1 Ceremonial South Pole 1.2 Historic monuments 1.2.1 Amundsen's Tent 1.2.2 Argentine flagpole 2 Exploration Toggle Exploration subs..."

## b-en-07 (LT)
- Frage: Which architect designed the Guggenheim Museum Bilbao?
- Antwort: Frank Gehry
- Regex: `Gehry`
- Quelle: https://en.wikipedia.org/wiki/Guggenheim_Museum_Bilbao
- Beleg: "...on":{"wt":"[[Abando]], [[Bilbao]], Spain"},"architect":{"wt":"[[Frank Gehry]]"},"coordinates":{"wt":"{{Coord/43/16/07/N/2/56/02/W/region:ES_type:..."

## b-en-08 (LT)
- Frage: What is the length of the main span of the Akashi Kaikyo Bridge in metres?
- Antwort: 1,991 m
- Regex: `1[,.]?991\s?(m\b|metres|meters)`
- Quelle: https://en.wikipedia.org/wiki/Akashi_Kaiky%C5%8D_Bridge
- Beleg: "...16 ft) [ 3 ] Height 282.8 metres (928 ft) (pylons) [ 3 ] Longest span 1,991 metres (6,532 ft) [ 3 ] Clearance below 65 metres (213 ft) [ 3 ] History Des..."

## b-en-09 (LT)
- Frage: Who composed the opera The Magic Flute?
- Antwort: Wolfgang Amadeus Mozart
- Regex: `Mozart`
- Quelle: https://en.wikipedia.org/wiki/The_Magic_Flute
- Beleg: "... From Wikipedia, the free encyclopedia 1791 opera by Wolfgang Amadeus Mozart For other uses, see The Magic Flute (disambiguation) . The Magic Flut..."

## b-de-07 (ZD)
- Frage: Wie groß ist die Lichtgeschwindigkeit im Vakuum in Metern pro Sekunde?
- Antwort: 299.792.458 m/s
- Regex: `299[.\s  ]?792[.\s  ]?458`
- Quelle: https://de.wikipedia.org/wiki/Lichtgeschwindigkeit
- Beleg: ".../math>"},"Art":{"wt":"[[Geschwindigkeit]]"},"WertSI":{"wt":"{{ZahlExp/299792458/post=<math>\\mathrm{\\tfrac{m}{s}}</math>}}"},"Genauigkeit":{"wt":"0"..."

## b-de-08 (ZD)
- Frage: An welchem Datum wurde das Euro-Bargeld in Deutschland eingeführt?
- Antwort: 1. Januar 2002
- Regex: `1\.\s?Januar 2002|01\.01\.2002`
- Quelle: https://de.wikipedia.org/wiki/Euro
- Beleg: "...er Euro wurde am 1. Januar 1999 als Buchgeld und drei Jahre später am 1. Januar 2002 als Bargeld eingeführt. [ 2 ] Damit löste er die nationalen Währungen..."

## b-de-09 (ZD)
- Frage: Auf wie viele Sitze ist die Regelgröße des Bundestags seit der Wahlrechtsreform von 2023 festgelegt?
- Antwort: 630
- Regex: `\b630\b`
- Quelle: https://de.wikipedia.org/wiki/Bundestagswahlrecht
- Beleg: "... weniger. Ab der Wahl zum 21. Deutschen Bundestag werden in der Regel 630 Sitze vergeben, sodass die Zahl der Wahlkreise mit 299 erstmals wenig..."

## b-de-10 (ZD)
- Frage: Bei welcher Temperatur in Grad Celsius schmilzt Eisen?
- Antwort: ca. 1538 °C
- Regex: `1[.\s]?53[58]\s?°\s?C`
- Quelle: https://de.wikipedia.org/wiki/Eisen
- Beleg: "...200 °C erreichen konnte, der Schmelzpunkt von reinem Eisen jedoch bei 1538 °C liegt (Schmelzpunkt von reinem Zementit , Fe 3 C: 1250 °C). Die Rennö..."

## b-en-10 (ZD)
- Frage: What is the exact value of the Planck constant in joule-seconds since the 2019 SI redefinition?
- Antwort: 6.62607015e-34
- Regex: `6\.62607015|6\.626\s?070\s?15`
- Quelle: https://en.wikipedia.org/wiki/Planck_constant
- Beleg: "...\displaystyle {\mathsf {M}}{\mathsf {L}}^{2}{\mathsf {T}}^{-1}} Value 6.626 070 15 × 10 −34 J⋅Hz −1 4.135 667 696 ... × 10 −15 eV⋅Hz −1 "},"symbols":{"w..."

## b-en-11 (ZD)
- Frage: How many member states does the European Union have?
- Antwort: 27
- Regex: `\b27\s+(member\s+)?(states|countries)`
- Quelle: https://en.wikipedia.org/wiki/European_Union
- Beleg: "...pa .eu The European Union ( EU ) is a political and economic union of 27 member states that are located primarily in Europe . [ 10 ] [ 11 ] A supranational ..."

## b-en-12 (ZD)
- Frage: On what date did Apollo 11 land on the Moon?
- Antwort: 20 July 1969
- Regex: `July 20,? 1969|20 July 1969`
- Quelle: https://en.wikipedia.org/wiki/Apollo_11
- Beleg: "...e = lander\n / component = [[Apollo Lunar Module]]\n / arrival_date = July 20, 1969, 20:17:40&nbsp;UTC<ref name=\"ALSJ 1\" />\n / departure_date = July 2..."

## b-en-13 (ZD)
- Frage: How many keys does a standard modern piano have?
- Antwort: 88
- Regex: `\b88\s+keys`
- Quelle: https://en.wikipedia.org/wiki/Piano
- Beleg: "...st modern pianos have 52 white keys and 36 black keys, for a total of 88 keys (seven octaves plus a minor third, from A 0 to C 8 ). Some older pian..."

## b-en-14 (ZD)
- Frage: What is the atomic number of gold?
- Antwort: 79
- Regex: `atomic number( of)? 79|element 79|\b79 protons`
- Quelle: https://en.wikipedia.org/wiki/Gold
- Beleg: "...le is about the element. For other uses, see Gold (disambiguation) . "Element 79" redirects here. For the anthology, see Element 79 (anthology) . Chem..."

## b-en-15 (ZD)
- Frage: How many chromosomes does a typical human somatic cell have?
- Antwort: 46 (23 pairs)
- Regex: `\b46\s+chromosomes|23 pairs`
- Quelle: https://en.wikipedia.org/wiki/Chromosome
- Beleg: "...ation. All act in the same way during cell division. Human cells have 23 pairs of chromosomes (22 pairs of autosomes and one pair of sex chromosomes..."

## b-de-11 (TE)
- Frage: Welchen Standardport nutzt SSH?
- Antwort: 22
- Regex: `Port:?\s*22\b|\b22/TCP|TCP[- ]Port 22`
- Quelle: https://de.wikipedia.org/wiki/Secure_Shell
- Beleg: "...: Datenübertragung auf Anwendungsschicht, Fernsteuerung von Computern Port: 22/TCP SSH im TCP/IP-Protokollstapel : Anwendung SSH Transport TCP Inter..."

## b-de-12 (TE)
- Frage: Welche Standardportnummer verwendet MongoDB?
- Antwort: 27017
- Regex: `27017`
- Quelle: https://www.mongodb.com/docs/manual/reference/default-mongodb-port/
- Beleg: "...lists the default TCP ports used by MongoDB: Default Port Description 27017 Default port for mongod and mongos . Change this port with port or --..."

## b-de-13 (TE)
- Frage: Was bedeutet der HTTP-Statuscode 503?
- Antwort: Service Unavailable
- Regex: `Service Unavailable|Dienst nicht verf`
- Quelle: https://de.wikipedia.org/wiki/HTTP-Statuscode
- Beleg: "...rfüllen, weil er seinerseits eine ungültige Antwort erhalten hat. 503 Service Unavailable Der Server steht temporär nicht zur Verfügung, zum Beispiel wegen Übe..."

## b-en-16 (TE)
- Frage: What is the default TCP port of PostgreSQL?
- Antwort: 5432
- Regex: `listens on; 5432 by default`
- Quelle: https://www.postgresql.org/docs/current/runtime-config-connection.html
- Beleg: "...ly be set at server start. port ( integer ) # The TCP port the server listens on; 5432 by default. Note that the same port number is used for all IP addresses the serv..."

## b-en-17 (TE)
- Frage: What is the default port used by Redis?
- Antwort: 6379
- Regex: `127\.0\.0\.1:6379`
- Quelle: https://redis.io/docs/latest/develop/tools/cli/
- Beleg: "...quote \\ - backslash For example, to return Hello World on two lines: 127.0.0.1:6379> SET mykey "Hello\nWorld" OK 127.0.0.1:6379> GET mykey Hello World Wh..."

## b-en-18 (TE)
- Frage: What does HTTP status code 418 mean?
- Antwort: I'm a teapot
- Regex: `I.m a teapot`
- Quelle: https://en.wikipedia.org/wiki/List_of_HTTP_status_codes
- Beleg: "... meet the requirements of the Expect request-header field. [ 28 ] 418 I'm a teapot (RFC 2324, RFC 7168) This code was defined in 1998 as one of the trad..."

## b-en-19 (TE)
- Frage: Which HTTP status code means Too Many Requests?
- Antwort: 429
- Regex: `\b429\b.{0,20}Too Many Requests`
- Quelle: https://en.wikipedia.org/wiki/List_of_HTTP_status_codes
- Beleg: "...y has modified the state on the server, leading to a conflict. [ 34 ] 429 Too Many Requests (RFC 6585) The user has sent too many requests in a given amount of t..."

## b-en-20 (TE)
- Frage: Which Cargo command creates a new Rust package?
- Antwort: cargo new
- Regex: `cargo new`
- Quelle: https://doc.rust-lang.org/cargo/commands/cargo-new.html
- Beleg: "... cargo new - The Cargo Book Keyboard shortcuts Press ← or → to navigate between ..."

## b-en-21 (TE)
- Frage: Which Python PEP introduced the walrus operator (assignment expressions)?
- Antwort: PEP 572
- Regex: `PEP 572|Assignment Expressions`
- Quelle: https://peps.python.org/pep-0572/
- Beleg: "... PEP 572 – Assignment Expressions / peps.python.org Following system colour sc..."

## b-en-22 (TE)
- Frage: In Git, which command shows the commit history?
- Antwort: git log
- Regex: `git log`
- Quelle: https://git-scm.com/docs/git-log
- Beleg: "...git by running git --version NAME git-log - Show commit logs SYNOPSIS git log [ <options> ] [ <revision-range> ] [[ -- ] <path> …​] DESCRIPTION Sho..."

## b-de-14 (DE)
- Frage: Wie lange beträgt die regelmäßige Verjährungsfrist nach dem BGB?
- Antwort: drei Jahre (§ 195 BGB)
- Regex: `drei Jahre|3 Jahre`
- Quelle: https://www.gesetze-im-internet.de/bgb/__195.html
- Beleg: "...Regelmäßige Verjährungsfrist Die regelmäßige Verjährungsfrist beträgt drei Jahre. zum Seitenanfang Impressum Datenschutz Barrierefreiheitserklärung Fe..."

## b-de-15 (DE)
- Frage: Wo hat das Bundesverfassungsgericht seinen Sitz?
- Antwort: Karlsruhe
- Regex: `Karlsruhe`
- Quelle: https://de.wikipedia.org/wiki/Bundesverfassungsgericht
- Beleg: "...ungsgerichts'' /wayback=20120118205635}}</ref>"},"hauptsitz":{"wt":"[[Karlsruhe]]"},"Vorsitz":{"wt":"[[Stephan Harbarth]] ([[#Präsidenten und Vizeprä..."

## b-de-16 (DE)
- Frage: Aus wie vielen Richtern besteht das Bundesverfassungsgericht insgesamt?
- Antwort: 16 (zwei Senate zu je acht)
- Regex: `(16|sechzehn) Richter|acht Richter`
- Quelle: https://de.wikipedia.org/wiki/Bundesverfassungsgericht
- Beleg: "...ten und Vizepräsidenten/Vizepräsidentin]])"},"anz_mitarbeiter":{"wt":"16 Richter,<br />insg. ca. 270 Mitarbeiter"},"Haushaltsvolumen":{"wt":"44,5 Mio...."

## b-de-17 (DE)
- Frage: Ab welchem Alter ist man in Deutschland volljährig und voll geschäftsfähig?
- Antwort: 18 Jahre (§ 2 BGB)
- Regex: `18\.? Lebensjahr|achtzehn|18 Jahre`
- Quelle: https://www.gesetze-im-internet.de/bgb/__2.html
- Beleg: "...tt der Volljährigkeit Die Volljährigkeit tritt mit der Vollendung des 18. Lebensjahres ein. zum Seitenanfang Impressum Datenschutz Barrierefreiheitserklär..."

## b-de-18 (DE)
- Frage: Wie lange dauert die Amtszeit des Bundespräsidenten?
- Antwort: fünf Jahre (Art. 54 GG)
- Regex: `f(ü|ue)nf Jahre|5 Jahre`
- Quelle: https://www.gesetze-im-internet.de/gg/art_54.html
- Beleg: "...te Lebensjahr vollendet hat. (2) Das Amt des Bundespräsidenten dauert fünf Jahre. Anschließende Wiederwahl ist nur einmal zulässig. (3) Die Bundesvers..."

## b-de-19 (DE)
- Frage: In welcher Stadt hat die Bundesagentur für Arbeit ihren Hauptsitz?
- Antwort: Nürnberg
- Regex: `N(ü|ue)rnberg`
- Quelle: https://de.wikipedia.org/wiki/Bundesagentur_f%C3%BCr_Arbeit
- Beleg: "...ung und Arbeitslosenversicherung neu errichtet"},"hauptsitz":{"wt":"[[Nürnberg]], {{DE-BY}}"},"behördenleiter":{"wt":"[[Andrea Nahles]] ([[Vorstands..."

## b-de-20 (DE)
- Frage: Wie viele Werktage Mindesturlaub gibt es nach dem Bundesurlaubsgesetz bei einer Sechstagewoche?
- Antwort: 24 Werktage (§ 3 BUrlG)
- Regex: `24 Werktage`
- Quelle: https://www.gesetze-im-internet.de/burlg/__3.html
- Beleg: "...etz) § 3 Dauer des Urlaubs (1) Der Urlaub beträgt jährlich mindestens 24 Werktage. (2) Als Werktage gelten alle Kalendertage, die nicht Sonn- oder gese..."

## b-de-21 (DE)
- Frage: In welcher Stadt sitzt das Bundesamt für Sicherheit in der Informationstechnik (BSI)?
- Antwort: Bonn
- Regex: `Bonn`
- Quelle: https://de.wikipedia.org/wiki/Bundesamt_f%C3%BCr_Sicherheit_in_der_Informationstechnik
- Beleg: "...9 Zentralstelle für Sicherheit in der Informationstechnik ) Hauptsitz Bonn , Nordrhein-Westfalen Nordrhein-Westfalen Präsidentin Claudia Plattne..."

## b-de-22 (DE)
- Frage: Welcher Artikel des Grundgesetzes erklärt die Würde des Menschen für unantastbar?
- Antwort: Artikel 1 Absatz 1
- Regex: `Art(ikel|\.)\s?1\b.{0,200}unantastbar|unantastbar`
- Quelle: https://www.gesetze-im-internet.de/gg/art_1.html
- Beleg: "...r die Bundesrepublik Deutschland Art 1 (1) Die Würde des Menschen ist unantastbar. Sie zu achten und zu schützen ist Verpflichtung aller staatlichen Ge..."

## b-de-23 (DE)
- Frage: Welches Bundesland hat die meisten Einwohner?
- Antwort: Nordrhein-Westfalen
- Regex: `Nordrhein-Westfalen`
- Quelle: https://de.wikipedia.org/wiki/Liste_der_deutschen_Bundesl%C3%A4nder_nach_Bev%C3%B6lkerung
- Beleg: "...völkerung Einwohner 2024 1990 2024 2015 2010 2000 1990 1980 1970 1960 Nordrhein-Westfalen Nordrhein-Westfalen 21,6 % ▼ 21,8 % 18.034.454 17.865.516 17.845.154 ..."

## b-de-24 (AK)
- Frage: Welche Partei verpasste bei der Landtagswahl 2026 in Baden-Württemberg erstmals den Einzug in den Landtag?
- Antwort: FDP
- Regex: `FDP verpasste erstmals`
- Quelle: https://de.wikipedia.org/wiki/Landtagswahl_in_Baden-W%C3%BCrttemberg_2026
- Beleg: "...ent ihr bundesweit schlechtestes Ergebnis bei einer Landtagswahl. Die FDP verpasste erstmals seit Bestehen des Bundeslandes den Einzug in den Landtag. Die Partei ..."

## b-de-25 (AK)
- Frage: In welchem Stadion fand das Finale der Fußball-WM 2026 statt?
- Antwort: MetLife Stadium (East Rutherford, New Jersey)
- Regex: `MetLife`
- Quelle: https://de.wikipedia.org/wiki/Fu%C3%9Fball-Weltmeisterschaft_2026
- Beleg: "...tekenstadion/Mexiko-Stadt}})"},"endspiel":{"wt":"19. Juli 2026 ({{USA/MetLife Stadium/East Rutherford}})"},"spiele":{"wt":"104"},"gespielte spiele"..."

## b-en-23 (AK)
- Frage: Which country won the 2026 FIFA World Cup final against Argentina?
- Antwort: Spain (1-0 after extra time)
- Regex: `Spain won the final against`
- Quelle: https://en.wikipedia.org/wiki/2026_FIFA_World_Cup
- Beleg: "...na , Spain , France , and England ) advanced to the semifinals. [ 7 ] Spain won the final against defending champion Argentina 1–0 after extra time , winning its secon..."

## b-en-24 (AK)
- Frage: Which country topped the medal table at the 2026 Winter Olympics in Milan Cortina?
- Antwort: Norway
- Regex: `Norway finished at the top of the medal table`
- Quelle: https://en.wikipedia.org/wiki/2026_Winter_Olympics
- Beleg: "...6 Winter Olympics and Torino hosted the 2006 Winter Olympics . [ 10 ] Norway finished at the top of the medal table for the fourth successive Winter Olympics with 18 gold and 41 medals ..."

## b-en-25 (AK)
- Frage: Which stadium hosted the opening ceremony of the 2026 Winter Olympics?
- Antwort: San Siro (Stadio San Siro, Milan)
- Regex: `San Siro in Milan hosted the opening ceremony`
- Quelle: https://en.wikipedia.org/wiki/2026_Winter_Olympics
- Beleg: "...n's Santa Giulia district and hosted ice hockey. [ 21 ] [ 22 ] Stadio San Siro in Milan hosted the opening ceremony, and the historic Verona Arena in Verona hosted the closing ceremony...."
