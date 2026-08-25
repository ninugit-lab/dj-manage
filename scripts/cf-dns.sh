#!/usr/bin/env bash
# DNS-Records der Zone dj-redoo.de über die Cloudflare-API verwalten.
# Zugangsdaten kommen aus secrets/cloudflare.env (nicht im Repo).
#
#   scripts/cf-dns.sh list
#   scripts/cf-dns.sh txt <name> <inhalt>     # anlegen oder aktualisieren
#   scripts/cf-dns.sh cname <name> <ziel>     # DNS-only, fuer Verifizierungen
#   scripts/cf-dns.sh unproxy <name>          # orange Wolke ausschalten
#   scripts/cf-dns.sh delete <name>
#
# Beispiel Search-Console-Verifizierung:
#   scripts/cf-dns.sh txt @ "google-site-verification=XXXXXXXXXXXX"
# Beispiel Bing-Verifizierung:
#   scripts/cf-dns.sh cname <von-Bing> verify.bing.com
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/secrets/cloudflare.env"
[ -r "$ENV_FILE" ] || { echo "FEHLER: $ENV_FILE fehlt." >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a
: "${CF_API_TOKEN:?}" "${CF_ZONE_ID:?}" "${CF_ZONE_NAME:?}"

API="https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records"
auth=(-H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json")

fqdn() { case "$1" in @|"$CF_ZONE_NAME") echo "$CF_ZONE_NAME";; *.*) echo "$1";; *) echo "$1.$CF_ZONE_NAME";; esac; }

# Gibt die Record-ID aus, oder nichts.
rec_id() {
  curl -s "${auth[@]}" "$API?name=$1${2:+&type=$2}" \
    | python3 -c "import json,sys;r=json.load(sys.stdin)['result'];print(r[0]['id'] if r else '')"
}

check() {
  python3 -c "
import json,sys
d=json.load(sys.stdin)
if not d['success']:
    print('FEHLER:', d.get('errors'), file=sys.stderr); sys.exit(1)
r=d.get('result') or {}
print('  ok:', r.get('name'), r.get('type'), '| proxied:', r.get('proxied'))"
}

cmd="${1:-list}"; shift || true
case "$cmd" in
  list)
    curl -s "${auth[@]}" "$API?per_page=100" | python3 -c "
import json,sys
for r in json.load(sys.stdin)['result']:
    print(f\"{r['type']:6} {r['name']:30} {str(r.get('proxied')):5} {r['content'][:55]}\")"
    ;;
  txt)
    [ $# -eq 2 ] || { echo "Aufruf: $0 txt <name> <inhalt>" >&2; exit 1; }
    name="$(fqdn "$1")"; content="$2"
    body="$(python3 -c "
import json,sys
print(json.dumps({'type':'TXT','name':sys.argv[1],'content':sys.argv[2],'ttl':300}))" "$name" "$content")"
    # Bei TXT kann es mehrere Records gleichen Namens geben (z. B. SPF).
    # Deshalb nur aktualisieren, wenn schon ein Record mit gleichem Praefix da ist.
    id="$(curl -s "${auth[@]}" "$API?type=TXT&name=$name" | python3 -c "
import json,sys
pre=sys.argv[1].split('=')[0]
print(next((r['id'] for r in json.load(sys.stdin)['result']
            if r['content'].strip('\"').startswith(pre)), ''))" "$content")"
    if [ -n "$id" ]; then
      echo "aktualisiere bestehenden TXT-Record …"
      curl -s -X PUT "${auth[@]}" --data "$body" "$API/$id" | check
    else
      echo "lege neuen TXT-Record an …"
      curl -s -X POST "${auth[@]}" --data "$body" "$API" | check
    fi
    echo "  Auflösung (kann kurz dauern):"
    sleep 5; dig +short TXT "$name" @1.1.1.1 | sed 's/^/    /'
    ;;
  cname)
    [ $# -eq 2 ] || { echo "Aufruf: $0 cname <name> <ziel>" >&2; exit 1; }
    name="$(fqdn "$1")"; target="$2"
    # Verifizierungs-CNAMEs muessen DNS-only sein: hinter dem Proxy liefert
    # Cloudflare seine eigenen Adressen aus, das Ziel ist dann nicht sichtbar.
    body="$(python3 -c "
import json,sys
print(json.dumps({'type':'CNAME','name':sys.argv[1],'content':sys.argv[2],
                  'ttl':300,'proxied':False}))" "$name" "$target")"
    id="$(rec_id "$name" CNAME)"
    if [ -n "$id" ]; then
      echo "aktualisiere bestehenden CNAME-Record …"
      curl -s -X PUT "${auth[@]}" --data "$body" "$API/$id" | check
    else
      echo "lege neuen CNAME-Record an …"
      curl -s -X POST "${auth[@]}" --data "$body" "$API" | check
    fi
    echo "  Auflösung (kann kurz dauern):"
    sleep 5; dig +short CNAME "$name" @1.1.1.1 | sed 's/^/    /'
    ;;
  unproxy)
    [ $# -eq 1 ] || { echo "Aufruf: $0 unproxy <name>" >&2; exit 1; }
    name="$(fqdn "$1")"; id="$(rec_id "$name")"
    [ -n "$id" ] || { echo "Kein Record namens $name." >&2; exit 1; }
    curl -s -X PATCH "${auth[@]}" --data '{"proxied":false}' "$API/$id" | check
    ;;
  delete)
    [ $# -eq 1 ] || { echo "Aufruf: $0 delete <name>" >&2; exit 1; }
    name="$(fqdn "$1")"; id="$(rec_id "$name")"
    [ -n "$id" ] || { echo "Kein Record namens $name." >&2; exit 1; }
    curl -s -X DELETE "${auth[@]}" "$API/$id" \
      | python3 -c "import json,sys;print('  geloescht:',json.load(sys.stdin)['success'])"
    ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
