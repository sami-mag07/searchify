#!/usr/bin/env bash
# Run the passages benchmark on schinken (Windows laptop, RTX 3070 Ti) from the Mac.
#
#   bench/schinken/run.sh check      is schinken online, is WSL there, GPU?
#   bench/schinken/run.sh ship       copy code + questions to WSL ~/searchify-bench (keeps out/)
#   bench/schinken/run.sh setup      venv + "searchify[rerank]" in WSL, prints the GPU torch sees
#   bench/schinken/run.sh smoke      2 questions through every stage, in the foreground
#   bench/schinken/run.sh launch     the whole night as a detached Windows task (UNTIL=07:30)
#   bench/schinken/run.sh status     last log lines, running stage, GPU load
#   bench/schinken/run.sh fetch      report, best settings and logs back to bench/out-schinken/
#
# Windows-SSH kills background jobs when the connection drops, so launch starts a
# scheduled task and deletes it right after it started (the running instance keeps going;
# a leftover one-time trigger would fire again, see the 23:59 trap of 26.10.2026).
# Commands reach PowerShell base64-encoded and bash base64-encoded, so no quoting breaks.
set -euo pipefail
HOST="${SCHINKEN:-schinken}"
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
UNTIL="${UNTIL:-07:30}"

ps() {  # run a PowerShell script on schinken
  local enc
  enc=$(printf '%s' "$1" | iconv -f UTF-8 -t UTF-16LE | base64 | tr -d '\n')
  ssh -o ConnectTimeout=10 "$HOST" "powershell -NoProfile -NonInteractive -EncodedCommand $enc"
}

wsl() {  # run a bash script inside WSL on schinken
  local b64
  b64=$(printf '%s' "$1" | base64 | tr -d '\n')
  ps "wsl.exe -e bash -lc 'echo $b64 | base64 -d | bash'"
}

WINHOME='WINHOME=$(wslpath "$(cmd.exe /c "echo %USERPROFILE%" 2>/dev/null | tr -d "\r")")'

case "${1:-}" in
  check)
    ssh -o ConnectTimeout=8 "$HOST" whoami
    wsl 'echo "wsl $(uname -r)"; command -v uv || echo "no uv"; python3 --version; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || echo "no nvidia-smi"'
    ;;
  ship)
    tgz="$(mktemp -d)/searchify-bench.tgz"
    tar -C "$HERE" -czf "$tgz" --exclude '__pycache__' --exclude 'bench/out' --exclude 'bench/out-*' \
      src pyproject.toml README.md LICENSE bench
    scp -q "$tgz" "$HOST:searchify-bench.tgz"
    rm -f "$tgz"
    wsl "set -e; $WINHOME; mkdir -p ~/searchify-bench; tar -xzf \"\$WINHOME/searchify-bench.tgz\" -C ~/searchify-bench; chmod +x ~/searchify-bench/bench/*.sh; echo shipped to ~/searchify-bench"
    ;;
  setup)
    wsl 'set -e; cd ~/searchify-bench
if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null; then uv venv .venv --python 3.12; else python3 -m venv .venv; fi
fi
if command -v uv >/dev/null; then uv pip install --python .venv/bin/python -q -e ".[rerank]"
else .venv/bin/pip install -q -e ".[rerank]"; fi
.venv/bin/python -c "import torch, searchify; print(\"torch\", torch.__version__, \"cuda\", torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"\")"'
    ;;
  smoke)
    wsl 'cd ~/searchify-bench && rm -rf bench/out-smoke && SMOKE=1 bench/run_all.sh 2>&1 | grep -vE "Loading weights|HF_TOKEN" | tail -25'
    ;;
  launch)
    ps "\$a = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument '-e bash -lc \"cd ~/searchify-bench && mkdir -p bench/out && UNTIL=$UNTIL bench/run_all.sh >> bench/out/run.log 2>&1\"'
\$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 16)
Register-ScheduledTask -TaskName SearchifyBench -Action \$a -Settings \$s -Force | Out-Null
Start-ScheduledTask -TaskName SearchifyBench
Start-Sleep -Seconds 8
Unregister-ScheduledTask -TaskName SearchifyBench -Confirm:\$false
'launched, task removed'"
    sleep 20
    "$0" status
    ;;
  status)
    wsl 'tail -n 15 ~/searchify-bench/bench/out/run.log 2>/dev/null || echo "no log yet"; echo; p=$(pgrep -af "bench/(record|latency|scores|loop)\.py" | cut -c1-120); echo "${p:-no stage running}"; nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader 2>/dev/null'
    ;;
  fetch)
    wsl "set -e; $WINHOME; cd ~/searchify-bench/bench/out; tar -czf \"\$WINHOME/searchify-bench-out.tgz\" \$(ls report.md best.json latency.json loop.jsonl run.log 2>/dev/null)"
    mkdir -p "$HERE/bench/out-schinken"
    scp -q "$HOST:searchify-bench-out.tgz" "$HERE/bench/out-schinken/out.tgz"
    tar -xzf "$HERE/bench/out-schinken/out.tgz" -C "$HERE/bench/out-schinken" && rm "$HERE/bench/out-schinken/out.tgz"
    ls -la "$HERE/bench/out-schinken"
    ;;
  *)
    sed -n '2,12p' "$0"; exit 1 ;;
esac
# Stand: 02.11.2026
