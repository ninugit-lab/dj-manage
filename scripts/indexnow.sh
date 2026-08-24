#!/usr/bin/env bash
# Meldet die Seiten aus site/sitemap.xml per IndexNow an Bing, Yandex & Co.
# Kein Account nötig — die Domain weist sich über die Key-Datei aus.
# Aufruf: scripts/indexnow.sh [nur-diese-url ...]
set -euo pipefail

KEY="d54d853abcf4a29dd70ed645c6c85773"
HOST="dj-redoo.de"
KEY_URL="https://${HOST}/${KEY}.txt"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Key-Datei muss erreichbar sein, sonst weist IndexNow alles zurück.
remote_key="$(curl -fsS "$KEY_URL" || true)"
if [ "$remote_key" != "$KEY" ]; then
  echo "FEHLER: $KEY_URL liefert nicht den Key (bekommen: '${remote_key:0:40}')" >&2
  exit 1
fi

if [ $# -gt 0 ]; then
  urls=("$@")
else
  mapfile -t urls < <(grep -oE '<loc>[^<]+</loc>' "$ROOT/site/sitemap.xml" \
                      | sed -E 's#</?loc>##g')
fi
[ ${#urls[@]} -gt 0 ] || { echo "Keine URLs gefunden." >&2; exit 1; }

payload="$(python3 -c "
import json,sys
print(json.dumps({
  'host': sys.argv[1],
  'key': sys.argv[2],
  'keyLocation': sys.argv[3],
  'urlList': sys.argv[4:],
}))" "$HOST" "$KEY" "$KEY_URL" "${urls[@]}")"

echo "Melde ${#urls[@]} URL(s) an IndexNow ..."
code="$(curl -s -o /tmp/indexnow-response -w '%{http_code}' \
  -X POST "https://api.indexnow.org/IndexNow" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data "$payload")"

case "$code" in
  200|202) echo "OK ($code) — angenommen." ;;
  400) echo "FEHLER 400: ungueltiges Format." >&2; cat /tmp/indexnow-response >&2; exit 1 ;;
  403) echo "FEHLER 403: Key nicht akzeptiert." >&2; exit 1 ;;
  422) echo "FEHLER 422: URLs passen nicht zum Host." >&2; exit 1 ;;
  429) echo "FEHLER 429: zu viele Anfragen — spaeter erneut." >&2; exit 1 ;;
  *)   echo "Unerwarteter Status $code" >&2; cat /tmp/indexnow-response >&2; exit 1 ;;
esac
