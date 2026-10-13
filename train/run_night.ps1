# Nachtlauf (Windows, PowerShell): Daten vorbereiten, falls noch nicht da, dann trainieren.
#
#   powershell -ExecutionPolicy Bypass -File train\run_night.ps1          # volle Nacht
#   powershell -ExecutionPolicy Bypass -File train\run_night.ps1 -Quick   # Rauchtest
#
# Haelt den PC wach (SetThreadExecutionState), solange das Skript laeuft. Danach gilt
# wieder die normale Energieeinstellung. Den Deckel offen lassen oder in den
# Energieoptionen "Zuklappen: Nichts unternehmen" waehlen, siehe train/LAPTOP.md.
# Alles wird mitgeschrieben in train\logs\night-<Zeit>.log.
param([switch]$Quick)

$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
$py = if ($env:PYTHON) { $env:PYTHON } else { ".venv-train\Scripts\python.exe" }
New-Item -ItemType Directory -Force -Path train\logs | Out-Null
$log = "train\logs\night-$(Get-Date -Format yyyyMMdd-HHmm).log"
$env:PYTHONUTF8 = "1"; $env:PYTHONUNBUFFERED = "1"; $env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
$env:TOKENIZERS_PARALLELISM = "false"
$quickArg = @(); if ($Quick) { $quickArg = @("--quick") }

function Say($msg) {
  $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
  Write-Host $line
  Add-Content -Path $log -Value $line -Encoding UTF8
}

# Python aufrufen, Ausgabe auf den Bildschirm und ins Log. stderr (Fortschrittsbalken,
# Warnungen) als Text, nicht als PowerShell-Fehler. Gibt den Exit-Code zurueck.
function Run-Py([string[]]$pyArgs, [string]$copyTo = "") {
  & $py @pyArgs 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log -Append | Tee-Object -Variable lines | Out-Host
  $code = $LASTEXITCODE
  if ($copyTo) { $lines | Set-Content -Path $copyTo -Encoding UTF8 }
  return $code
}

# Wach halten: ES_CONTINUOUS | ES_SYSTEM_REQUIRED (Bildschirm darf aus).
Add-Type -Namespace Night -Name Power -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint SetThreadExecutionState(uint esFlags);
"@
[void][Night.Power]::SetThreadExecutionState([uint32]2147483649)

try {
  Say "Nachtlauf startet, Log: $log"
  if (-not (Test-Path $py)) { Say "Python nicht gefunden: $py (Einrichtung siehe train\LAPTOP.md)"; exit 1 }
  if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
  } else { Say "Warnung: nvidia-smi fehlt. Ohne GPU dauert das Training sehr lange." }
  [void](Run-Py @("-c", "import torch; print('CUDA verfuegbar:', torch.cuda.is_available())"))
  $drive = (Get-Location).Drive
  Say ("Freier Platz auf {0}: {1:N1} GB" -f $drive.Name, ($drive.Free / 1GB))

  # 1) Daten
  $needData = $true
  if (Test-Path train\data\manifest.json) {
    $m = Get-Content train\data\manifest.json -Raw | ConvertFrom-Json
    if ([bool]$m.quick -eq [bool]$Quick) { $needData = $false }
  }
  if ($needData) {
    Say "Schritt 1/2: Daten vorbereiten"
    foreach ($attempt in 1..3) {
      if ((Run-Py (@("train\prepare_data.py") + $quickArg)) -eq 0) { break }
      Say "prepare_data.py fehlgeschlagen (Versuch $attempt/3), neuer Versuch in 60 s (fertige Quellen bleiben)"
      Start-Sleep -Seconds 60
    }
    if (-not (Test-Path train\data\manifest.json)) { Say "Keine Daten, Abbruch."; exit 1 }
  } else { Say "Schritt 1/2: Daten schon vorhanden (train\data\manifest.json), uebersprungen" }

  # 2) Training mit Fortsetzen nach Absturz und kleinerem Batch bei Speichermangel.
  $name = if ($Quick) { "reranker-mminilm-quick" } else { "reranker-mminilm" }
  $batch = 64; $acc = 1
  foreach ($attempt in 1..4) {
    $extra = @()
    if (Get-ChildItem "train\checkpoints\$name\checkpoint-*" -ErrorAction SilentlyContinue) { $extra = @("--resume") }
    Say "Schritt 2/2: Training (Versuch $attempt, Batch $batch x $acc) $extra"
    $runlog = "train\logs\last-train-output.txt"
    $rc = Run-Py (@("train\train_reranker.py") + $quickArg + @("--batch", "$batch", "--grad-accum", "$acc") + $extra) $runlog
    if ($rc -eq 0) {
      Say "Fertig. Ergebnis:"
      [void](Run-Py (@("train\train_reranker.py") + $quickArg + @("--status")))
      exit 0
    }
    if (Select-String -Path $runlog -Pattern "out of memory|OutOfMemoryError" -Quiet) {
      if ($batch -le 16) { Say "Speicher reicht auch mit Batch 16 nicht, Abbruch."; exit 1 }
      $batch = $batch / 2; $acc = $acc * 2
      Say "GPU-Speicher voll: weiter mit Batch $batch und Gradient-Sammeln $acc"
      Remove-Item -Recurse -Force "train\checkpoints\$name" -ErrorAction SilentlyContinue
    } else {
      Say "Training abgebrochen (Code $rc), Fortsetzen in 60 s"
      Start-Sleep -Seconds 60
    }
  }
  Say "Nach 4 Versuchen nicht fertig. Log pruefen: $log"
  exit 1
}
finally {
  [void][Night.Power]::SetThreadExecutionState([uint32]2147483648)
}
