# AI-SEO: Nächste Maßnahmen + Google-Business-Profil-Anleitung

**Stand:** 2026-08-24 · Domain: `dj-redoo.de` · App: `app.dj-redoo.de`
Ergänzt `docs/superpowers/specs/2026-08-04-ai-seo-strategie-design.md` (Abschnitte 1–4, 10, 11 sind umgesetzt).

---

# Teil A — Was bereits steht

| Bereich | Status |
|---|---|
| 8 statische Seiten, Titles/Descriptions/Canonicals/OG | ✅ |
| Schema.org: LocalBusiness, Service, FAQPage (per `@id` verknüpft) | ✅ |
| `robots.txt` + `sitemap.xml`, `/live` auf noindex | ✅ |
| Impressum + Datenschutz + Consent im Buchungsformular | ✅ |
| Testimonials, Bildergalerie, Dark-Premium-Design | ✅ |
| **Google Business Profile** | ❌ → Teil C |
| **Review-Funnel (APScheduler + `review_requested_at`)** | ❌ Spec 6/7 nicht implementiert |
| **Google Search Console / Bing Webmaster** | ❌ |
| **Verzeichniseinträge (NAP-Zitate)** | ❌ |

Die größten offenen Hebel sind **nicht** auf der Website — sie sind GBP + Reviews + NAP-Zitate. KI-Suchmaschinen und lokale Rankings ziehen genau daraus.

---

# Teil B0 — BLOCKER: Cloudflare sperrt alle AI-Crawler

**Gefunden am 2026-08-24. Wichtiger als alles andere in Teil B.**

Die `robots.txt` erlaubt GPTBot, ClaudeBot, PerplexityBot & Co. ausdruecklich —
aber sie kommen gar nicht erst bis nginx. Cloudflare blockt sie eine Ebene
davor mit `403 Your request was blocked.`

Messung (`curl -A "<UA>" https://dj-redoo.de/`):

| User-Agent | Status |
|---|---|
| Googlebot, bingbot, Applebot, normale Browser | 200 |
| **GPTBot, OAI-SearchBot** (ChatGPT) | **403** |
| **ClaudeBot** (Claude) | **403** |
| **PerplexityBot** (Perplexity) | **403** |
| **meta-externalagent** (Meta AI) | **403** |

Auch `/llms.txt` ist fuer diese Bots 403 — nur `/robots.txt` kommt durch.
Der komplette AI-SEO-Teil der Strategie ist damit **wirkungslos**: die
Modelle koennen die Seite nicht lesen, egal wie gut sie ausgezeichnet ist.

**Ursache:** Cloudflares "Block AI Scrapers and Crawlers" bzw. der
AI-Labyrinth/Bot-Fight-Mode. Fuer neu angelegte Zonen ist das seit 2025
teilweise standardmaessig aktiv — es wurde nicht bewusst eingeschaltet.

**Behebung (nur im Cloudflare-Dashboard moeglich, kein Repo-Change):**

1. Cloudflare Dashboard → Zone `dj-redoo.de` → **Security → Bots**
2. **"AI Scrapers and Crawlers"** auf *Off* / *Allow* stellen
   (ggf. heisst die Option "Block AI bots" oder liegt unter
   Security → Settings)
3. Unter **Security → WAF → Custom rules** pruefen, ob eine Regel auf
   `cf.verified_bot_category eq "AI Crawler"` o. ae. blockt → deaktivieren
4. **Security → Bots → Bot Fight Mode** ebenfalls pruefen: blockt
   pauschal nicht-verifizierte Bots
5. Danach verifizieren:

```bash
for ua in GPTBot/1.0 OAI-SearchBot/1.0 ClaudeBot/1.0 PerplexityBot/1.0 \
          meta-externalagent/1.1; do
  printf "%-24s " "$ua"
  curl -s -o /dev/null -w "%{http_code}\n" -A "$ua" https://dj-redoo.de/
done
```

Alle Zeilen muessen `200` zeigen. Erst danach greifen `llms.txt`,
Schema.org und der gesamte Rest der AI-Strategie.

> Hinweis: Cloudflare bietet unter "AI Audit" auch eine Pay-per-Crawl-
> Option. Fuer ein lokales Dienstleistungsgeschaeft ist das der falsche
> Hebel — Ziel ist maximale Sichtbarkeit in AI-Antworten, nicht die
> Monetarisierung von Crawls.

---

# Teil B — Was man noch machen kann

Sortiert nach Wirkung ÷ Aufwand. B1–B4 sind die Pflicht, der Rest optional.

## B1. Google Business Profile anlegen (höchste Priorität)

→ Komplette Anleitung in **Teil C**. Ohne GBP gibt es kein lokales Ranking, keine Map-Pack-Sichtbarkeit und keine echten Bewertungen — und AI-Antworten auf „DJ in Duisburg" speisen sich fast immer aus Google-Maps-Daten.

Nachgelagert im Code, sobald GBP live ist:
- `sameAs` in allen JSON-LD-Blöcken mit der GBP-URL füllen (aktuell `"sameAs": []`)
- `aggregateRating` ergänzen — **erst ab ≥ 5 echten Google-Bewertungen** und nur mit Werten, die exakt dem GBP entsprechen (falsche Werte = Rich-Snippet-Sperre)

## B2. Google Search Console + Bing Webmaster Tools

1. `search.google.com/search-console` → Property **Domain** `dj-redoo.de` (nicht URL-Präfix) anlegen
2. Verifizierung per DNS-TXT-Record — Cloudflare-DNS, Record hinzufügen, Proxy irrelevant bei TXT
3. Sitemap einreichen: `https://dj-redoo.de/sitemap.xml`
4. Nach 3–5 Tagen: Abdeckung prüfen, „URL-Prüfung" für jede Seite → Indexierung beantragen
5. `bing.com/webmasters` → Import aus Search Console (ein Klick). Wichtig, weil **ChatGPT-Suche über den Bing-Index läuft** — ohne Bing-Indexierung ist DJ Redoo für ChatGPT unsichtbar.

## B3. Review-Funnel implementieren (Spec Abschnitte 6/7)

Der einzige noch fehlende Code-Block der Original-Spec. Ohne ihn passiert nach dem Event nichts, und Bewertungen sind der stärkste Einzelfaktor im lokalen Ranking.

- `Event.review_requested_at` (Migration)
- `AppConfig.review_request_email_subject/_body/google_review_url`
- `send_review_requests()` + APScheduler in `AppConfig.ready()` mit `fcntl.flock`
- Trigger: Status `past`, 2 Tage nach Eventdatum, einmalig

Ziel-Kadenz: **jedes** durchgeführte Event bekommt eine Anfrage. Realistisch konvertieren 20–30 % → bei 30 Events/Jahr sind das 6–9 neue Bewertungen jährlich. Das reicht, um in Duisburg vorne zu liegen.

## B4. NAP-Zitate: Verzeichniseinträge mit identischen Daten

„NAP" = Name, Adresse, Telefon. Google gleicht diese Angaben quer über das Web ab — je konsistenter, desto höher das Vertrauen in den Standort. **Immer exakt identisch schreiben:**

```
DJ Redoo
Zanderstraße 21
47059 Duisburg          ← PLZ ergänzen, fehlt aktuell auf der Website!
0152 31751085
https://dj-redoo.de
```

Kostenlose Einträge, in dieser Reihenfolge:

| Verzeichnis | Warum |
|---|---|
| **Bing Places** | ChatGPT/Copilot-Quelle |
| **Apple Business Connect** | Apple Maps + Siri |
| **OpenStreetMap** | Speist unzählige Karten-Apps + wird von AI-Modellen gelesen |
| Das Örtliche / Gelbe Seiten / 11880 | Klassische deutsche Zitate, hohe Domain-Autorität |
| Yelp DE | Wird von Perplexity zitiert |
| Facebook-Seite | Ohne Pflege, nur als NAP-Zitat + `sameAs` |

Alle diese URLs anschließend ins `sameAs`-Array der JSON-LD aufnehmen — das ist der Mechanismus, über den eine KI die Entität „DJ Redoo" über Quellen hinweg als **eine** Firma erkennt.

## B5. `llms.txt` + AI-Crawler explizit erlauben

Aktuelle `robots.txt` erlaubt implizit alles. Explizit ist sicherer und signalisiert Absicht:

```
User-agent: GPTBot
User-agent: OAI-SearchBot
User-agent: ChatGPT-User
User-agent: PerplexityBot
User-agent: ClaudeBot
User-agent: Google-Extended
User-agent: Applebot-Extended
Allow: /
```

Zusätzlich `site/llms.txt` — ein Markdown-Steckbrief, den AI-Crawler zunehmend bevorzugt lesen: Firmenname, Leistungen, Preis ab 660 €, Einzugsgebiet, Kontakt, Links zu den Unterseiten. Fünf Minuten Aufwand, wachsender Effekt.

## B6. Schema.org ausbauen

Aktuell fehlen Felder, die AI-Antworten direkt zitieren:

- `geo` (Breiten-/Längengrad Zanderstraße 21) + `postalCode: "47059"`
- `openingHoursSpecification` bzw. besser: Umstellung auf **`areaServed` + `serviceArea`** — DJ Redoo ist ein Servicegebiet-Unternehmen ohne Ladenlokal
- `hasOfferCatalog` mit den drei Services und `priceSpecification` ab 660 € → lässt AI-Modelle den Preis direkt nennen
- `Person`-Schema für Thorsten mit `jobTitle`, `knowsAbout`, verknüpft per `founder` → stärkt die Entität
- `sitemap.xml`: `<lastmod>` pro URL ergänzen (fehlt komplett — Crawler priorisieren danach)

## B7. Content-Erweiterungen mit dem besten Verhältnis

- **FAQ ausbauen von 6 auf 15–20 Fragen.** Die FAQ-Seite ist die mit Abstand meistzitierte Quelle in AI-Overviews, weil Frage-Antwort-Paare 1:1 übernehmbar sind. Neue Fragen an echten Suchanfragen ausrichten: „Wie lange spielt ein DJ auf einer Hochzeit?", „Was kostet ein DJ für 100 Gäste?", „Braucht man für eine Hochzeit einen DJ oder eine Band?", „Wie früh muss man einen DJ buchen?", „Übernimmt der DJ auch die Moderation?"
- **Stadt-Landingpages** (in der Original-Spec out of scope, aber der nächstgrößte Hebel): je eine Seite für Düsseldorf, Essen, Oberhausen, Mülheim, Krefeld, Moers. Nur sinnvoll mit **echt unterschiedlichem Inhalt** — konkrete Locations der Stadt, gespielte Events dort, Anfahrtshinweis. Sechs kopierte Seiten mit ausgetauschtem Stadtnamen sind Doorway-Pages und werden abgestraft.
- **Referenz-Locations nennen.** Namen realer Hochzeitslocations, in denen DJ Redoo gespielt hat, auf `ueber-uns.html`. Extrem starkes lokales Signal, weil diese Locations selbst gesucht werden.
- **Bilder ersetzen.** Die aktuellen Bilder sind 300 px breit (Thumbnail-Auflösung). Echte Eventfotos in ≥ 1600 px, mit sprechenden Dateinamen (`dj-hochzeit-duisburg-2025.webp`), gefülltem `alt` und `width`/`height` gegen Layout-Shift.

## B8. Messung

- Search Console wöchentlich: welche Suchanfragen bringen Impressionen?
- GBP-Insights monatlich: Anrufe, Routen-Anfragen, Website-Klicks
- Monatlicher AI-Test mit festen Prompts in ChatGPT, Perplexity und Google AI Mode:
  „Empfiehl mir einen DJ in Duisburg für eine Hochzeit" · „Was kostet ein Hochzeits-DJ im Ruhrgebiet?" · „DJ mit eigener Lichttechnik Niederrhein"
  Ergebnis in eine Textdatei protokollieren — nur so wird der Fortschritt sichtbar.

---

# Teil C — Google Business Profil anlegen (Schritt für Schritt)

**Zeitaufwand:** 45 Min Einrichtung + 1–14 Tage Verifizierung.
**Voraussetzung:** Google-Konto. Empfehlung: ein dediziertes Geschäftskonto, **nicht** das private — sonst hängt das Profil später an einer privaten Identität und ist schwer übertragbar.

## Schritt 1 — Prüfen, ob das Profil schon existiert

Google legt für Firmen manchmal automatisch Einträge an (aus Verzeichnisdaten).

1. Google Maps öffnen, „DJ Redoo Duisburg" und „Zanderstraße 21 Duisburg" suchen
2. **Falls ein Eintrag existiert:** darauf klicken → „Als Inhaber eintragen" / „Inhaberschaft beanspruchen". Nicht neu anlegen — Duplikate schaden dem Ranking dauerhaft.
3. Falls nichts gefunden wird: weiter mit Schritt 2

## Schritt 2 — Profil erstellen

1. `business.google.com` → „Jetzt starten"
2. **Unternehmensname:** exakt `DJ Redoo`
   Kein Keyword-Stuffing („DJ Redoo — Hochzeits-DJ Duisburg"). Das ist ein Richtlinienverstoß und ein häufiger Sperrgrund. Der Name muss dem realen Auftritt entsprechen (Schilder, Rechnungen, Website).
3. **Unternehmenskategorie (Hauptkategorie):** `Diskjockey`
   Die Hauptkategorie ist der stärkste einzelne Ranking-Faktor im Map Pack. Nicht später ohne Grund ändern.

## Schritt 3 — Servicegebiet statt Ladenadresse ⚠️

Der wichtigste Schritt und der am häufigsten falsch gemachte.

1. Frage „Möchtest du einen Standort hinzufügen, den Kunden besuchen können?" → **Nein**
2. Grund: DJ Redoo ist ein Servicegebiet-Unternehmen (Dienstleistung beim Kunden), die Zanderstraße 21 ist die Betriebsadresse, kein Ladenlokal. Bei „Ja" erscheint die Privatadresse öffentlich auf Google Maps.
3. Danach: **Servicegebiet festlegen** — einzeln eintragen:
   `Duisburg`, `Düsseldorf`, `Essen`, `Oberhausen`, `Mülheim an der Ruhr`, `Krefeld`, `Moers`, `Dinslaken`, `Kleve`, `Wesel`, `Ratingen`, `Neuss`, `Bottrop`
   Maximal 20 Gebiete. Der Radius sollte nicht über ~2 Stunden Fahrzeit hinausgehen — zu große Gebiete verwässern das Ranking im Kerngebiet.
4. Die Adresse selbst trotzdem im Verifizierungsschritt angeben — sie wird dann nicht öffentlich angezeigt, dient aber der Standortbestimmung.

## Schritt 4 — Kontaktdaten

- **Telefon:** `0152 31751085` — muss unter dieser Nummer tatsächlich erreichbar sein, Google ruft bei manchen Verifizierungen an
- **Website:** `https://dj-redoo.de`
- Keine Weiterleitungsnummer und keine Trackingnummer verwenden; die Nummer muss der auf der Website entsprechen (NAP-Konsistenz, siehe B4)

## Schritt 5 — Verifizierung

Google wählt die Methode, meist bei Servicegebiets-Unternehmen:

- **Video-Verifizierung** (häufigster Fall, 1–5 Tage): Ein ununterbrochenes Video aufnehmen, das zeigt: (a) das Equipment — Mischpult, Boxen, Lichttechnik, (b) das Firmenschild/Fahrzeugbeschriftung falls vorhanden, (c) die Umgebung mit erkennbarem Straßenschild oder Hausnummer, (d) einen Beleg der Geschäftstätigkeit — Rechnung, Gewerbeanmeldung, Visitenkarten. In einem Take, ohne Schnitt. **Gewerbeanmeldung bereitlegen.**
- **Postkarte** (5–14 Tage): PIN kommt per Post an die Betriebsadresse, dann im Profil eingeben
- **Telefon/E-Mail:** nur in wenigen Fällen angeboten

Bei Ablehnung: Einspruch über den GBP-Support möglich. Häufigste Ablehnungsgründe sind Keyword im Namen und eine Adresse, die als Wohnadresse ohne Geschäftsnachweis erkannt wird — daher Schritt 2 und die Gewerbeanmeldung ernst nehmen.

## Schritt 6 — Profil vollständig ausfüllen (nach Freischaltung)

Vollständigkeit ist ein direkter Ranking-Faktor. Alles ausfüllen:

**Nebenkategorien** (bis zu 9, hier 3–4 setzen):
`Hochzeitsdienstleister` · `Veranstaltungstechnik-Verleih` · `Musiker` · `Eventagentur`

**Beschreibung** (750 Zeichen, natürlich formuliert, nicht Keyword-gestopft):
> DJ Redoo aus Duisburg sorgt seit über 18 Jahren für die richtige Musik auf Hochzeiten, Firmenfeiern, Geburtstagen, Jubiläen und Vereinsfesten. Thorsten ist Fachmann für Veranstaltungstechnik und Mitglied der DJ Allianz — neben dem DJ-Set kommen professionelle Ton- und Lichttechnik, Planung sowie Auf- und Abbau aus einer Hand. Gespielt wird Allround-Musik aus 50 Jahren, abgestimmt auf euren Gästekreis, von unter 50 bis über 200 Personen. Musikwünsche können Gäste vorab und live über die eigene Wunschlisten-App einreichen. Im Einsatz in Duisburg, Düsseldorf, Essen, Oberhausen, Mülheim, Krefeld und im Umkreis von rund 100 km. Auch Festinstallationen für Locations und Gastronomie.

**Dienstleistungen** (eigener Abschnitt, je mit Beschreibung — wird von AI-Suchen direkt ausgelesen):
Hochzeits-DJ · Firmenfeier-DJ · Geburtstags-DJ · Vereinsfest-DJ · Ton- und Lichttechnik-Verleih · Veranstaltungstechnik-Planung · Festinstallation Gastronomie

**Attribute:** „Inhabergeführt", Online-Terminvereinbarung, Zahlungsarten, Barrierefreiheit — alles anhaken, was zutrifft.

**Öffnungszeiten:** Erreichbarkeitszeiten für Anfragen eintragen (z. B. Mo–Fr 10–20 Uhr, Sa 10–16 Uhr). Ein Profil ganz ohne Zeiten wirkt unvollständig.

**Fotos — der unterschätzte Hebel.** Profile mit vielen Fotos bekommen deutlich mehr Anfragen. Mindestens:
- Logo (quadratisch, 720×720) — **liegt fertig vor:** `site/images/logo-google-720.png`.
  Motiv sitzt innerhalb des einbeschriebenen Kreises, weil Google Profillogos
  rund beschneidet. Breite Variante fürs Knowledge Panel: `logo-google-wide.png`
  (1447×400), Vektor-Quellen: `logo-square.svg` / `logo-wide.svg`
- Titelbild (Querformat, 1024×576) — am besten volle Tanzfläche
- 10–20 weitere: Equipment-Aufbau, Lichtshow im Dunkeln, Hochzeitslocation, Thorsten am Pult, Fahrzeug
- Monatlich 2–3 neue Fotos nachlegen — Aktivität ist ein Ranking-Signal
- Fotos vor dem Upload mit sprechenden Dateinamen versehen und, wenn möglich, mit GPS-Daten aus Duisburg

**Beiträge:** Alle 2–4 Wochen ein kurzer Post (Foto + 2 Sätze vom letzten Event, saisonale Angebote, freie Termine). Beiträge laufen nach 6 Monaten aus.

**Fragen & Antworten:** Selbst 5–8 Fragen einstellen und beantworten (aus einem zweiten Google-Konto fragen, vom Firmenkonto antworten — das ist ausdrücklich erlaubt). Diese Q&A-Paare landen direkt in AI-Antworten. Die FAQ von `dj-redoo.de/faq.html` hierher übernehmen.

**Buchungslink:** `https://dj-redoo.de/#buchen` bzw. das Buchungsformular direkt.

## Schritt 7 — Bewertungslink holen und im Code hinterlegen

1. GBP-Dashboard → „Rezensionen" → „Mehr Rezensionen erhalten" → Link kopieren
   (Format: `https://g.page/r/XXXXXXXX/review`)
2. Diesen Link später in `AppConfig.google_review_url` eintragen — er wird vom Review-Funnel (B3) in die Nachfass-Mails eingesetzt
3. QR-Code daraus generieren, ausdrucken, beim Event ans DJ-Pult stellen. Bewertungen am Abend selbst, in Feierlaune, haben die mit Abstand höchste Quote.
4. **Auf jede Bewertung innerhalb von 48 h antworten** — auch auf 5-Sterne, auch kurz. Antwortquote ist ein bestätigter Ranking-Faktor, und die Antworttexte sind indexierbarer Content mit Keywords.

## Schritt 8 — Profil-URL zurück in die Website

Sobald das Profil live ist, in `site/*.html` in jedem `LocalBusiness`-JSON-LD:

```json
"sameAs": [
  "https://www.google.com/maps/place/?q=place_id:DEINE_PLACE_ID"
]
```

Die Place ID über `developers.google.com/maps/documentation/places/web-service/place-id` ermitteln. Das ist die Verknüpfung zwischen Website-Entität und Google-Entität — ohne sie behandeln KI-Modelle beides als zwei unabhängige Fundstücke.

---

# Teil D — Reihenfolge

| Woche | Aufgabe |
|---|---|
| 1 | GBP anlegen + Verifizierung starten (Teil C 1–5) · Search Console + Bing (B2) |
| 1 | PLZ `47059` auf Website/Impressum/Schema ergänzen · `robots.txt` AI-Crawler · `llms.txt` · `lastmod` (B5/B6) |
| 2 | GBP vollständig ausfüllen inkl. Fotos und Q&A (Teil C 6) |
| 2–3 | Review-Funnel implementieren (B3) · Review-QR-Code fürs DJ-Pult |
| 3 | `sameAs` + Place ID einbauen (Teil C 8) · Verzeichniseinträge (B4) |
| 4+ | FAQ auf 15–20 Fragen · echte Eventfotos · später Stadt-Landingpages (B7) |
| laufend | Monatlich: GBP-Beitrag, 2–3 Fotos, AI-Testprompts protokollieren, Bewertungen beantworten |
