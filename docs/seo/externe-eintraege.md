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

## 1. Bing Webmaster Tools ✅ erledigt

**Wichtigster Punkt von allen: ChatGPTs Suche läuft über den Bing-Index.**
Ohne Bing-Indexierung bleibt die Seite für ChatGPT unsichtbar.

Stand 2026-08-25: Site verifiziert (Import aus der Search Console),
zusätzlich eigener DNS-Nachweis gesetzt, Sitemap eingereicht und von Bing
gecrawlt (`Status: Success`, 8 URLs), alle 8 URLs zur Indexierung angemeldet.

Verwaltet wird das ohne Weboberfläche über `scripts/bing.sh` — Schlüssel in
`secrets/bing.env`, Details in `DEPLOY.md`:

```bash
scripts/bing.sh sites      # Verifizierungsstatus
scripts/bing.sh feeds      # Sitemap-Status und Crawl-Zeitpunkt
scripts/bing.sh keywords   # welche Suchanfragen Impressionen bringen
```

Auswertungsdaten erscheinen erst 24–48 Stunden nach der Verifizierung.

> Der CNAME `a7b13f9511633a1a25fa30746f95c454` → `verify.bing.com` muss
> stehen bleiben. Er ist bewusst zusätzlich zum Import gesetzt: eine
> importierte Verifizierung hängt am Google-Konto und verfällt, wenn Bing
> der Zugriff darauf entzogen wird.

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
Index. Speist Bing Maps und **Microsoft Copilot**, das in Windows und Edge
mitgeliefert wird. Deutlich weniger Betriebe pflegen ihren Bing-Eintrag als
den bei Google, entsprechend leichter fällt hier die Sichtbarkeit.

**Zeitaufwand:** 10 Minuten bei Import, 20 Minuten manuell.

### Der schnelle Weg: aus dem Google-Profil importieren

1. `bingplaces.com` → mit Microsoft-Konto anmelden
2. Nach `DJ Redoo` + `Duisburg` suchen. Erscheint ein Eintrag: **beanspruchen**,
   nicht neu anlegen. Zwei Einträge desselben Betriebs schaden dauerhaft.
3. **„Import from Google Business Profile"** wählen. Bing fragt Leserechte auf
   das Google-Profil ab und übernimmt Name, Adresse, Telefon, Kategorien,
   Beschreibung, Zeiten und Fotos in einem Zug. Am Google-Profil ändert sich
   dabei nichts.
4. Optional die laufende Synchronisation aktivieren — dann folgt Bing künftigen
   Änderungen am Google-Profil automatisch.

> **Import nur, wenn das Google-Profil sauber ist.** Bing kopiert Fehler
> unbesehen mit; aus einer Baustelle werden dann zwei. Da unser GBP frisch und
> geprüft ist, ist der Import hier der richtige Weg.

### Manuell, falls der Import nicht angeboten wird

1. „Add a new business" → Geschäftstyp **Servicegebiet / Service Area Business**
2. NAP-Block von oben zeichengenau übernehmen
3. **Adresse ausblenden** ankreuzen — sonst steht die Betriebsadresse öffentlich
   auf der Karte. Stattdessen die Servicegebiete eintragen (Städteliste unten).
4. Kategorie `Disc Jockey`, Logo `site/images/logo-google-720.png`,
   Kurzbeschreibung von oben

### Verifizierung

Per Telefon oder E-Mail, meist sofort. Danach dauert es einige Tage, bis der
Eintrag in Bing Maps erscheint.

---

## 3. Google Search Console ✅ verifiziert

**Stand 2026-08-25:** Property `https://dj-redoo.de/` (URL-Präfix) ist per
HTML-Datei verifiziert, Sitemap eingereicht. Die Verifizierungsdatei
`site/google8c4ed904e5aa0401.html` **muss liegen bleiben** — Google prüft sie
periodisch nach, beim Löschen verfällt die Property.

Nach 3–5 Tagen unter „Seiten" die Abdeckung prüfen; einzelne URLs über
„URL-Prüfung" → „Indexierung beantragen" nachschieben.

<details>
<summary>Falls später die Domain-Property (alle Subdomains) dazukommen soll</summary>

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

</details>

### `lastmod` pflegen

Google priorisiert das Crawling nach `<lastmod>`. Veraltete Werte kosten
Aktualität, pauschal auf „heute" gesetzte werden ignoriert. Deshalb kommen
die Werte aus dem Git-Log:

```bash
scripts/sitemap.sh            # aus dem letzten Commit-Datum je Datei setzen
scripts/sitemap.sh --check    # nur pruefen, Exit 1 bei Abweichung
```

Nach jeder inhaltlichen Änderung ausführen, dann `scripts/indexnow.sh`.

---

## 4. Apple Business Connect

Speist Apple Maps und Siri — relevant für jeden iPhone-Nutzer, der nach einem
DJ sucht. Der Dienst heißt inzwischen schlicht **Apple Business**; bestehende
Daten wurden übernommen.

**Zeitaufwand:** 20 Minuten, dann bis zu 5 Werktage bis zur Freischaltung.

> **Wichtig, weil ältere Anleitungen das Gegenteil sagen:** Apple hat lange nur
> Betriebe mit Ladenlokal zugelassen und Dienstleister abgelehnt. Das ist
> geändert — Servicegebiets-Betriebe können sich eintragen, ohne die
> Privatadresse öffentlich zu zeigen. Wer noch einen Forenbeitrag von 2024
> findet, der das verneint, liest veraltete Auskunft.

1. `businessconnect.apple.com` → mit Apple-ID anmelden. Wenn möglich eine
   geschäftliche verwenden, nicht die private — das Profil hängt sonst dauerhaft
   an einer Privatperson.
2. Nach `DJ Redoo` suchen. Vorhandenen Eintrag beanspruchen, sonst neu anlegen.
3. Beim Anlegen: Name, Website, Adresse **und der bürgerliche Name der Person,
   die den Eintrag anmeldet** — Apple fragt das ab, Google nicht.
4. Typ **Servicegebiet** wählen, Adresse ausblenden, Städteliste eintragen.
5. NAP-Block zeichengenau übernehmen, Logo und Fotos ergänzen.

### Verifizierung — hier ist Apple strenger als Google

- **Zwei** Verifizierungsmethoden aus Apples Liste sind nötig, nicht eine.
- Ab Anmeldung bleiben **10 Tage** Zeit. Wer die Frist verstreichen lässt,
  fängt von vorn an — also erst anmelden, wenn eine ruhige halbe Stunde da ist.
- Apple kann Nachweise verlangen, dass du den Betrieb vertrittst. **Gewerbe-
  anmeldung bereitlegen.** Name, Adresse, Telefon und Website müssen dort
  identisch zur Website stehen; jede Abweichung verzögert oder löst eine
  erneute Prüfung aus. Genau dafür ist der NAP-Block oben da.

### Servicegebiete (für beide Dienste, gleiche Liste wie bei Google)

```
Duisburg · Düsseldorf · Essen · Oberhausen · Mülheim an der Ruhr · Krefeld
Moers · Dinslaken · Kleve · Wesel · Ratingen · Neuss · Bottrop
```

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

1. ~~Search Console (Abschnitt 3)~~ ✅ verifiziert, Sitemap eingereicht
2. ~~Bing Webmaster (Abschnitt 1)~~ ✅ verifiziert, Sitemap eingereicht
3. Bing Places + Apple Business Connect (2, 4) ← **als Nächstes**
4. Die drei deutschen Verzeichnisse (6)
5. URLs an mich → `sameAs`
