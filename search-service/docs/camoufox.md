# Camoufox als letzte Lade-Stufe (Reader, Extract)

Stand 15.10.2026. Camoufox ist ein quelloffener, getarnter Firefox (github.com/daijro/camoufox,
Paket MIT, Browser MPL-2.0). Er springt nur an, wenn normales Laden, Chrome-TLS (FETCH_FALLBACK)
und Headless-Chromium (READER_RENDER) nichts Lesbares liefern, hoechstens einmal je Anfrage, mit
15 s Zeitlimit. Die Suche (/search, /serper, /exa) nutzt ihn nicht.

## Messung

- 33 Seiten, die am 14.10. auf allen Stufen blockiert blieben (eval/browser_bench_urls.json):
  Camoufox 20, Chromium 9 und 8 (zwei Laeufe), 11 Seiten nur mit Camoufox, keine nur mit Chromium.
- Reader live, zwei Instanzen abwechselnd (eval/stealth_live.py): mit 19/33, ohne 10/33, keine Seite
  verloren. Seiten, die blockiert bleiben, brauchen ~6 s statt ~0,6 s. Normale Seiten unveraendert.
- Daten: eval/private/camoufox-2026-10-15/

## Einrichtung (eigenes venv, nicht das des Dienstes)

Camoufox verlangt playwright < 1.63, der Dienst nutzt 1.63. Darum laeuft der Browser als eigener
Prozess (app/compat/camoufox_worker.py) aus ~/tools/camoufox/.venv. Versionen vom Security-Vetting
15.10. (camoufox 0.5.6, Browser v152.0.4-beta.30), alle Pakete mit Hash:

    mkdir -p ~/tools/camoufox && cd ~/tools/camoufox
    cp ~/Desktop/APP/tavily-clone/docs/camoufox-requirements.in requirements.in
    uv venv -p 3.12 .venv
    uv pip compile --generate-hashes -p 3.12 --exclude-newer 2026-10-01 requirements.in -o requirements.txt
    VIRTUAL_ENV=$PWD/.venv uv pip install --require-hashes -r requirements.txt

Browser von Hand laden, Hash pruefen, in den Camoufox-Cache legen (kein `camoufox fetch`: das nimmt
die neueste Version, auch aus Fremd-Forks):

    curl -L -o cf.zip https://github.com/daijro/camoufox/releases/download/v152.0.4-beta.30/camoufox-152.0.4-beta.30-mac.arm64.zip
    shasum -a 256 cf.zip   # muss 3b43e766574f286a6a63296cf58b660b7a3120952086c869b4df4c9a71604bc3 sein
    V=~/Library/Caches/camoufox/browsers/official/152.0.4-beta.30; mkdir -p $V
    unzip -q cf.zip -d $V && rm cf.zip
    printf '{"version":"152.0.4","build":"beta.30"}' > $V/version.json
    touch ~/Library/Caches/camoufox/.0.5_FLAG
    printf '{"active_version":"browsers/official/152.0.4-beta.30"}' > ~/Library/Caches/camoufox/config.json

In .env: `CAMOUFOX_PYTHON=/Users/<du>/tools/camoufox/.venv/bin/python`. Standard ist aus, `READER_STEALTH=1` schaltet ein. Grund: im gemischten Lauf vom 16.10.
(150 Seiten, 30 mit Bot-Schutz) kaum mehr Seiten, aber p95 4,5 -> 7,5 s und RAM +0,9 bis 1,8 GB.

## Sicherheit

- Alles Netz des Browsers geht ueber app/compat/stealth_proxy.py: nur Port 80/443, der Proxy loest
  den Host selbst auf (alle IPs is_global) und verbindet mit genau dieser IP; TLS bleibt Ende zu Ende
  beim Browser (darum wirkt die Tarnung, anders als bei render.py, das ueber httpx laedt).
- Firefox-Prefs: Proxy auch fuer localhost, kein Direkt-Rueckfall, DoH aus, WebRTC aus, Service Worker
  aus, keine Vorabfragen. Kein GeoIP, keine Standard-Addons (uBlock wuerde ungepinnt nachgeladen).
- Start- und End-URL nur oeffentlich (render.url_allowed), Prozessgruppe wird nach dem Lauf beendet.
- Test: tests/test_stealth.py (Browser-Teil mit CAMOUFOX_PYTHON gesetzt).
- Nicht eingebaut: ShardBrowser (Chromium-Kern closed source vom Hersteller-CDN).
