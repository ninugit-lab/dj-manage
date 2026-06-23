# Design & UX-Überarbeitung Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tailwind-basiertes, mobil-taugliches, WCAG-AA-konformes Design-System über alle Gäste- und DJ-Admin-Seiten ausrollen, ohne funktionale Logik zu ändern.

**Architecture:** `django-tailwind` (`theme`-App) liefert Tokens + `@layer components`. Multi-Stage Dockerfile baut CSS in einer Node-Stage, Runtime bleibt Node-frei. Wiederverwendbare Template-Partials (`templates/components/`) ersetzen Inline-Styles. Inkrementeller Rollout Phase 0→5, jede Phase deploybar.

**Tech Stack:** Django 4.2, django-tailwind, Tailwind v3, Node 20 (nur Build), Turbo 7.3, WhiteNoise (CompressedManifestStaticFilesStorage), Docker.

## Modell-Routing (Token-Ersparnis)

Pro Task ist ein Agent/Modell empfohlen. Regel: Mechanik & Markup-Umbau → günstig; Architektur/Token-Design/Review → stärker.

| Aufgabentyp | Agent | Modell |
|---|---|---|
| Architektur-Setup (Docker, settings, tailwind.config) | Orchestrator selbst | Opus/Sonnet nativ |
| Komponenten & Markup-Umbau (>20 Z.) | `@code-creator` | `glm/glm-5` |
| Template-Mechanik, Inline-Style-Extraktion | `@code-creator` | `glm/glm-5` |
| Review jeder Phase | `@code-reviewer` | `glm/glm-4.7-flash` |
| Datei-/Markup-Suche | `@explorer` | Haiku |
| Tests/Container-Build prüfen | `@test-runner` | Haiku |

Token-Regeln: nur Diffs, keine ganzen Dateien zurückgeben; Partials einmal bauen, dann referenzieren.

---

## Dateistruktur

```
Dockerfile                          # Multi-Stage (node build → python runtime)
docker-compose.yml                  # + optionales tailwind-watch-Service
requirements.txt                    # + django-tailwind
app/dj_wishlist/settings.py         # + tailwind/theme apps, INTERNAL_IPS, NPM_BIN_PATH
app/theme/                          # django-tailwind app (generiert)
  static_src/src/styles.css         #   @tailwind + @layer components (.glass, .btn-*)
  static_src/tailwind.config.js     #   Custom-Tokens (Farben, Fonts, Spacing, Z, Shadow)
  static/css/dist/styles.css        #   Build-Output (collectstatic-Quelle)
app/templates/components/           # Wiederverwendbare Partials
  card.html  button.html  icon_button.html  form_field.html
  tabs.html  stat_card.html  badge.html  toast.html  data_table.html
  now_playing.html  wish_card.html
app/static/js/polling.js            # aria-live Live-Update-Wrapper
app/templates/dj_admin/*.html       # Phasen 3-4: auf Tailwind+Partials umgestellt
app/templates/wishlist/*.html       # Phase 2: auf Tailwind+Partials umgestellt
```

---

## PHASE 0 — Fundament (Orchestrator selbst, nativ)

**Blockierend für alle weiteren Phasen.**

### Task 0.1: django-tailwind installieren & registrieren

**Files:**
- Modify: `requirements.txt`
- Modify: `app/dj_wishlist/settings.py:27-48` (INSTALLED_APPS, MIDDLEWARE-Umgebung)

- [ ] **Step 1:** `requirements.txt` ergänzen:

```
django-tailwind>=3.8
```

- [ ] **Step 2:** In `settings.py` zu `INSTALLED_APPS` hinzufügen (nach bestehenden Apps):

```python
INSTALLED_APPS += [
    "tailwind",
    "theme",
]
TAILWIND_APP_NAME = "theme"
NPM_BIN_PATH = "/usr/bin/npm"  # Node-Stage-Pfad; Dev-Service überschreibt via env
INTERNAL_IPS = ["127.0.0.1"]
```

- [ ] **Step 3: Commit**

```bash
git add requirements.txt app/dj_wishlist/settings.py
git commit -m "feat: register django-tailwind theme app"
```

*(Hinweis: `git` ist im Projekt evtl. nicht initialisiert — falls `git` fehlschlägt, Commits dieser Phase überspringen und am Ende einmalig sichern.)*

### Task 0.2: theme-App scaffolden & Tokens definieren

**Files:**
- Create: `app/theme/` (via `manage.py tailwind init`)
- Create/Modify: `app/theme/static_src/tailwind.config.js`
- Create/Modify: `app/theme/static_src/src/styles.css`

- [ ] **Step 1:** App erzeugen (im Container mit Node, siehe Task 0.4 — beim ersten Mal ggf. lokal):

Run: `python manage.py tailwind init theme`

- [ ] **Step 2:** `tailwind.config.js` content-Pfade + Tokens setzen. `content` muss alle Templates abdecken:

```js
module.exports = {
  content: ["../templates/**/*.html", "../../templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        bg: "#06060e",
        surface: "rgba(12,12,24,.85)",
        card: "rgba(16,16,32,.7)",
        "card-solid": "#10102a",
        border: { DEFAULT: "rgba(40,40,80,.45)", hover: "rgba(70,70,140,.5)" },
        accent: { DEFAULT: "#3b82f6", 400: "#60a5fa", 600: "#2563eb", glow: "rgba(59,130,246,.25)" },
        accent2: { DEFAULT: "#a855f7", glow: "rgba(168,85,247,.2)" },
        success: "#22c55e", warn: "#eab308", danger: "#ef4444",
        text: { DEFAULT: "#e4e4ef", bright: "#f4f4ff" },
        // A11y-Fix: >=4.5:1 auf bg
        muted: "rgba(176,176,214,.92)",
        subtle: "rgba(148,148,190,.7)",
      },
      fontFamily: {
        display: ["'Bebas Neue'", "sans-serif"],
        sans: ["'DM Sans'", "sans-serif"],
        mono: ["'Space Mono'", "monospace"],
      },
      borderRadius: { DEFAULT: "10px", sm: "6px" },
      boxShadow: {
        "glass-sm": "0 2px 12px rgba(0,0,0,.2)",
        "glass-lg": "0 12px 40px rgba(0,0,0,.45)",
      },
      zIndex: { dropdown: "10", sticky: "20", modal: "100", toast: "200", drawer: "150" },
    },
  },
  plugins: [],
};
```

- [ ] **Step 3:** `src/styles.css` mit `@layer components` für Glas + Buttons:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer components {
  .glass {
    @apply bg-card border border-border rounded backdrop-blur-lg shadow-glass-sm;
    backdrop-filter: saturate(1.6) blur(18px);
    -webkit-backdrop-filter: saturate(1.6) blur(18px);
  }
  .btn { @apply inline-flex items-center justify-center gap-2 min-h-[44px] px-4 rounded-sm font-medium transition; }
  .btn-primary { @apply btn bg-accent text-white hover:bg-accent-600; }
  .btn-purple  { @apply btn bg-accent2 text-white hover:opacity-90; }
  .btn-danger  { @apply btn bg-danger text-white hover:opacity-90; }
  .btn-ghost   { @apply btn bg-transparent text-text border border-border hover:border-border-hover; }
  .field-label { @apply block text-sm text-muted mb-1 font-mono; }
  .field-input { @apply w-full min-h-[44px] px-3 bg-card-solid border border-border rounded-sm text-text focus:border-accent focus:ring-4 focus:ring-accent-glow outline-none; }
}
```

- [ ] **Step 4: Commit**

```bash
git add app/theme/static_src/tailwind.config.js app/theme/static_src/src/styles.css
git commit -m "feat: tailwind design tokens + component layer"
```

### Task 0.3: Multi-Stage Dockerfile

**Files:**
- Modify: `Dockerfile`

- [ ] **Step 1:** Dockerfile durch Multi-Stage ersetzen:

```dockerfile
# ── Stage 1: Tailwind build ──────────────────────────────
FROM node:20-slim AS css
WORKDIR /app
COPY app/theme/static_src/package*.json app/theme/static_src/
RUN cd app/theme/static_src && npm install
COPY app ./app
RUN cd app/theme/static_src && npx tailwindcss \
    -i ./src/styles.css -o ../static/css/dist/styles.css --minify

# ── Stage 2: Python runtime ──────────────────────────────
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app .
COPY --from=css /app/app/theme/static/css/dist/styles.css ./theme/static/css/dist/styles.css
RUN chmod +x /app/entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
```

- [ ] **Step 2: Verify build.** `@test-runner` (Haiku):

Run: `docker compose build web`
Expected: Build erfolgreich, Stage `css` erzeugt `styles.css`, kein Node im Runtime.

- [ ] **Step 3: Commit** `git add Dockerfile && git commit -m "feat: multi-stage tailwind build"`

### Task 0.4: Dev-Watch-Service (optional, compose)

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1:** Service ergänzen (nur Dev; Volume-Mount nutzt vorhandenes `./app`):

```yaml
  tailwind:
    image: node:20-slim
    working_dir: /app/theme/static_src
    volumes:
      - ./app:/app
    command: sh -c "npm install && npx tailwindcss -i ./src/styles.css -o ../static/css/dist/styles.css --watch"
    profiles: ["dev"]
```

- [ ] **Step 2: Commit** `git add docker-compose.yml && git commit -m "chore: tailwind dev watch service"`

### Task 0.5: base.html-Pilot — Tailwind laden + Render-Smoke-Test

**Files:**
- Modify: `app/templates/dj_admin/base.html:7-10` (CSS einbinden)

- [ ] **Step 1:** Im `<head>` Tailwind-CSS laden (vor/statt großem `<style>`-Block; alten Block vorerst belassen für Parität):

```html
{% load static tailwind_tags %}
{% tailwind_css %}
```

- [ ] **Step 2:** Test-Marker: einem Element eine Tailwind-Klasse geben, z.B. `<body class="font-sans">`.

- [ ] **Step 3: Verify.** `@test-runner` (Haiku):

Run: `docker compose up -d web && curl -s localhost:8500/dj-admin/ | grep -c "dist/styles.css"`
Expected: `>= 1` (CSS-Link gerendert).

- [ ] **Step 4: Commit** `git commit -am "feat: load tailwind css in base template"`

**Phase-0-Akzeptanz:** Container baut, `/dj-admin/` rendert mit eingebundenem Tailwind-CSS, Runtime ohne Node.

---

## PHASE 1 — Komponenten-Bibliothek (`@code-creator` glm/glm-5, Review glm-4.7-flash)

Jede Komponente ist ein Django-Partial mit dokumentierten `{% include %}`-Parametern. Pro Task: bauen → `@code-reviewer` prüft → Smoke-Render.

### Task 1.1: button.html + icon_button.html

**Files:**
- Create: `app/templates/components/button.html`
- Create: `app/templates/components/icon_button.html`

- [ ] **Step 1:** `button.html` (Parameter: `variant` default `primary`, `label`, `type`, `href`, `aria_label`, `extra`):

```django
{% comment %} include: variant(primary|purple|danger|ghost) label type href aria_label extra {% endcomment %}
{% with v=variant|default:"primary" %}
{% if href %}
<a href="{{ href }}" class="btn-{{ v }} {{ extra }}"{% if aria_label %} aria-label="{{ aria_label }}"{% endif %}>{{ label }}</a>
{% else %}
<button type="{{ type|default:'button' }}" class="btn-{{ v }} {{ extra }}"{% if aria_label %} aria-label="{{ aria_label }}"{% endif %}>{{ label }}</button>
{% endif %}
{% endwith %}
```

- [ ] **Step 2:** `icon_button.html` (Pflicht-`aria_label`, Emoji `aria-hidden`):

```django
{% comment %} include: icon aria_label variant onclick extra (aria_label PFLICHT) {% endcomment %}
<button type="button" class="btn-{{ variant|default:'ghost' }} !min-w-[44px] !px-2 {{ extra }}"
        aria-label="{{ aria_label }}"{% if onclick %} onclick="{{ onclick }}"{% endif %}>
  <span aria-hidden="true">{{ icon }}</span>
</button>
```

- [ ] **Step 3: Review** `@code-reviewer`: A11y (aria_label gesetzt?), Klassen existieren in styles.css.
- [ ] **Step 4: Commit** `git add app/templates/components/button.html app/templates/components/icon_button.html && git commit -m "feat: button + icon_button components"`

### Task 1.2: form_field.html

**Files:** Create: `app/templates/components/form_field.html`

- [ ] **Step 1:** Label+Input+Fehler als Einheit, erzwingt `for`/`id`:

```django
{% comment %} include: id label name type value required help error placeholder {% endcomment %}
<div class="mb-4">
  <label for="{{ id }}" class="field-label">{{ label }}{% if required %} <span aria-hidden="true">*</span>{% endif %}</label>
  <input id="{{ id }}" name="{{ name }}" type="{{ type|default:'text' }}"
         value="{{ value|default:'' }}" placeholder="{{ placeholder|default:'' }}"
         class="field-input{% if error %} border-danger{% endif %}"
         {% if required %}required aria-required="true"{% endif %}
         {% if error %}aria-invalid="true" aria-describedby="{{ id }}-err"{% elif help %}aria-describedby="{{ id }}-help"{% endif %}>
  {% if help and not error %}<small id="{{ id }}-help" class="text-subtle text-xs">{{ help }}</small>{% endif %}
  {% if error %}<small id="{{ id }}-err" role="alert" class="text-danger text-xs">{{ error }}</small>{% endif %}
</div>
```

- [ ] **Step 2: Review + Commit** `git add app/templates/components/form_field.html && git commit -m "feat: accessible form_field component"`

### Task 1.3: tabs.html (ARIA + Pfeiltasten)

**Files:**
- Create: `app/templates/components/tabs.html`
- Create: `app/static/js/tabs.js`

- [ ] **Step 1:** `tabs.html` — erwartet `tabs`-Liste mit `id`/`label`:

```django
{% comment %} include: tabs(list of {id,label}) group {% endcomment %}
<div class="flex gap-1 border-b border-border mb-4" role="tablist" data-tabs="{{ group }}">
  {% for t in tabs %}
  <button role="tab" id="tab-{{ group }}-{{ t.id }}" aria-controls="panel-{{ group }}-{{ t.id }}"
          aria-selected="{% if forloop.first %}true{% else %}false{% endif %}"
          tabindex="{% if forloop.first %}0{% else %}-1{% endif %}"
          class="min-h-[44px] px-4 font-mono text-sm border-b-2 border-transparent aria-selected:border-accent aria-selected:text-accent text-muted">
    {{ t.label }}
  </button>
  {% endfor %}
</div>
```

- [ ] **Step 2:** `tabs.js` — Klick + Pfeiltasten, schaltet `aria-selected`/`hidden` der Panels (`#panel-<group>-<id>`):

```js
document.querySelectorAll('[role="tablist"]').forEach(list => {
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  const sel = i => tabs.forEach((t, j) => {
    const on = i === j;
    t.setAttribute('aria-selected', on); t.tabIndex = on ? 0 : -1;
    const panel = document.getElementById(t.getAttribute('aria-controls'));
    if (panel) panel.hidden = !on;
    if (on) t.focus();
  });
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => sel(i));
    t.addEventListener('keydown', e => {
      if (e.key === 'ArrowRight') sel((i + 1) % tabs.length);
      if (e.key === 'ArrowLeft') sel((i - 1 + tabs.length) % tabs.length);
    });
  });
  sel(0);
});
```

- [ ] **Step 3: Review + Commit** `git add app/templates/components/tabs.html app/static/js/tabs.js && git commit -m "feat: accessible tabs component"`

### Task 1.4: card, stat_card, badge, toast

**Files:** Create: `app/templates/components/{card,stat_card,badge,toast}.html`

- [ ] **Step 1:** `card.html`:

```django
{% comment %} include: extra ; content via caller block not supported -> use as wrapper include with {{ body }} {% endcomment %}
<div class="glass p-4 md:p-6 {{ extra }}">{{ body|safe }}</div>
```

- [ ] **Step 2:** `stat_card.html` (Params `label`,`value`,`accent`):

```django
<div class="glass p-4 flex flex-col gap-1">
  <span class="font-mono text-xs text-muted uppercase tracking-wide">{{ label }}</span>
  <span class="font-display text-3xl text-{{ accent|default:'text-bright' }}">{{ value }}</span>
</div>
```

- [ ] **Step 3:** `badge.html` (`label`,`tone`): `<span class="inline-flex items-center px-2 py-0.5 rounded-sm text-xs font-mono bg-{{ tone|default:'border' }}/30 text-{{ tone|default:'muted' }}">{{ label }}</span>`

- [ ] **Step 4:** `toast.html` mit `aria-live`:

```django
<div id="toast" role="status" aria-live="polite" aria-atomic="true"
     class="fixed bottom-4 right-4 z-toast glass px-4 py-3 hidden"></div>
```

- [ ] **Step 5: Review + Commit** `git commit -am "feat: card/stat_card/badge/toast components"`

### Task 1.5: data_table.html (responsiv)

**Files:** Create: `app/templates/components/data_table.html`

- [ ] **Step 1:** Tabelle am Desktop, Card-Stack am Handy via `data-label` + CSS. Params: `columns` (Liste {key,label}), `rows` (Liste Dicts), `caption`.

```django
{% comment %} include: columns(list {key,label}) rows(list dict) caption {% endcomment %}
<table class="w-full text-sm">
  {% if caption %}<caption class="sr-only">{{ caption }}</caption>{% endif %}
  <thead class="max-md:hidden"><tr class="text-left text-muted font-mono text-xs">
    {% for c in columns %}<th class="py-2 px-3">{{ c.label }}</th>{% endfor %}
  </tr></thead>
  <tbody>
    {% for row in rows %}
    <tr class="border-t border-border max-md:block max-md:border max-md:rounded max-md:mb-2 max-md:p-2">
      {% for c in columns %}
      <td class="py-2 px-3 max-md:flex max-md:justify-between max-md:before:content-[attr(data-label)] max-md:before:text-muted max-md:before:font-mono"
          data-label="{{ c.label }}">{{ row|get:c.key }}</td>
      {% endfor %}
    </tr>
    {% endfor %}
  </tbody>
</table>
```

- [ ] **Step 2:** Falls `get`-Filter fehlt: bestehende Tabellen rendern Zellen meist direkt im Template — `data_table.html` dann als Layout-Hülle mit `{% block %}` statt Daten-Filter verwenden. `@code-creator` prüft vorhandene Template-Tags und wählt passende Variante.
- [ ] **Step 3: Review + Commit** `git commit -am "feat: responsive data_table component"`

### Task 1.6: now_playing.html + wish_card.html + polling.js

**Files:**
- Create: `app/templates/components/now_playing.html`, `app/templates/components/wish_card.html`
- Create: `app/static/js/polling.js`

- [ ] **Step 1:** `@explorer` (Haiku) extrahiert das aktuelle Now-Playing- und Wish-Card-Markup aus `wishlist/index.html` und `dj_admin/wishlist_live.html` (nur die relevanten Blöcke, mit Zeilen).
- [ ] **Step 2:** `@code-creator` baut beide Partials aus dem extrahierten Markup, Cover `min-w-[44px]`, ganze Wish-Card als Touch-Target, Tailwind-Klassen statt Inline.
- [ ] **Step 3:** `polling.js` — Wrapper, der gepollte Container als `aria-live="polite"` markiert:

```js
export function livePoll(selector, url, ms) {
  const el = document.querySelector(selector);
  if (!el) return;
  el.setAttribute('aria-live', 'polite');
  el.setAttribute('aria-atomic', 'false');
  const tick = () => fetch(url).then(r => r.text()).then(html => { el.innerHTML = html; });
  setInterval(tick, ms);
}
```

- [ ] **Step 4: Review + Commit** `git commit -am "feat: now_playing/wish_card partials + aria-live polling"`

### Task 1.7: Pilot — Dashboard auf Komponenten umstellen

**Files:** Modify: `app/templates/dj_admin/dashboard.html`

- [ ] **Step 1:** `@code-creator` ersetzt Stat-Cards durch `{% include "components/stat_card.html" %}`, Tabellen durch `data_table.html`, Buttons durch `button.html`. Grid → `grid-cols-1 sm:grid-cols-3`.
- [ ] **Step 2: Verify.** `@test-runner`: `curl -s localhost:8500/dj-admin/` rendert ohne Template-Error; visuelle Mobile-QA bei 375px.
- [ ] **Step 3: Review + Commit** `git commit -am "refactor: dashboard uses component library"`

**Phase-1-Akzeptanz:** Alle Partials existieren & rendern; Dashboard nutzt sie ohne Regression.

---

## PHASE 2 — Gäste-Seiten (`@code-creator` glm/glm-5)

Höchste Außenwirkung, mobil-kritisch. Reihenfolge: Layout → Wishlist → Buchungsformular.

### Task 2.1: base.html Layout + Off-Canvas Drawer

**Files:** Modify: `app/templates/dj_admin/base.html`

- [ ] **Step 1:** Sidebar → off-canvas Drawer am Handy: `class="... max-lg:fixed max-lg:-translate-x-full max-lg:z-drawer [&.open]:translate-x-0"`, Hamburger-Button `min-h-[44px]`, Backdrop-Overlay, Fokus-Trap (kleines JS in `app/static/js/drawer.js`).
- [ ] **Step 2:** Großen Inline-`<style>`-Block schrittweise entfernen, sobald Klassen migriert sind (Parität prüfen).
- [ ] **Step 3:** Alle `grid`-Layouts → `grid-cols-1 lg:grid-cols-2`.
- [ ] **Step 4: Review (a11y: Fokus-Trap, Tastatur-Escape) + Commit** `git commit -am "feat: responsive off-canvas sidebar"`

### Task 2.2: wishlist/index.html

**Files:** Modify: `app/templates/wishlist/index.html`, `app/templates/wishlist/_wishes_list.html`

- [ ] **Step 1:** Eigenen Token-`<style>`-Block entfernen, `{% tailwind_css %}` laden (Gäste-Layout hat kein base.html → ggf. eigenes `wishlist/base.html` anlegen).
- [ ] **Step 2:** Such-Tabs → `tabs.html`; Such-Ergebnisse `role="listbox"`/`option` + `aria-live`; Wish-Karten → `wish_card.html` (Cover ≥44px, Karte Touch-Target).
- [ ] **Step 3:** Now-Playing → `now_playing.html`, visuell untergeordnet (kompakt, `text-subtle`).
- [ ] **Step 4:** Polling auf `polling.js` umstellen.
- [ ] **Step 5: Verify** 375px + axe-Check (Kontrast, ARIA). **Review + Commit** `git commit -am "feat: redesign guest wishlist mobile-first"`

### Task 2.3: wishlist/event_form.html — Steps

**Files:** Modify: `app/templates/wishlist/event_form.html`

- [ ] **Step 1:** Flow in echte Steps mit Progress-Indikator („Schritt N von 5"): Datum → Kunde → Event → Paket → Optionen; eine Sektion pro Screen am Handy (JS-Step-Wechsel, ohne Server-Roundtrip).
- [ ] **Step 2:** Alle Felder → `form_field.html` (Label-Verknüpfung, `aria-required`, Fehlertexte). Checkboxen/Radios `min-h-[44px]`.
- [ ] **Step 3:** Adress-Dropdown viewport-begrenzt (`max-w-[calc(100vw-2rem)]`). Sticky Preis-Zusammenfassung unten mit `aria-live="polite"`.
- [ ] **Step 4: Verify + Review + Commit** `git commit -am "feat: stepped accessible booking form"`

**Phase-2-Akzeptanz:** Gäste-Seiten bei 375px sauber, axe ohne kritische Fehler, Touch-Targets ≥44px.

---

## PHASE 3 — DJ-Dashboard & Live (`@code-creator` glm/glm-5)

### Task 3.1: wishlist_live.html — Mobile-Tabs-Overhaul

**Files:** Modify: `app/templates/dj_admin/wishlist_live.html`

- [ ] **Step 1:** Am Handy `tabs.html` mit 4 Panels: „Wünsche"(Default)/„Now Playing"/„Einstellungen"/„Blockieren". Desktop: 2-Spalten bleibt, Triage-Reihenfolge Now-Playing+Wünsche zuerst, Settings/Block in `<details>`-Accordion.
- [ ] **Step 2:** Action-Buttons pro Wish (📊✓🚫✕↻) → `icon_button.html` mit aria-label („Audio-Features", „Als gespielt markieren", „Blockieren", „Löschen", „Aktualisieren").
- [ ] **Step 3:** Polling-Frames (`_wishlist_frame`, `_blocked_frame`) → `polling.js`-aria-live.
- [ ] **Step 4: Verify + Review + Commit** `git commit -am "feat: live event mobile tabs + a11y buttons"`

### Task 3.2: calendar.html

**Files:** Modify: `app/templates/dj_admin/calendar.html`

- [ ] **Step 1:** FullCalendar `initialView` responsiv: `window.innerWidth < 768 ? 'listWeek' : 'dayGridMonth'`. Container Tailwind-Klassen.
- [ ] **Step 2: Verify + Commit** `git commit -am "feat: mobile-friendly calendar list view"`

**Phase-3-Akzeptanz:** Live-Seite & Kalender mobil bedienbar; alle Icon-Buttons mit Labels.

---

## PHASE 4 — Komplexe Admin-Seiten (`@code-creator` glm/glm-5)

### Task 4.1: event_form.html (Admin) — Kalkulator-Accordion

**Files:** Modify: `app/templates/dj_admin/event_form.html`

- [ ] **Step 1:** 3 Preis-Kalkulatoren in `<details>`-Accordion bzw. `tabs.html`, nur einer sichtbar; Überschriften „Manuell" vs. „Gäste-Staffelung (auto)".
- [ ] **Step 2:** Event/Kunde/Preis als 3 kollabierbare Sektionen mobil; `grid-cols-1 lg:grid-cols-2`. Felder → `form_field.html`.
- [ ] **Step 3: Verify + Review + Commit** `git commit -am "feat: declutter admin event form"`

### Task 4.2: workflow_builder.html — Tastatur/Touch-Alternative

**Files:** Modify: `app/templates/dj_admin/workflow_builder.html`

- [ ] **Step 1:** Pro Block `↑`/`↓`-Buttons (`icon_button.html`, aria-label „nach oben/unten") die `wb-step`-Reihenfolge im DOM tauschen; Drag&Drop (Maus) bleibt.
- [ ] **Step 2:** `.wb-wrap` → `flex-col lg:flex-row`; Palette am Handy als Drawer/Accordion. Eingebetteten `<style>` nach Tailwind migrieren.
- [ ] **Step 3: Verify + Review + Commit** `git commit -am "feat: keyboard-accessible workflow builder mobile"`

### Task 4.3: config.html — ARIA-Tabs

**Files:** Modify: `app/templates/dj_admin/config.html`

- [x] **Step 1:** Tab-System durch `tabs.html` + `tabs.js` ersetzt (ARIA + Pfeiltasten). Felder → `form_field.html`.
- [x] **Step 2: Verify + Review + Commit** `git commit -am "feat: accessible config tabs"`

**Phase-4-Akzeptanz:** Alle Admin-Seiten mobil, ARIA-Tabs, Workflow tastatur-bedienbar.

---

## PHASE 5 — Politur & Verifikation

### Task 5.1: Inline-Style-Residuen entfernen

- [x] **Step 1:** Verbleibende Inline-Styles erfasst.
- [x] **Step 2:** Statische Inline-Styles auf Tailwind-Utilities migriert (dashboard, wishlist_live, event_form, workflow_builder, config, calendar, base, Partials). Belassen: JS-getoggelte `display:none`, JS-innerHTML-Strings, `accent-color` (kein Tailwind-Äquiv). Große `<style>`-Blöcke (Legacy-Klassen `.card`/`.btn`/`.tab`) noch in Verwendung → bleiben vorerst.
- [x] **Step 3: Commit** `a576652`

### Task 5.2: Durchgängiger A11y-Audit

- [x] **Step 1:** `@code-reviewer`-Audit: 5 Befunde (dashboard-Tabs ohne ARIA, af-modal ohne Fokus/ESC, --muted Kontrast <4.5:1, guest-name ohne Label, NP-Widgets ohne aria-live).
- [x] **Step 2:** Alle 5 gefixt + block-dialog ESC/Fokus ergänzt.
- [x] **Step 3: Commit** `3d75546`

### Task 5.3: Mobile-QA-Matrix

- [x] **Step 1:** Jeden Screen bei 375px / 768px / 1280px geprüft (Playwright, Session-Cookie-Auth). Befunde: (a) mehrzeiliger `{# #}`-Kommentar in `wishlist_live.html` rendert als Literaltext → Phantom-`<lg):`-Tag → auf `{% comment %}` umgestellt; (b) `.main` als Flex-Child ohne `min-width:0` verursachte Viewport-Overflow auf dashboard/live/config/workflow → `min-width:0` + `overflow-x:hidden`; (c) `.wish-item`-Action-Buttons bei 375px abgeschnitten → `flex-wrap:wrap`. Danach alle 7 Screens × 3 Breakpoints overflow-frei, keine Konsolen-Fehler.
- [x] **Step 2:** Production-Build verifiziert: `docker compose build web` grün, Container `healthy`, `collectstatic`-Manifest enthält `css/dist/styles.css`, Gäste-Seite rendert gehashtes `dist/styles.<hash>.css` (HTTP 200), `/dj-admin/` 302 (Auth-Redirect).
- [x] **Step 3: Final Commit**

**Phase-5-Akzeptanz:** Keine Inline-Styles mehr, axe ohne kritische Fehler, alle Screens auf 3 Breakpoints sauber, Production-Build grün.

---

## Self-Review-Notizen

- **Spec-Abdeckung:** Tailwind/Multi-Stage (P0) ✓ · Tokens+Kontrast-Fix (0.2) ✓ · alle 11 Komponenten (P1) ✓ · Gäste-Seiten inkl. Steps (P2) ✓ · Live-Tabs+Icon-Labels (3.1) ✓ · Kalender (3.2) ✓ · Event-Form-Accordion (4.1) ✓ · Workflow ↑/↓ (4.2) ✓ · Config-ARIA-Tabs (4.3) ✓ · Politur/A11y/Mobile-QA (P5) ✓.
- **Offene Annahme:** Gäste-Templates haben evtl. kein gemeinsames `base.html` — Task 2.2 legt bei Bedarf `wishlist/base.html` an. `@explorer` klärt dies vor P2.
- **Git:** Projekt ist evtl. nicht initialisiert; falls `git`-Commits fehlschlagen, Phasen-weise sichern.
- **Konsistenz:** Klassennamen (`.glass`, `.btn-*`, `.field-input`, `tabs.js`-Selektoren `#panel-<group>-<id>`) durchgängig identisch verwendet.
