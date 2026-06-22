# Design & UX-Überarbeitung — DJ Wishlist

**Datum:** 2026-06-22
**Status:** Genehmigt, bereit für Implementierungsplan

## Ziel

Ganzheitliche Design- und UX-Überarbeitung beider App-Bereiche (Gäste-Seiten + DJ-Dashboard) mit einheitlichem Design-System. Designrichtung: **Evolution** — Marke (Dark, Glasmorphism, Bebas Neue / DM Sans / Space Mono, Blau/Lila-Accents) bleibt erkennbar, wird aber systematisiert und modernisiert.

**Prioritäten (alle vier):** Mobile-First · Accessibility (WCAG AA) · klarere Hierarchie · konsistentes Komponenten-System.

## Ausgangslage (Audit-Befunde)

Über alle Screens dieselben vier Wurzelprobleme:

1. **Kein zentrales CSS** — Styling inline in `<style>`-Blöcken + hunderte `style="..."`-Attribute pro Template. Tokens existieren nur in `dj_admin/base.html`; Gäste-Seiten haben eigene Token-Kopien.
2. **Mobile bricht systematisch** — 2-Spalten-Layouts (Live-Event, Event-Form, Workflow-Builder) auf 375px unbedienbar; Touch-Targets <44px; Tabellen nicht responsiv; Drag&Drop nicht touch-tauglich.
3. **Accessibility fehlt durchgängig** — keine ARIA-Tabs, Emoji-Buttons ohne Labels, `--muted`-Kontrast ~2.5:1 (WCAG braucht 4.5:1), keine `aria-live` für Polling-Updates (alle 5–10s), fehlende `for`/`id`-Label-Verknüpfung.
4. **Information-Overload** — Event-Form (3 Preis-Kalkulatoren gleichzeitig), Workflow-Builder, Live-Seite überladen.

## Technische Architektur

### CSS-Framework: Tailwind CSS via `django-tailwind`

- `django-tailwind` als `theme`-App, Tailwind v3, `tailwind.config.js` mit Custom-Tokens.
- `requirements.txt` += `django-tailwind`.

### Docker-Integration: Multi-Stage Build

Aktueller Stand: `python:3.12-slim` **ohne Node**, Code per Volume gemountet (`./app:/app`), `entrypoint.sh` macht migrate/collectstatic/clearsessions.

- **Build-Stage** (`node:20-slim`): `npm install` + `manage.py tailwind build` → erzeugt `theme/static/css/dist/styles.css`.
- **Runtime-Stage** (`python:3.12-slim`): kopiert nur das fertige CSS, **kein Node im Runtime-Image**.
- **Dev-Komfort:** optionales `tailwind`-Service in `docker-compose.yml` (`node:20-slim`, `tailwind start` Watch-Mode), nur lokal.
- **`entrypoint.sh`:** unverändert — gebautes CSS liegt vor `collectstatic` vor.

## Design-Tokens (`tailwind.config.js`)

Tokens via CSS-Variablen definiert (Light-Mode später ohne Umbau ergänzbar, aktuell **out of scope**).

- **Farben (semantisch):** `bg`, `surface`, `card`, `card-solid`, `border`; `accent` (Blau `#3b82f6`) + `accent2` (Lila `#a855f7`) als 50–900-Skalen für ableitbare Hover/Active/Glow; Status `success`/`warn`/`danger` als Skalen.
- **A11y-Kontrast-Fix:** `text-muted` mit ≥4.5:1 auf `bg` (statt `rgba(148,148,190,.7)`); zusätzlich `text-subtle` für unkritische Sekundärtexte.
- **Typo:** `font-display` (Bebas Neue), `font-sans` (DM Sans), `font-mono` (Space Mono); durchgängige `fontSize`-Skala statt Inline-Werte.
- **Spacing/Radius/Shadow/Z-Index:** Tailwind 4px-Spacing-Scale; `borderRadius` aus `--radius`/`--radius-sm`; Custom-`boxShadow` (`glass-sm/md/lg` + Status-Glows); Custom-`zIndex` (`dropdown/sticky/modal/toast`) statt hardcodierter Werte.
- **Glasmorphism:** `.glass`-Component-Layer (`@layer components`) bündelt `backdrop-blur` + `saturate` + `bg-card` + `border`.

## Komponenten-Bibliothek

Django-Template-Partials (`{% include %}` mit Parametern) + `@layer components`-Klassen. Jede Komponente an einer Quelle.

`templates/components/`:

- `card.html` / `.glass` — Glas-Karte (eine Quelle für Border/Blur/Radius/Shadow).
- `button.html` — Varianten `primary`/`purple`/`danger`/`ghost`, Größen, `aria-label`-Param, **min 44px Touch-Target**.
- `icon_button.html` — Emoji-Buttons (📊✓🚫✕↻) mit **Pflicht-`aria-label`** + `aria-hidden` aufs Emoji.
- `form_field.html` — Label+Input+Fehler als Einheit: erzwingt `for`/`id`, `aria-required`, `aria-invalid`/`aria-describedby`, Fehlertext-Slot.
- `tabs.html` — ARIA-konform (`role=tablist/tab/tabpanel`, `aria-selected`, Pfeiltasten-Navigation); ersetzt **alle** Ad-hoc-Tabs (Wishlist, Config, Dashboard).
- `stat_card.html`, `badge.html`, `toast.html` (mit `aria-live`).
- `data_table.html` — responsiv: Tabelle (Desktop) / Card-Stack (Handy, via `data-label`).
- `now_playing.html`, `wish_card.html` — als Partials (auf Gäste- *und* Admin-Seite genutzt), beseitigt Duplikate.
- `_polling.js` — zentrales Live-Update-Pattern, wrappt Updates in `aria-live="polite"`-Regionen.

## Seiten-UX-Umbauten

- **`base.html` (global):** Sidebar → off-canvas Drawer am Handy (Overlay + Backdrop + Fokus-Trap), Hamburger ≥44px; alle 2-Spalten-`grid` → `grid-cols-1 lg:grid-cols-2`.
- **Gäste-Wishlist (`index.html`):** Mobile-first; Suchfeld + Tabs prominent als primäre Aktion; Now-Playing visuell untergeordnet; Wish-Karten als Partial, Cover ≥44px, ganze Karte Touch-Target; Such-Ergebnisse `role=listbox`/`option` + `aria-live`.
- **Buchungsformular (`event_form.html`):** Echte Steps mit Progress-Indikator („Schritt 2 von 5"), eine Sektion pro Screen am Handy; sticky Preis-Zusammenfassung unten mit `aria-live`; Adress-Dropdown viewport-begrenzt; Fehlervalidierung via `form_field.html`.
- **DJ-Dashboard:** Stat-Cards `grid-cols-1 sm:grid-cols-3`; Event-Tabellen → `data_table.html`.
- **Live-Event (`wishlist_live.html`):** Am Handy **Tabs** statt 4 gestapelter Cards („Wünsche" Default / „Now Playing" / „Einstellungen" / „Blockieren"); Desktop-Triage: Now-Playing + Wünsche zuerst, Settings/Block einklappbar; Action-Buttons via `icon_button.html`.
- **Event-Form Admin:** 3 Preis-Kalkulatoren → Accordion/Tabs, nur einer sichtbar; klare Beschriftung „Manuell" vs. „Gäste-Staffelung (auto)"; Event/Kunde/Preis als 3 kollabierbare Sektionen mobil.
- **Workflow-Builder:** Drag&Drop bleibt (Maus) + **↑/↓-Buttons** pro Block (Tastatur/Touch-Alternative); Palette am Handy als Drawer; `flex-col` statt fixe 240px-Spalte.
- **Kalender:** FullCalendar `listWeek`-View als Default am Handy.

## Rollout (inkrementell, jede Phase lauffähig & deploybar)

- **Phase 0 — Fundament (blockierend):** Multi-Stage Dockerfile + Node-Build-Stage, `django-tailwind` + `theme`-App, `tailwind.config.js` mit allen Tokens, `@layer components`, dev-`tailwind`-Service. *Akzeptanz:* Container baut, Testseite rendert mit Tailwind.
- **Phase 1 — Komponenten-Bibliothek:** Alle Partials + `_polling.js`. *Akzeptanz:* Render-Tests, Pilot-Screen (Dashboard) umgestellt.
- **Phase 2 — Gäste-Seiten:** `base.html`-Drawer, `index.html`, `event_form.html` (Steps). *Akzeptanz:* 375px sauber, axe-A11y-Check, Touch-Targets.
- **Phase 3 — DJ-Dashboard & Live:** Dashboard, `wishlist_live.html` (Tabs-Overhaul), Kalender.
- **Phase 4 — Komplexe Admin-Seiten:** Event-Form (Accordion-Kalkulatoren), Workflow-Builder (↑/↓), Config (ARIA-Tabs).
- **Phase 5 — Politur & Verifikation:** Inline-Style-Residuen entfernen, durchgängiger A11y-Audit (Kontrast, aria-live, Tastatur), Mobile-QA über alle Screens.

Jede Phase: `@code-creator` baut → `@code-reviewer` prüft → `@test-runner` / manuelle Mobile-QA.

## Out of Scope (YAGNI)

- Light-Mode (Tokens sind aber vorbereitet).
- WebSockets statt Polling (Polling bleibt, nur a11y-gewrappt).
- Funktionale Änderungen an Preislogik/Spotify/Google-Integration — reine Design/UX-Arbeit.
