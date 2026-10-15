#!/usr/bin/env python3
"""Pruefsatz fuer die Themen-Bibliothek, zeitlich getrennt vom Bau (Auftrag 14.10.2026).

Die Bibliothek darf nur aus Funden bis 01.10.2026 entstehen (research-agent tasks.json).
Geprueft wird gegen spaetere Funde: Programme Runde 8 (04.10.), Hackathon-Radar (13.10.),
Programme Runde 9 (14.10.). Diese Links hat Sami nicht bestaetigt (Recherche-Ergebnis,
nicht Gold), darum heissen sie hier "spaetere Funde".

Ausgabe eval/bibliothek/pruefsatz.json: je Thema eigene Testfragen (kommen nicht in
tasks.json vor und gehen nie in Bau oder Training) und die Links der spaeteren Funde.
Rohdaten: eval/private/bibliothek/pruefsatz-roh.json (aus den drei Board-Artifacts).
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROH = HERE.parent / "private" / "bibliothek" / "pruefsatz-roh.json"
TASKS = Path.home() / ".claude/skills/research-agent/evals/tasks.json"

# Board-Kategorie -> Regal. Regale wie die Aufgaben in tasks.json, damit der Bau sie kennt.
THEMA = {
    "Hackathon": "hackathon",
    "Politik international": "politik", "Parlament": "planspiel",
    "Stipendium": "uni", "Sommer 2027": "ausland_bezahlt",
    "Gründer": "gruender", "Preis": "gruender", "EdTech": "gruender", "Weltweit": "gruender",
    "Event Berlin": "konferenz", "KI-Firmen": "konferenz",
}
# Kein eigener Seitentyp (Formular-Hoster, Profile), ueber die Domain nicht pruefbar.
OHNE = ("docs.google.com", "forms.gle", "linkedin.com", "airtable.com", "polls.", "typeform.com")

FRAGEN = {
    "hackathon": [
        "Hackathon Deutschland November 2026 Preisgeld Anmeldung",
        "KI Hackathon Berlin Wochenende kostenlos teilnehmen",
        "hackathon Munich December 2026 prize money register",
        "Hackathons Januar Februar 2027 online Preisgeld",
        "Robotik Hackathon 2026 Deutschland Bewerbung",
    ],
    "politik": [
        "UN Jugenddelegierte Deutschland Bewerbung 2027",
        "youth delegate United Nations Germany application 2027",
        "Jugenddelegation G20 G7 Y20 Ausschreibung",
        "Young Trade Leaders WTO youth programme apply",
        "Sicherheitskonferenz Jugend Berlin 2027 Bewerbung",
    ],
    "planspiel": [
        "Jugendforum Landtag Brandenburg Bewerbung",
        "Model Congress Europe 2027 Berlin Anmeldung",
        "Model United Nations Europe 2027 financial aid",
    ],
    "uni": [
        "Begabtenförderung Studienanfänger Bewerbung vor dem Abitur",
        "Stipendium Studienwerk Bewerbung 2027 Frist",
        "university full scholarship international students 2027 early action",
        "Liberal Arts College Berlin Stipendium Financial Aid",
    ],
    "ausland_bezahlt": [
        "AI safety research fellowship summer 2027 stipend",
        "paid summer program 2027 London research stipend",
        "open source mentorship paid remote summer 2027",
    ],
    "gruender": [
        "Social Impact Wettbewerb junge Gründer Preisgeld 2027",
        "EdTech Wettbewerb 2027 Einreichung Preisgeld",
        "Förderung Open Source Einzelpersonen 2027 Bewerbung",
        "youth startup competition under 25 Europe 2027",
        "Jugendpreis Engagement 2027 bewerben",
    ],
    "konferenz": [
        "KI Events Berlin Oktober 2026 kostenlos",
        "Gründerkonferenz Berlin November 2026 Tickets",
        "ambassador program AI company community apply",
        "Claude Code Workshop Berlin",
    ],
}


def host(url):
    return (urlsplit(url).hostname or "").removeprefix("www.")


def main():
    roh = json.load(open(ROH))
    alt = json.load(open(TASKS))["tasks"]
    alt_fragen = {q.lower() for t in alt for q in t.get("queries", []) + t.get("queries_niche", [])}
    alt_hosts = {host(u) for t in alt for u in t["gold"]}
    for qs in FRAGEN.values():
        for q in qs:
            assert q.lower() not in alt_fragen, f"Testfrage steht schon in tasks.json: {q}"
    themen = {}
    for e in roh:
        regal = THEMA.get(e["thema"])
        url = (e.get("link") or "").strip()
        if not regal or not url.startswith("http") or any(o in url for o in OHNE):
            continue
        if e.get("status") in ("zu", "nicht"):  # Radar: Anmeldung zu oder nicht fuer Sami
            continue
        t = themen.setdefault(regal, {"fragen": FRAGEN[regal], "funde": []})
        if url not in [f["url"] for f in t["funde"]]:
            t["funde"].append({"url": url, "name": e["name"], "quelle": e["quelle"],
                               "domain_schon_bekannt": host(url) in alt_hosts})
    out = {"stand": "2026-10-15", "bau_bis": "2026-10-01",
           "pruefung": ["runde8 04.10.", "radar 13.10.", "runde9 14.10."],
           "hinweis": "Funde nicht von Sami bestaetigt, schwaecher als Gold",
           "themen": themen}
    json.dump(out, open(HERE / "pruefsatz.json", "w"), ensure_ascii=False, indent=1)
    for k, t in themen.items():
        neu = sum(not f["domain_schon_bekannt"] for f in t["funde"])
        print(f"{k:16} Fragen {len(t['fragen'])}  Funde {len(t['funde'])}  davon Domain neu {neu}")


if __name__ == "__main__":
    main()
