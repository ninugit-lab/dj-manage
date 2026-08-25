#!/usr/bin/env bash
# Setzt <lastmod> in site/sitemap.xml auf das letzte Commit-Datum der jeweiligen
# Datei. Google priorisiert das Crawling danach — veraltete Werte kosten
# Aktualität, erfundene (z. B. pauschal "heute") werden ignoriert.
#
#   scripts/sitemap.sh          # aktualisieren
#   scripts/sitemap.sh --check  # nur pruefen, Exit 1 bei Abweichung
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 - "${1:-}" <<'PY'
import re, subprocess, sys, urllib.parse
check = sys.argv[1] == "--check"
p = "site/sitemap.xml"
s = open(p, encoding="utf-8").read()

def datum(path):
    d = subprocess.run(["git", "log", "-1", "--format=%cs", "--", path],
                       capture_output=True, text=True).stdout.strip()
    return d or None

changed = []
def ersetze(m):
    loc, lm = m.group(2), m.group(4)
    pfad = urllib.parse.urlparse(loc).path
    datei = "site/index.html" if pfad in ("", "/") else "site" + pfad
    neu = datum(datei)
    if neu and neu != lm:
        changed.append((pfad or "/", lm, neu))
        return m.group(1) + loc + m.group(3) + neu + m.group(5)
    return m.group(0)

s = re.sub(r"(<loc>)(.*?)(</loc>\s*<lastmod>)(.*?)(</lastmod>)", ersetze, s, flags=re.S)

if not changed:
    print("sitemap.xml: lastmod aktuell.")
    sys.exit(0)
for pfad, alt, neu in changed:
    print(f"  {pfad:26} {alt} -> {neu}")
if check:
    print("sitemap.xml ist veraltet (scripts/sitemap.sh ausfuehren).", file=sys.stderr)
    sys.exit(1)
open(p, "w", encoding="utf-8").write(s)
print(f"sitemap.xml aktualisiert ({len(changed)} Eintraege).")
PY
