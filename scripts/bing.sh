#!/usr/bin/env bash
# Bing Webmaster Tools über die API steuern, ohne Weboberflaeche.
# Zugangsdaten kommen aus secrets/bing.env (nicht im Repo).
#
#   scripts/bing.sh sites            # Sites + Verifizierungsstatus
#   scripts/bing.sh feeds            # eingereichte Sitemaps
#   scripts/bing.sh submit-sitemap [url]
#   scripts/bing.sh submit <url>...  # einzelne URLs zur Indexierung
#   scripts/bing.sh quota            # verbleibendes Tageskontingent
#   scripts/bing.sh stats            # Klicks/Impressionen pro Tag
#   scripts/bing.sh keywords         # Suchanfragen mit Impressionen
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/secrets/bing.env"
[ -r "$ENV_FILE" ] || { echo "FEHLER: $ENV_FILE fehlt." >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a
: "${BING_API_KEY:?}" "${BING_SITE_URL:?}"

API="https://ssl.bing.com/webmaster/api.svc/json"
SITE_ENC="$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=""))' "$BING_SITE_URL")"

get()  { curl -s "$API/$1?apikey=$BING_API_KEY&siteUrl=$SITE_ENC"; }
post() { curl -s -X POST "$API/$1?apikey=$BING_API_KEY" \
              -H "Content-Type: application/json; charset=utf-8" --data "$2"; }

cmd="${1:-sites}"; shift || true
case "$cmd" in
  sites)          get GetUserSites            | python3 "$ROOT/scripts/bing_fmt.py" sites ;;
  feeds)          get GetFeeds                | python3 "$ROOT/scripts/bing_fmt.py" feeds ;;
  quota)          get GetUrlSubmissionQuota   | python3 "$ROOT/scripts/bing_fmt.py" quota ;;
  stats)          get GetRankAndTrafficStats  | python3 "$ROOT/scripts/bing_fmt.py" stats ;;
  keywords)       get GetQueryStats           | python3 "$ROOT/scripts/bing_fmt.py" keywords ;;
  submit-sitemap)
    url="${1:-${BING_SITE_URL%/}/sitemap.xml}"
    body="$(python3 -c 'import json,sys;print(json.dumps({"siteUrl":sys.argv[1],"feedUrl":sys.argv[2]}))' \
            "$BING_SITE_URL" "$url")"
    post SubmitFeed "$body" | python3 "$ROOT/scripts/bing_fmt.py" ok "Sitemap eingereicht: $url"
    ;;
  submit)
    [ $# -ge 1 ] || { echo "Aufruf: $0 submit <url>..." >&2; exit 1; }
    body="$(python3 -c 'import json,sys;print(json.dumps({"siteUrl":sys.argv[1],"urlList":sys.argv[2:]}))' \
            "$BING_SITE_URL" "$@")"
    post SubmitUrlBatch "$body" | python3 "$ROOT/scripts/bing_fmt.py" ok "$# URL(s) zur Indexierung angemeldet"
    ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
