# Laptop: Training über Nacht starten

Du machst nur die 4 Schritte unter „Was du machst“. Den Rest macht Claude Code auf dem Laptop allein.

## Was du machst (ca. 5 Minuten)

1. Laptop ans Ladekabel, Deckel offen lassen.
2. PowerShell öffnen. Falls Claude Code noch nicht drauf ist: `irm https://claude.ai/install.ps1 | iex`, danach PowerShell neu öffnen.
3. Im Ordner `C:\tavily` Claude Code starten: `mkdir C:\tavily; cd C:\tavily; claude`
4. Den ganzen Block unter „Auftrag“ reinkopieren und Enter drücken. Wenn ein Browserfenster für GitHub aufgeht, einmal bestätigen. Danach kannst du schlafen gehen.

Morgen früh: In derselben Claude-Sitzung „Wie ist der Stand?“ fragen. Der fertige Ordner liegt unter `C:\tavily\tavily-clone\models\`, ihn per USB auf den Mac kopieren.

## Auftrag

```
Du richtest auf diesem Windows-Laptop (RTX 3070 Ti Laptop, 8 GB) das Reranker-Training aus dem privaten Repo github.com/sami-mag07/tavily-clone ein und startest es über Nacht. Der Besitzer schläft, frag ihn nichts, entscheide selbst und arbeite bis das Training sicher läuft. Erlaubt ist alles in diesem Auftrag; nichts löschen ausser train/data bei einem fehlgeschlagenen Rauchtest, nichts pushen, keine Anfragen an Tavily, Serper oder Exa.

1. Werkzeuge: Prüfe git, gh (GitHub CLI) und Python 3.12. Fehlt etwas, installiere es mit winget (Git.Git, GitHub.cli, Python.Python.3.12), ohne Rückfrage (--accept-package-agreements --accept-source-agreements). Danach PATH in der Sitzung neu laden.
2. GitHub: Prüfe `gh auth status`. Ist niemand angemeldet, starte `gh auth login --web --git-protocol https` und warte, bis der Besitzer im Browser bestätigt hat. Danach `gh auth setup-git`.
3. Klonen nach C:\tavily\tavily-clone (falls schon da: git pull). Lies dort train/LAPTOP.md komplett, das ist die Anleitung. Wo LAPTOP.md und dieser Auftrag sich widersprechen, gilt LAPTOP.md für Befehle und dieser Auftrag für den Ablauf.
4. Grafikkarte: `nvidia-smi` muss die RTX 3070 Ti zeigen. Wenn nicht: Fehler in C:\tavily\STATUS.txt schreiben und aufhören.
5. Umgebung: venv .venv-train mit Python 3.12, darin torch genau wie in LAPTOP.md (CUDA-Index), dann train/requirements-train.txt. Prüfe danach mit Python, dass torch.cuda.is_available() True ist und die Karte erkannt wird.
6. Rauchtest: `powershell -ExecutionPolicy Bypass -File train\run_night.ps1 -Quick`. Muss ohne Fehler durchlaufen. Wenn nicht: Log lesen, Ursache beheben (nur Umgebung oder Aufruf, nicht die Trainingslogik umschreiben), danach train\data löschen und den Rauchtest wiederholen. Höchstens 3 Versuche.
7. Energie: Damit der Laptop nicht einschläft, im Netzbetrieb Standby und Ruhezustand aus und Deckel zuklappen auf „Nichts unternehmen“ (powercfg /change standby-timeout-ac 0, powercfg /change hibernate-timeout-ac 0, powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0, powercfg /setactive SCHEME_CURRENT). Die alten Werte vorher in C:\tavily\power-vorher.txt notieren.
8. Nachtlauf: Nach erfolgreichem Rauchtest train\data löschen (Rauchtest-Daten) und den vollen Lauf als eigenen Prozess starten, der weiterläuft, auch wenn diese Sitzung endet: Start-Process powershell mit -ExecutionPolicy Bypass -File train\run_night.ps1, Arbeitsordner C:\tavily\tavily-clone.
9. Kontrolle: Nach 15 Minuten prüfen, dass der Datendownload läuft (train/logs). Sobald das Training beginnt, mit `train_reranker.py --status` und nvidia-smi prüfen, dass Schritte zählen und die GPU arbeitet. Danach jede Stunde einmal prüfen. Bei Abbruch: Log lesen, beheben, run_night.ps1 erneut starten (setzt fort).
10. Schreibe nach jeder Prüfung einen kurzen Stand in C:\tavily\STATUS.txt (Uhrzeit, Phase, Schritte, dev-Wert, Fehler). Wenn das Modell fertig unter models\ liegt: Energieeinstellungen aus power-vorher.txt wiederherstellen, Endstand in STATUS.txt schreiben (Ordnername, Dauer, dev-Werte aus der Modellkarte) und aufhören.
```

## Gut zu wissen

- Dauer: geschätzt 2 bis 5 h. Platz: mindestens 25 GB frei auf C:.
- Die Messdaten-Zip vom Mac braucht es für das Training nicht.
- Ob das neue Modell besser ist, wird nur auf dem Mac gemessen, nie auf dem Laptop.
- Alles Weitere zum Dienst, zu den Zielen und Messungen steht in `train/LAPTOP.md`.
