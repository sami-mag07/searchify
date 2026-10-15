#!/bin/zsh
# Startet SearXNG oder den Such-Dienst lokal (fuer launchd, siehe deploy/launchd/).
# Secrets und Einstellungen kommen ausschliesslich aus der .env des Projekts.
set -euo pipefail
PROJECT="${0:A:h:h}"
cd "$PROJECT"

if [[ ! -r "$PROJECT/.env" ]]; then
  echo "$(date '+%F %T') .env fehlt in $PROJECT (siehe .env.example)" >&2
  exit 78  # launchd startet hoechstens alle 60 s neu (ThrottleInterval)
fi
# .env als Daten lesen, nicht als Shell-Code ausfuehren: Werte mit Leerzeichen
# oder $ bleiben woertlich, nur Zeilen KEY=WERT mit gueltigem Namen zaehlen.
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
  line="${line%$'\r'}"  # Windows-Zeilenende
  key="${line%%=*}"; value="${line#*=}"
  # Umschliessende Anfuehrungszeichen entfernen wie python-dotenv/Docker.
  if [[ "$value" == \"*\" || "$value" == \'*\' ]] && (( ${#value} >= 2 )); then
    value="${value[2,-2]}"
  fi
  [[ "$key" =~ '^[A-Za-z_][A-Za-z0-9_]*$' ]] && export "$key=$value"
done < "$PROJECT/.env"

case "${1:-}" in
  searxng)
    export SEARXNG_SETTINGS_PATH="$PROJECT/deploy/searxng/settings.yml"
    cd "${SEARXNG_DIR:-$HOME/tools/searxng}"
    exec .venv/bin/python -m searx.webapp
    ;;
  service)
    # Warten, bis SearXNG antwortet (nach dem Login starten beide gleichzeitig).
    for _ in {1..30}; do
      curl -s -o /dev/null "${SEARXNG_URL:-http://127.0.0.1:8888}/healthz" && break
      sleep 1
    done
    # Kein Access-Log: /jina/r/<url> und /jina/s/<frage> tragen Nutzerdaten im Pfad.
    exec "$PROJECT/.venv/bin/python" -m uvicorn app.main:app --host 127.0.0.1 \
      --port "${SERVICE_PORT:-8000}" --no-access-log
    ;;
  *)
    echo "Aufruf: run-local.sh searxng|service" >&2
    exit 64
    ;;
esac
