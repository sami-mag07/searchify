# Laptop: Browser-Vergleich Chrome, patchright, ShardBrowser

Misst, wie viele schwierige Seiten jeder Browser lesbar holt und wie schnell. 93 Seiten in drei Gruppen (blockiert, schwer, normal), alles headless, ohne Proxy. Dauer etwa 30 bis 45 Minuten.

ShardBrowser lädt beim ersten Start seinen Chromium-Kern (ca. 170 MB, closed source) vom Hersteller. Darum läuft das auf dem Laptop und nicht auf dem Mac.

## Was du machst

1. Laptop an Strom und WLAN (nicht Handy-Hotspot, sonst verfälschen Netzabbrüche das Ergebnis).
2. PowerShell, `cd C:\tavily`, `claude` starten.
3. Den Block „Auftrag“ reinkopieren, Enter.

## Auftrag

```
Du führst auf diesem Windows-Laptop einen Browser-Vergleich aus dem privaten Repo github.com/sami-mag07/tavily-clone aus. Frag nichts nach, entscheide selbst. Keine Anfragen an Tavily, Serper oder Exa. Nichts pushen ausser in Schritt 6.

1. C:\tavily\tavily-clone aktualisieren (git pull, Branch main). Falls nicht vorhanden: klonen (gh ist angemeldet, sonst gh auth login --web).
2. Eigenes venv C:\tavily\browser-venv mit Python 3.12. Darin genau: playwright==1.63.0, patchright==1.63.0, shardx==2.0.3. Danach: python -m playwright install chromium und python -m patchright install chromium.
3. Rauchtest: python eval\browser_bench.py --backends chrome,patchright,shardx --limit 2 --out C:\tavily\bb-smoke.json. Fällt ein Backend aus, Fehlermeldung lesen und die Ursache beheben (nur Installation oder Aufruf, nicht die Messlogik ändern). Höchstens 3 Versuche je Backend; bleibt eins kaputt, ohne es weitermachen und den Grund notieren.
4. Voller Lauf: python eval\browser_bench.py --backends chrome,patchright,shardx --out C:\tavily\browser-bench-JJJJ-MM-TT.json. Nicht parallel zu anderen Downloads.
5. Kurzbericht C:\tavily\BROWSER-BENCH.txt: die Tabelle am Ende der Ausgabe, Gesamtdauer je Backend, ausgefallene Backends mit Grund, ob Netzabbrüche auftraten.
6. Ergebnis zurück an den Mac: Branch laptop/browser-bench-JJJJ-MM-TT vom aktuellen main, darauf die JSON-Datei nach eval/laptop/ und BROWSER-BENCH.txt committen (vorher Diff auf Schlüssel prüfen), Branch pushen, nie main.
7. Aufräumen: ShardX-Profile löscht das Skript selbst. Den Engine-Cache von shardx nicht löschen, falls wir später nachmessen.
```

## Danach

Auf dem Mac sagst du „Werte den Browser-Vergleich aus“. Entschieden wird dann, ob ShardBrowser als letzte Stufe für den Reader eingebaut wird. Messlatte: Er muss in der Gruppe „blockiert“ deutlich mehr holen als patchright (offen, ohne geschlossenen Kern).
