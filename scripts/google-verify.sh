#!/usr/bin/env bash
# Legt die HTML-Verifizierungsdatei fuer die Google Search Console an.
# Aufruf: scripts/google-verify.sh googleXXXXXXXXXXXX.html
#
# Das Token kommt aus der Search Console:
#   Property "dj-redoo.de" (URL-Praefix https://dj-redoo.de/)
#   -> Verifizierungsmethode "HTML-Datei" -> Dateiname kopieren
set -euo pipefail

[ $# -eq 1 ] || { echo "Aufruf: $0 googleXXXX.html" >&2; exit 1; }
name="$1"
case "$name" in
  google*.html) ;;
  *) echo "FEHLER: Dateiname muss google….html sein." >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
printf 'google-site-verification: %s\n' "$name" > "$ROOT/site/$name"
chmod o+r "$ROOT/site/$name"   # sonst liefert nginx 403
echo "Angelegt: site/$name"
echo
echo "Weiter:"
echo "  git add site/$name && git commit -m 'chore(seo): Search-Console-Verifizierung' && git push"
echo "  ssh djredoo 'cd /opt/dj-redoo && git fetch -q origin && git merge --ff-only -q origin/master'"
echo "  curl -s https://dj-redoo.de/$name        # muss das Token zeigen"
echo "  danach in der Search Console auf 'Bestaetigen' klicken"
