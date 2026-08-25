"""Antworten der Bing-Webmaster-API lesbar ausgeben.

Die API meldet Fehler mit HTTP 200 und ErrorCode im Body — deshalb laeuft
jede Antwort durch fail(), sonst wirken Fehlschlaege wie Erfolge.
"""
import datetime
import json
import re
import sys


def when(value):
    """/Date(1787649436000)/ -> 25.08.2026 09:17"""
    m = re.match(r"/Date\((\d+)", str(value))
    if not m:
        return str(value)
    ts = datetime.datetime.fromtimestamp(int(m.group(1)) / 1000)
    return ts.strftime("%d.%m.%Y %H:%M")


def load():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except ValueError:
        sys.exit(f"Unerwartete Antwort (kein JSON): {raw[:200]}")
    if isinstance(data, dict) and data.get("ErrorCode"):
        sys.exit(f"FEHLER {data['ErrorCode']}: {data.get('Message')}")
    return data.get("d")


LEER = "Noch keine Daten. Nach der Verifizierung dauert es bis zu 48 Stunden."

mode = sys.argv[1]
d = load()

if mode == "ok":
    print(sys.argv[2])

elif mode == "sites":
    for s in d:
        print(s["Url"])
        print(f"  verifiziert: {'ja' if s['IsVerified'] else 'NEIN'}")
        print(f"  DNS-Kennung: {s['DnsVerificationCode']}")

elif mode == "feeds":
    if not d:
        sys.exit("Keine Sitemap eingereicht.")
    for f in d:
        print(f["Url"])
        print(f"  Status: {f['Status']} | URLs: {f['UrlCount']}")
        print(f"  eingereicht: {when(f['Submitted'])} | gecrawlt: {when(f['LastCrawled'])}")

elif mode == "quota":
    print(f"heute noch: {d['DailyQuota']} URL(s) | diesen Monat: {d['MonthlyQuota']}")

elif mode == "stats":
    if not d:
        sys.exit(LEER)
    print(f"{'Datum':12}{'Klicks':>8}{'Impress.':>10}")
    for x in d[-14:]:
        print(f"{when(x['Date'])[:10]:12}{x['Clicks']:>8}{x['Impressions']:>10}")

elif mode == "keywords":
    if not d:
        sys.exit(LEER)
    d.sort(key=lambda x: -x["Impressions"])
    print(f"{'Suchanfrage':42}{'Impress.':>9}{'Klicks':>8}{'Pos.':>7}")
    for x in d[:25]:
        print(f"{x['Query'][:40]:42}{x['Impressions']:>9}"
              f"{x['Clicks']:>8}{x['AvgImpressionPosition']:>7.1f}")

else:
    sys.exit(f"Unbekannter Modus: {mode}")
