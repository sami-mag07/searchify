# Rechtslage SERP-Scraper (Stand 11.10.2026), nur Fakten, keine Rechtsberatung

## 1. Google LLC v. SerpApi, LLC, 4:25-cv-10826-YGR (N.D. Cal., Chief Judge Gonzalez Rogers)
- Eingereicht 19.12.2025. Docket: https://www.courtlistener.com/docket/72059948/google-llc-v-serpapi-llc/
- Klagegrund NUR DMCA: (1) 17 U.S.C. 1201(a)(1)(A) Umgehung, (2) 1201(a)(2) Trafficking in Umgehungstechnik. Wegen "SearchGuard" (Bot-Schutz, seit Jan 2025). Kein CFAA, kein Vertrag, kein Trespass in der Klage. Complaint: https://storage.courtlistener.com/recap/gov.uscourts.cand.461513/gov.uscourts.cand.461513.1.0.pdf
- Beschluss 20.07.2026 (Dkt. 42): Motion to dismiss GRANTED. Quelle: https://cdn.arstechnica.net/wp-content/uploads/2026/07/Google-v-SerpApi-Order-GRanting-Motion-to-DIsmiss-7-20-26.pdf
  - Ohne leave to amend: Claims soweit SearchGuard Ergebnisse ohne urheberrechtlich geschuetzte Inhalte schuetzt (DMCA nur fuer Schutz von Werken).
  - Mit leave to amend (21 Tage): Ergebnisse mit urheberrechtlichem Bestandteil (Knowledge-Panel-Bilder). Grund: 1201(a)(3)(B) "with the authority of the copyright owner" nicht plausibel dargelegt, Lizenzvertraege nicht vorgelegt.
  - Abgelehnt wurden andere SerpApi-Argumente laut SEJ: DMCA-Anspruch nicht auf Rechteinhaber beschraenkt, SearchGuard muss nicht eng auf Werke zielen. https://www.searchenginejournal.com/google-amends-serpapi-suit-with-content-licensing-terms/585505
  - Discovery-Stay. Kein Urteil zur Legalitaet des Scrapings an sich.
  - Pressestimmen: https://searchengineland.com/google-loses-key-dmca-claims-against-serpapi-in-scraping-lawsuit-483185 , https://serpapi.com/blog/google-v-serpapi-the-court-granted-our-motion-to-dismiss/
- Amended Complaint 10.08.2026 (Dkt. 45): https://storage.courtlistener.com/recap/gov.uscourts.cand.461513/gov.uscourts.cand.461513.45.0.pdf  Shopping/Maps-Theorien raus, Fokus lizenzierte Inhalte (Knowledge-Panel-Fotos), neu: Behauptung, Lizenzgeber (u.a. Reddit) haetten Zugangsschutz gewollt. Laut SerpApi nennt die Klage nur zwei Fotos (Babe Ruth 1921, Willie Mays 1951): https://serpapi.com/blog/google-tried-again-were-moving-to-dismiss-their-claims-a-second-time
- Docket-Verlauf (CourtListener-API/Seite): 25.08. Motion to dismiss Amended Complaint (Dkt. 49); 15.09. Beschluss Dkt. 57 lehnt SerpApis Antrag auf Herausgabe der Lizenzvertraege ab; 15.09. Opposition Google; 29.09. Reply SerpApi; Hearing war angesetzt 13.10.2026 14:00 Oakland; Eintrag 05.10.2026 (Dkt. 60) heisst "Terminate Deadlines and Hearings AND Order", Inhalt von mir NICHT gelesen (vermutlich Termin aufgehoben, unbestaetigt). Entscheidung ueber 2. MTD: bis 11.10. nichts gefunden.
- Andere Beklagte: Google hat laut Suche keinen Serper, Bright Data oder Oxylabs verklagt (keinen Treffer gefunden). Verwandt: Reddit v. SerpApi, Oxylabs, AWMProxy, Perplexity (S.D.N.Y., Okt 2025); 31.07.2026 liess Judge Engelmayer die DMCA-Kernclaims zu (1201(b) gg. SerpApi abgewiesen) laut SEJ/MediaPost: https://www.mediapost.com/publications/article/416950/reddit-can-proceed-with-scraping-claims-against-pe.html (nur ueber Sekundaerquelle gesehen).

## 2. Google AGB und robots.txt
- Google Terms of Service, "Effective July 30, 2026": https://policies.google.com/terms . Woertlich unter den Verboten: "using automated means to access content from any of our services in violation of the machine-readable instructions on our web pages (for example, robots.txt files that disallow crawling, training, or other activities)". Ausserdem "reverse engineering our services ... to extract trade secrets ..., except as allowed by applicable law".
- robots.txt https://www.google.com/robots.txt (abgerufen 11.10.2026): unter "User-agent: *" "Disallow: /search", Ausnahmen "Allow: /search/about", "Allow: /search/howsearchworks", "Allow: /search?*tbm=map" (Zeile 258 zudem "Allow: /search" in einem anderen Block, nicht zugeordnet).
=> Die AGB-Klausel koppelt automatisierten Zugriff an robots.txt, und /search ist dort fuer * disallowed.

## 3. EU/DE (nur Normtext, keine Bewertung)
- 95a UrhG: schuetzt wirksame technische Massnahmen "zum Schutz eines nach diesem Gesetz geschuetzten Werkes oder anderen Schutzgegenstandes" gegen Umgehung ohne Zustimmung des Rechtsinhabers (Zugang oder Nutzung). Also Werkbezug, wie beim US-Urteil. https://www.gesetze-im-internet.de/urhg/__95a.html
- 202a StGB: "unbefugt ... Zugang zu Daten, die nicht fuer ihn bestimmt und die gegen unberechtigten Zugang besonders gesichert sind, unter Ueberwindung der Zugangssicherung" , bis 3 Jahre. Tatbestandsmerkmale: nicht fuer ihn bestimmt, besonders gesichert, Ueberwindung. https://www.gesetze-im-internet.de/stgb/__202a.html
- 44b UrhG (TDM): Vervielfaeltigung zulaessig, wenn kein maschinenlesbarer Nutzungsvorbehalt (Abs. 3). Relevant, falls Ergebnisse Werke enthalten. https://www.gesetze-im-internet.de/urhg/__44b.html
- Einzelnutzer vs. gehosteter Dienst: aus meinen Quellen nur belegt, dass die US-Klage auf Anbieter eines Dienstes (Trafficking, kommerzieller Weiterverkauf, "hundreds of millions" Anfragen) zielt, nicht auf Einzelnutzer. AGB-Bindung ohne Account (Browsewrap) und Rate-Relevanz fuer DE habe ich NICHT mit Primaerquelle belegt.

## 4. Serper.dev ToS (Updated May 29, 2024): https://serper.dev/terms
- Verboten (Usage License): "Modify, copy, prepare derivative works of, decompile, or reverse engineer any materials and software contained on the serper.dev website"; "Attempt to circumvent any restrictions or limits placed on your Serper API key or account"; "Mirror the materials on any other server as-is with no-value-added"; nur ein Account.
- Kein ausdrueckliches Konkurrenzprodukt-Verbot im Text gefunden. Self-description: "Serper is not affiliated with or endorsed by Google", "web-scraped data collected from public domain sources".
