# AI-SEO-Strategie für dj-redoo.de

**Datum:** 2026-08-04
**Status:** Design approved, bereit für Implementierungsplan

## Ziel

Sichtbarkeit für DJ Redoo bei KI-Suchmaschinen (ChatGPT, Perplexity, Google AI Overviews) und klassischem lokalem SEO erreichen, sodass Anfragen wie "empfiehl mir einen DJ in Duisburg" DJ Redoo nennen.

## Kontext

- Zielgruppe: gemischt (Hochzeiten, Firmenevents, Partys)
- Region: ~100 km Radius um Duisburg (Ruhrgebiet/Niederrhein: Duisburg, Düsseldorf, Essen, Oberhausen, Mülheim, Krefeld u.a.)
- Ressourcen: nur Rene, nebenbei — Strategie muss schlank und wartungsarm sein
- Ausgangslage: `dj-redoo.de` (statische Site in `site/`) ist aktuell eine Ein-Seiten-Baustellenseite ohne Content, `robots.txt` oder `sitemap.xml`. Die eigentliche App liegt unter `app.dj-redoo.de` (Wishlist/Buchung).
- Google Business Profile existiert bereits für DJ Redoo.

## Gewählter Ansatz

Schlanker Hybrid aus (1) kleiner, strukturierter statischer Website mit Schema.org-Markup und (2) aktivem Review-Funnel über Google Business Profile. KI-Suchmaschinen ziehen lokale Empfehlungen primär aus strukturierten Daten und externer Reputation (Reviews), nicht aus Content-Masse — das passt zu begrenzten Content-Ressourcen.

## 1. Website-Struktur (statisch, `site/`)

Ausgeliefert von nginx unter `dj-redoo.de`, kein Django nötig, kein Build-System (analog bestehendem `site/index.html` + `site/css/styles.css`).

Seiten:
- `index.html` — Startseite: Value Proposition, Leistungsüberblick, Trust-Signale (Bewertungen, Event-Anzahl)
- `hochzeit.html` — Service-Seite Hochzeits-DJing
- `firmenfeier.html` — Service-Seite Firmenevents/Corporate
- `party.html` — Service-Seite Club/Party
- `faq.html` — Häufige Fragen (wichtigste Seite für AI-Suchmaschinen — FAQ-Content wird direkt in AI-Overview-Antworten übernommen)
- `ueber-uns.html` — Profil, Erfahrung, Einzugsgebiet (Städteliste im 100-km-Radius)

Jede Service-/CTA-Seite verlinkt auf `/buchen/` (Buchungsformular in der Django-App unter `app.dj-redoo.de`).

## 2. Technisches SEO-Fundament

- `site/robots.txt`: erlaubt vollständiges Crawling, verweist auf Sitemap
- `site/sitemap.xml`: statisch gepflegt (6 Seiten, kein Generator nötig)
- nginx (`nginx/conf.d/djredoo.conf`): neue `location`-Blöcke für `/robots.txt` und `/sitemap.xml` mit kurzem Cache; bestehende Security-Header (CSP, HSTS etc.) bleiben unverändert
- Meta-Tags pro Seite: individuelle Title/Description mit Stadt+Leistung (z. B. "DJ für Hochzeit in Duisburg & Umgebung"), OpenGraph-Tags für Social-Previews
- Reines HTML/CSS ohne JS-Dependencies — Ladezeit als Ranking-/Crawl-Faktor

## 3. Schema.org / Structured Data

Zentral für AI-Sichtbarkeit — Hauptmechanismus, über den KI-Suchmaschinen Inhalte direkt zitieren/strukturiert auslesen.

- **`LocalBusiness`-JSON-LD** auf jeder Seite: Name, Telefon, `areaServed` (Städteliste 100-km-Radius), `aggregateRating` (aus GBP sobald verfügbar), `sameAs` → Google Business Profile
- **`FAQPage`-Schema** auf `faq.html`: Frage/Antwort-Paare als strukturierte Daten
- **`Service`-Schema** auf den Service-Seiten: `serviceType`, `areaServed`, `provider` → Verweis auf LocalBusiness-Entity

## 4. Live-QR-Seite (nicht indexiert)

Separate statische Seite `site/live.html` unter `dj-redoo.de/live` für den Live-Einsatz am Abend (per QR-Code auf Tablet/Smartphone aufgerufen) — bewusst **nicht** Teil der SEO-Sichtbarkeit, sondern reine Bedienoberfläche.

- Bindet die bestehende öffentliche Wishlist (`app.dj-redoo.de/`) per `<iframe>` ein — zeigt automatisch das aktive Event (`Event.is_active=True`), kein Event-Parameter nötig
- Minimalistisches Layout: Vollbild-iframe, kein Header/Footer/Navigation
- iframe-Einbettung funktioniert ohne CSP-Änderung — `nginx/conf.d/djredoo.conf:60` setzt für die öffentlichen Routen von `app.dj-redoo.de` bereits `frame-ancestors *`

**Nicht auffindbar/indexiert:**
- `<meta name="robots" content="noindex, nofollow">` im `<head>` von `live.html`
- `Disallow: /live` in `site/robots.txt`
- Nicht in `sitemap.xml` aufgenommen

QR-Code selbst wird extern generiert (z. B. beim Event-Aufbau) und zeigt auf `dj-redoo.de/live` — kein QR-Generator im Projekt.

## 5. Google Business Profile Optimierung (manuelle Checkliste)

Kein Code, Aufgaben für Rene:
- Kategorien prüfen (Hauptkategorie "DJ", Nebenkategorien Hochzeit/Firmenevent)
- Alle Attribute ausfüllen, Fotos aktuell halten
- Servicegebiet auf 100-km-Radius um Duisburg setzen
- GBP-Link als `sameAs` in Website-Schema einbinden

## 6. Review-Funnel: Trigger-Mechanismus

Kein Scheduler im Projekt vorhanden (Status `Event.status` wechselt aktuell nur lazy beim nächsten `save()`, siehe `wishlist/models.py:110-118`). Gewählt: **In-Process-Scheduler (APScheduler)**.

- Registrierung beim Django-Start (`AppConfig.ready()`), läuft im bestehenden Gunicorn-Prozess — kein neuer Container, kein Host-Cron, keine Docker-Socket-Freigabe
- Täglicher Lauf (z. B. 10:00 Uhr) ruft `send_review_requests()` auf
- Da mehrere Gunicorn-Worker laufen können: Scheduler-Start nur in einem Worker via non-blocking Dateilock (`fcntl.flock` auf `/tmp/scheduler.lock`)

## 7. Review-Funnel: Ablauflogik

**Neues Feld:** `Event.review_requested_at` (DateTimeField, nullable) — verhindert Doppelversand unabhängig vom Scheduler-Lock.

**Neue `AppConfig`-Felder** (analog bestehendem `confirmation_email_subject/body`-Pattern):
- `review_request_email_subject`, `review_request_email_body` — editierbar im Admin
- `google_review_url` — direkter "Rezension schreiben"-Link aus dem Google Business Profile

**`send_review_requests()`** (Funktion, täglich per Scheduler ausgeführt):
1. Query: `Event.objects.filter(status=PAST, review_requested_at__isnull=True, date__lte=today - 2 Tage)`
2. Nur Events, die den Status `confirmed → past` durchlaufen haben (keine `inquiry`/`cancelled`-Events)
3. Pro Event: E-Mail via bestehendem `GoogleService.send_email()` an `event.customer_email`, Text aus `AppConfig`-Template mit Platzhalter für `google_review_url`
4. Bei Erfolg: `EmailLog`-Eintrag (bestehendes Model, keine Schemaänderung) + `event.review_requested_at = now()`
5. Bei Fehler (z. B. Gmail-API/Token-Problem): `EmailLog.success=False` + Fehlermeldung, `review_requested_at` bleibt leer → nächster Lauf versucht erneut

**Timing:** 2 Tage Abstand nach Eventdatum, einmalige Anfrage, kein Opt-out-Mechanismus nötig (Bestandskundenkontext, berechtigtes Interesse) — Text macht deutlich, dass es eine einmalige Bitte ist.

## 8. Erfolgsmessung & Monitoring

Kein neues Analytics-Tool (würde CSP-Lockerung + Cookie-Banner-Pflicht nach sich ziehen — unverhältnismäßig für den Umfang):
- Google Search Console für `dj-redoo.de` einrichten
- `EmailLog` liefert Versand-/Erfolgsmetrik für Review-Requests
- Manuelle Stichprobe alle paar Wochen: Testfragen in ChatGPT/Perplexity stellen und Sichtbarkeit prüfen

## 9. Umsetzungsreihenfolge

1. Statische Seiten (inkl. `live.html`) + Schema.org + `robots.txt`/`sitemap.xml` (unabhängig, sofort startbar)
2. GBP-Checkliste (Rene manuell, parallel möglich)
3. `Event.review_requested_at` + neue `AppConfig`-Felder (Migration)
4. APScheduler-Integration + `send_review_requests()`
5. Google Search Console einrichten

## Out of Scope

- Google Analytics / Tag Manager
- Content-Blog / regelmäßige Artikel (zu wartungsintensiv für "nur Rene, nebenbei")
- Landingpages pro Stadt (Skalierung auf viele Seiten — spätere Erweiterung, nicht Teil dieser Iteration)
- Bezahlte Verzeichniseinträge/Portale (z. B. Hochzeitsportale) — separate Entscheidung
