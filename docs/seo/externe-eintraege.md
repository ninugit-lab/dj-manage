# Externe Einträge — Anleitung mit fertigen Werten

**Stand:** 2026-08-25 · ergänzt `ai-seo-massnahmen-und-gbp.md` Teil B2/B4

Alles im Repo ist erledigt. Was hier steht, braucht Logins, die nur du hast.
Jeder Abschnitt enthält die exakten Werte zum Kopieren — nichts muss neu
ausgedacht werden.

---

## Der NAP-Block — überall exakt identisch

Zeichengenau übernehmen. Jede Abweichung (Abkürzung, andere PLZ, andere
Telefon-Schreibweise) zählt für Google als anderer Betrieb und verwässert
das lokale Ranking.

```
DJ Redoo
Zanderstraße 21
47058 Duisburg
Deutschland
0152 31751085
https://dj-redoo.de
```

| Feld | Wert |
|---|---|
| Kategorie | Diskjockey |
| Nebenkategorien | Hochzeitsdienstleister · Veranstaltungstechnik-Verleih · Musiker |
| Geokoordinaten | 51.4460464, 6.7927648 |
| Einzugsgebiet | 100 km um Duisburg |
| Preis ab | 660 € / 6 Stunden |
| Logo (quadratisch) | `site/images/logo-google-720.png` |
| Logo (breit) | `site/images/logo-google-wide.png` |
| Kurzbeschreibung | siehe unten |

**Kurzbeschreibung (unter 300 Zeichen, für Verzeichnisse mit Limit):**

> DJ Redoo aus Duisburg sorgt seit über 18 Jahren für die Musik auf Hochzeiten,
> Firmenfeiern, Geburtstagen und Vereinsfesten. Inklusive professioneller Ton-
> und Lichttechnik, Planung sowie Auf- und Abbau. Im Einsatz im Ruhrgebiet und
> am Niederrhein, rund 100 km um Duisburg.

Die lange Fassung (750 Zeichen) steht in `ai-seo-massnahmen-und-gbp.md`,
Teil C Schritt 6.

---

## 1. Bing Webmaster Tools

**Wichtigster Punkt von allen: ChatGPTs Suche läuft über den Bing-Index.**
Ohne Bing-Indexierung bleibt die Seite für ChatGPT unsichtbar.

1. `bing.com/webmasters` → mit Microsoft-Konto anmelden
2. **„Import aus Google Search Console"** — geht in einem Klick, sobald die
   Search Console eingerichtet ist (Abschnitt 3). Andernfalls Site manuell
   hinzufügen: `https://dj-redoo.de`
3. Verifizierung: die Methode **„XML-Datei"** oder **„Meta-Tag"** wählen —
   sag mir den Wert, dann lege ich die Datei bzw. das Tag an und deploye.
4. Sitemap einreichen: `https://dj-redoo.de/sitemap.xml`

### IndexNow ✅ läuft bereits

Neue und geänderte Seiten werden Bing sofort gemeldet — **ohne Konto**, die
Domain weist sich über die Key-Datei aus.

```bash
scripts/indexnow.sh                      # alle Seiten aus der sitemap.xml
scripts/indexnow.sh https://dj-redoo.de/faq.html   # gezielt einzelne
```

Key-Datei: `https://dj-redoo.de/d54d853abcf4a29dd70ed645c6c85773.txt`
**Nicht löschen und nicht umbenennen** — sonst weist IndexNow alle
Einreichungen zurück.

> Nach jeder inhaltlichen Änderung an einer Seite einmal laufen lassen.
> Das ersetzt kein Webmaster-Konto, beschleunigt die Indexierung aber
> erheblich.

---

## 2. Bing Places

Eigenständig neben den Webmaster Tools — das ist der Kartenzeiger, nicht der
Index.

1. `bingplaces.com` → „Neues Unternehmen hinzufügen"
2. NAP-Block von oben übernehmen
3. **Servicegebiet statt Ladenadresse** wählen (wie bei Google) — sonst
   erscheint die Betriebsadresse öffentlich auf der Karte
4. Kategorie `Disc Jockey`, Logo hochladen, Beschreibung einfügen

---

## 3. Google Search Console

1. `search.google.com/search-console` → Property hinzufügen
2. **Empfehlung: Typ „Domain"** (nicht URL-Präfix) — deckt alle Subdomains
   und beide Protokolle ab. Erfordert einen DNS-TXT-Record bei Cloudflare:
   DNS → Records → Add record → Typ `TXT`, Name `@`, Inhalt = der von Google
   angezeigte Wert. Proxy-Status ist bei TXT ohne Bedeutung.
3. Alternative ohne DNS-Zugriff: Typ „URL-Präfix" mit `https://dj-redoo.de/`
   und HTML-Datei-Verifizierung. Dafür gibt es ein Skript:

   ```bash
   scripts/google-verify.sh googleXXXXXXXXXXXX.html
   ```

   Es legt die Datei mit korrektem Inhalt und Leserecht an und nennt die
   Deploy-Schritte. Danach in der Console auf „Bestätigen" klicken.
4. Sitemap einreichen: `sitemap.xml`
5. Nach 3–5 Tagen unter „Seiten" die Abdeckung prüfen; einzelne URLs über
   „URL-Prüfung" → „Indexierung beantragen" nachschieben.

---

## 4. Apple Business Connect

Speist Apple Maps und Siri — relevant für jeden iPhone-Nutzer, der nach
einem DJ sucht.

1. `businessconnect.apple.com` → Apple-ID, dann „Unternehmen hinzufügen"
2. NAP-Block übernehmen, Typ **Dienstleistung vor Ort**
3. Verifizierung per Post oder Telefon
4. „Showcase" mit Logo und Fotos füllen

---

## 5. OpenStreetMap

Speist unzählige Karten-Apps und wird von AI-Modellen direkt gelesen.

**Aktueller Stand geprüft (2026-08-25):** Unter Zanderstraße 21 existiert nur
das Gebäude (`way/303736573`, `building=apartments`) — **kein Geschäftseintrag
für DJ Redoo**. Muss also neu angelegt werden.

1. Konto auf `openstreetmap.org` anlegen
2. Ort suchen, „Bearbeiten" → Punkt setzen auf `51.4460464, 6.7927648`
3. Objektart **„Tonstudio / Veranstaltungsdienstleister"**, dann diese Tags:

```
office=events_venue
name=DJ Redoo
addr:street=Zanderstraße
addr:housenumber=21
addr:postcode=47058
addr:city=Duisburg
phone=+49 152 31751085
website=https://dj-redoo.de
```

> Ehrlich bleiben: OSM ist eine Karte, kein Branchenbuch. Ein Eintrag ohne
> Ladenlokal ist grenzwertig und wird von der Community mitunter entfernt.
> Falls das passiert: nicht erneut anlegen, sondern akzeptieren — ein
> Editierstreit schadet mehr, als der Eintrag bringt.

---

## 6. Weitere Verzeichnisse

Nach Wirkung sortiert. Überall denselben NAP-Block verwenden.

| Verzeichnis | Warum | Aufwand |
|---|---|---|
| Das Örtliche | hohe Domain-Autorität, klassisches Zitat | 10 Min |
| Gelbe Seiten | dito | 10 Min |
| 11880.com | dito | 10 Min |
| Yelp DE | wird von Perplexity zitiert | 15 Min |
| Facebook-Seite | reines NAP-Zitat, keine Pflege nötig | 15 Min |
| eventpeppers / DJ-Portale | branchenspezifisch, oft mit Anfragen | je 15 Min |

---

## Nach jedem neuen Eintrag: `sameAs` erweitern

Jede neue Profil-URL gehört in das `sameAs`-Array beider LocalBusiness-Blöcke
(`site/index.html`, `site/ueber-uns.html`). Das ist der Mechanismus, über den
KI-Modelle die verstreuten Fundstücke als **eine** Firma erkennen.

Aktuell:

```json
"sameAs": [
  "https://maps.google.com/?cid=16718455517482973523"
]
```

**Schick mir die URLs, dann trage ich sie ein und deploye.**

---

## Reihenfolge

1. Search Console (Abschnitt 3) — schaltet den Bing-Import frei
2. Bing Webmaster per Import (Abschnitt 1)
3. Bing Places + Apple Business Connect (2, 4)
4. Die drei deutschen Verzeichnisse (6)
5. URLs an mich → `sameAs`
