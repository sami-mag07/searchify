#!/usr/bin/env bash
# One benchmark night: record (network), latency, scores (GPU), loop until UNTIL, report.
# Every stage resumes where it stopped, so after a crash the script is simply started again.
# A failing stage does not stop the next one; the loop works with whatever data exists.
#
#   UNTIL=07:30 RECORD_UNTIL=02:00 SCORES_UNTIL=05:00 bench/run_all.sh
#   SMOKE=1 bench/run_all.sh          # 2 questions, 1 model, 50 settings, own out dir
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"

if [ "${SMOKE:-}" = 1 ]; then
  export BENCH_OUT="${BENCH_OUT:-bench/out-smoke}"
  REC=(--limit 2 --gap 2); LAT=(--models mminilm --pairs 32)
  SCO=(--models mminilm --limit 2); LOOP=(--max-configs 50)
else
  export BENCH_OUT="${BENCH_OUT:-bench/out}"
  REC=(--gap 4 --until "${RECORD_UNTIL:-02:00}"); LAT=()
  SCO=(--until "${SCORES_UNTIL:-05:00}"); LOOP=(--until "${UNTIL:-07:30}")
fi
mkdir -p "$BENCH_OUT"

stamp() { date '+%F %T'; }
stage() {
  local name=$1; shift
  echo "$(stamp) stage.start $name"
  "$@"
  echo "$(stamp) stage.end $name rc=$?"
}

gpu=$("$PY" -c 'import torch; print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none")' 2>/dev/null)
echo "$(stamp) run.start out=$BENCH_OUT gpu=$gpu"
stage record  "$PY" bench/record.py "${REC[@]}"
stage latency "$PY" bench/latency.py "${LAT[@]}"
stage scores  "$PY" bench/scores.py "${SCO[@]}"
stage loop    "$PY" bench/loop.py "${LOOP[@]}"
echo "$(stamp) run.done report=$BENCH_OUT/report.md"
# Stand: 02.11.2026
