#!/usr/bin/env bash
# Nachtlauf (Linux/WSL2): Daten vorbereiten, falls noch nicht da, dann trainieren.
#
#   bash train/run_night.sh            # volle Nacht
#   bash train/run_night.sh --quick    # Rauchtest in wenigen Minuten
#
# Alles wird mitgeschrieben in train/logs/night-<Zeit>.log.
# Wach halten: unter Linux mit systemd per systemd-inhibit (automatisch).
# Unter WSL2 schlaeft Windows trotzdem ein, siehe train/LAPTOP.md (powercfg).
# Python: .venv-train/bin/python, sonst $PYTHON.
set -uo pipefail

cd "$(dirname "$0")/.."
QUICK=""
[ "${1:-}" = "--quick" ] && QUICK="--quick"

# Einmal neu starten unter systemd-inhibit, damit der Rechner nicht schlafen geht.
if [ -z "${NIGHT_INHIBITED:-}" ] && command -v systemd-inhibit >/dev/null 2>&1 \
   && systemd-inhibit --list >/dev/null 2>&1; then
  export NIGHT_INHIBITED=1
  exec systemd-inhibit --what=sleep:idle:handle-lid-switch --who="tavily-clone" \
       --why="Reranker-Training ueber Nacht" bash "$0" "$@"
fi

PY="${PYTHON:-.venv-train/bin/python}"
mkdir -p train/logs
LOG="train/logs/night-$(date +%Y%m%d-%H%M).log"
exec > >(tee -a "$LOG") 2>&1
export PYTHONUTF8=1 PYTHONUNBUFFERED=1 HF_HUB_DISABLE_SYMLINKS_WARNING=1 TOKENIZERS_PARALLELISM=false

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

say "Nachtlauf startet, Log: $LOG"
[ -x "$PY" ] || { say "Python nicht gefunden: $PY (Einrichtung siehe train/LAPTOP.md)"; exit 1; }
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
else
  say "Warnung: nvidia-smi fehlt. Ohne GPU dauert das Training sehr lange."
fi
"$PY" -c "import torch; print('CUDA verfuegbar:', torch.cuda.is_available())"
df -h . | tail -1

# 1) Daten: nur wenn noch keine fertige Datei mit passender Einstellung da ist.
need_data=1
if [ -f train/data/manifest.json ]; then
  if "$PY" -c "import json,sys; m=json.load(open('train/data/manifest.json')); sys.exit(0 if m['quick']==(sys.argv[1]=='--quick') else 1)" "${QUICK:---full}"; then
    need_data=0
  fi
fi
if [ "$need_data" = 1 ]; then
  say "Schritt 1/2: Daten vorbereiten"
  for attempt in 1 2 3; do
    "$PY" train/prepare_data.py $QUICK && break
    say "prepare_data.py fehlgeschlagen (Versuch $attempt/3), neuer Versuch in 60 s (fertige Quellen bleiben)"
    sleep 60
  done
  [ -f train/data/manifest.json ] || { say "Keine Daten, Abbruch."; exit 1; }
else
  say "Schritt 1/2: Daten schon vorhanden (train/data/manifest.json), uebersprungen"
fi

# 2) Training mit Fortsetzen nach Absturz und kleinerem Batch bei Speichermangel.
NAME="reranker-mminilm"; [ -n "$QUICK" ] && NAME="reranker-mminilm-quick"
BATCH=64; ACC=1
for attempt in 1 2 3 4; do
  extra=()
  if ls "train/checkpoints/$NAME"/checkpoint-* >/dev/null 2>&1; then extra+=(--resume); fi
  say "Schritt 2/2: Training (Versuch $attempt, Batch $BATCH x $ACC) ${extra[*]:-}"
  RUNLOG="train/logs/last-train-output.txt"
  "$PY" train/train_reranker.py $QUICK --batch "$BATCH" --grad-accum "$ACC" ${extra[@]+"${extra[@]}"} 2>&1 | tee "$RUNLOG"
  rc=${PIPESTATUS[0]}
  if [ "$rc" = 0 ]; then
    say "Fertig. Ergebnis:"
    "$PY" train/train_reranker.py $QUICK --status
    exit 0
  fi
  if grep -qiE "out of memory|OutOfMemoryError" "$RUNLOG"; then
    if [ "$BATCH" -le 16 ]; then say "Speicher reicht auch mit Batch 16 nicht, Abbruch."; exit 1; fi
    BATCH=$((BATCH / 2)); ACC=$((ACC * 2))
    say "GPU-Speicher voll: weiter mit Batch $BATCH und Gradient-Sammeln $ACC"
    # Zwischenstaende mit anderem Batch passen nicht zusammen: neu beginnen.
    rm -rf "train/checkpoints/$NAME"
  else
    say "Training abgebrochen (Code $rc), Fortsetzen in 60 s"
    sleep 60
  fi
done
say "Nach 4 Versuchen nicht fertig. Log pruefen: $LOG"
exit 1
