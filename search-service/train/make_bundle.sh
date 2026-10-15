#!/usr/bin/env bash
# Packt die privaten Messdaten fuer den Laptop in EINE Zip-Datei (laeuft auf dem Mac).
#
#   train/make_bundle.sh --dry-run             # nur Liste + Groesse, schreibt nichts
#   train/make_bundle.sh /Volumes/USB/eval-bundle.zip
#
# Inhalt (nur, was die Messungen brauchen):
#   eval/private/<alle Dateien direkt im Ordner>   Bench-/Score-Dateien, live-tempo, reader_split,
#                                                  tavily_cache, objective-baseline, history-*
#   eval/private/{replay,replay-reader,jina_cache,zuverl-2026-10-13,stand-2026-10-13,
#                 blind,real-tasks,vergleich}/
#   research-agent/evals/      tasks.json, search_eval.py, Kassetten (Gold-Links)
#   research-agent/references/ search.py & Co. (search_eval.py importiert search.py)
# Nicht drin: pause-2026-10-11/, nacht-2026-10-12/, join1/, dashboard/, models/, .env-Dateien,
#             __pycache__, .venv. Vor dem Packen wird nach Schluesseln gesucht, Fund = Abbruch.
#
# Die Zip enthaelt gespeicherte Tavily-Antworten: nur per USB-Stick, AirDrop oder lokalem
# Netz kopieren, NIE auf GitHub oder in eine Cloud laden. Entpacken auf dem Laptop:
#   python train/unpack_bundle.py <zip>
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PRIV="eval/private"
SKILLS="$HOME/.claude/skills"
DIRS=(replay replay-reader jina_cache zuverl-2026-10-13 stand-2026-10-13 blind real-tasks vergleich)

if [ $# -ne 1 ]; then
  echo "Aufruf: train/make_bundle.sh --dry-run | <ziel.zip>" >&2
  exit 2
fi
TARGET="$1"
DRY=0
[ "$TARGET" = "--dry-run" ] && DRY=1

[ -d "$PRIV" ] || { echo "$PRIV fehlt, nichts zu packen" >&2; exit 1; }
[ -d "$SKILLS/research-agent/evals" ] || { echo "$SKILLS/research-agent/evals fehlt" >&2; exit 1; }

LIST_PRIV="$(mktemp)"
LIST_SKILL="$(mktemp)"
trap 'rm -f "$LIST_PRIV" "$LIST_SKILL"' EXIT

# Ausschluesse fuer alle Teile: Secrets, Modelle, Caches, virtuelle Umgebungen.
EXCL=( -not -name '.env' -not -name '.env.*' -not -name '*.pyc' -not -name '.DS_Store'
       -not -path '*/__pycache__/*' -not -path '*/models/*' -not -path '*/.venv/*' -not -path '*/.git/*' )

{
  find "$PRIV" -maxdepth 1 -type f "${EXCL[@]}"
  for d in "${DIRS[@]}"; do
    if [ -d "$PRIV/$d" ]; then
      find "$PRIV/$d" -type f "${EXCL[@]}"
    else
      echo "Hinweis: $PRIV/$d fehlt, uebersprungen" >&2
    fi
  done
} | LC_ALL=C sort > "$LIST_PRIV"

( cd "$SKILLS" && {
    find research-agent/evals -type f "${EXCL[@]}"
    find research-agent/references -maxdepth 1 -type f \( -name '*.py' -o -name '*.md' \) "${EXCL[@]}"
  } | LC_ALL=C sort ) > "$LIST_SKILL"

count_priv=$(wc -l < "$LIST_PRIV" | tr -d ' ')
count_skill=$(wc -l < "$LIST_SKILL" | tr -d ' ')
kb_priv=$(tr '\n' '\0' < "$LIST_PRIV" | xargs -0 du -k | awk '{s += $1} END {print s + 0}')
kb_skill=$( cd "$SKILLS" && tr '\n' '\0' < "$LIST_SKILL" | xargs -0 du -k | awk '{s += $1} END {print s + 0}')
total_mb=$(( (kb_priv + kb_skill) / 1024 ))

echo "eval/private: $count_priv Dateien, $(( kb_priv / 1024 )) MB"
for d in "${DIRS[@]}"; do
  n=$(grep -c "^$PRIV/$d/" "$LIST_PRIV" || true)
  echo "  $d/: $n Dateien"
done
echo "  direkt im Ordner: $(grep -vc "^$PRIV/.*/" "$LIST_PRIV" || true) Dateien"
echo "research-agent: $count_skill Dateien, $(( kb_skill / 1024 )) MB"
echo "Summe unkomprimiert: ca. $total_mb MB (Zip meist 30 bis 50 % davon, JSON/HTML packt gut)"

# Schluessel-Suche: echte Werte, keine Platzhalter. Ein Fund bricht ab.
PATTERN='((^|[^A-Za-z0-9_-])(tvly-[A-Za-z0-9]{12,}|sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,})|(SERPER|EXA|TAVILY|OPENAI|JINA)_API_KEY=[A-Za-z0-9_-]{16,}|SEARXNG_SECRET=[A-Za-z0-9_-]{16,})'
hits="$( { tr '\n' '\0' < "$LIST_PRIV" | xargs -0 grep -lE "$PATTERN" 2>/dev/null || true;
           ( cd "$SKILLS" && tr '\n' '\0' < "$LIST_SKILL" | xargs -0 grep -lE "$PATTERN" 2>/dev/null ) || true; } )"
if [ -n "$hits" ]; then
  echo "ABBRUCH: moegliche Schluessel in diesen Dateien:" >&2
  echo "$hits" >&2
  exit 1
fi
echo "Schluessel-Suche: nichts gefunden"

if [ "$DRY" = 1 ]; then
  echo "Dry-Run: nichts geschrieben. Erste Dateien:"
  head -5 "$LIST_PRIV"
  head -3 "$LIST_SKILL"
  exit 0
fi

case "$TARGET" in
  *.zip) ;;
  *) echo "Ziel muss auf .zip enden" >&2; exit 2 ;;
esac
TARGET_DIR="$(cd "$(dirname "$TARGET")" && pwd)"
TARGET="$TARGET_DIR/$(basename "$TARGET")"
case "$TARGET" in
  "$ROOT"/*) echo "Ziel nicht ins Repo legen (Gefahr, es zu committen). USB-Stick oder anderer Ordner." >&2; exit 2 ;;
esac
free_mb=$(df -Pm "$TARGET_DIR" | awk 'NR==2 {print $4}')
if [ "$free_mb" -lt "$total_mb" ]; then
  echo "Zu wenig Platz in $TARGET_DIR: frei $free_mb MB, gebraucht bis $total_mb MB" >&2
  exit 1
fi
rm -f "$TARGET"
zip -q -X "$TARGET" -@ < "$LIST_PRIV"
( cd "$SKILLS" && zip -q -X "$TARGET" -@ < "$LIST_SKILL" )
echo "Fertig: $TARGET ($(du -m "$TARGET" | awk '{print $1}') MB)"
echo "Nur per USB-Stick, AirDrop oder lokalem Netz kopieren. Nie GitHub, nie Cloud."
