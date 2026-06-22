# Design: Preis-Workflow fertigstellen

**Datum:** 2026-06-10
**Status:** Vom User freigegeben

## Ziel

Zwei Baustellen des Preis-Workflow-Systems abschließen:

1. **Live-Preisschätzung für Kunden** im öffentlichen Buchungsformular (`/buchen/`) inkl. automatischer Distanzberechnung aus der Event-Adresse.
2. **Workflow-Builder stabilisieren**: fehlende JS-Funktionen nachrüsten und einen vollwertigen Simulations-Test einbauen.

## Kontext / Ausgangslage

- `api_price_estimate()` (`app/wishlist/views.py:540–583`) existiert und ist unter `/api/price-estimate/` geroutet (`urls.py:15`). Sie baut ein ungespeichertes `Event()` aus JSON-Daten und ruft `PriceEngine.calculate()` bzw. `calculate_workflow()` auf.
- `event_form.html` hat bereits Live-Preis-JS (`doUpdatePrice()`, 400ms-Debounce, Breakdown-Anzeige) und Nominatim-Adresssuche.
- `price_engine.py` ist stabil; Berechnungen brauchen `date`, `time_start`, `time_end`, `guest_count`, `event_type`, `distance_km`.
- Der Workflow-Builder (`workflow_builder.html`, ~860 Zeilen) funktioniert grundsätzlich (Drag-Drop-Blöcke, Komponenten-Tabs), hat aber fehlende JS-Funktionen.

### Bekannte Lücken (Lückenanalyse 2026-06-10)

| Lücke | Ort | Auswirkung |
|---|---|---|
| `f-distance`-Element existiert nicht im HTML | `event_form.html:279` | `distance_km` ist immer `null`, Distanz-Regeln greifen nie |
| Typo `input[name="package"]` statt `f-package` | `event_form.html:281` (Submit-Pfad) | Paket wird beim Absenden nicht mitgesendet |
| `getStepConfigValue()` nie definiert | `workflow_builder.html` (~667, aufgerufen in `testWorkflow()`) | Test-Button kann Paket/Formel-Config nicht auslesen |
| `showToast()` nie definiert | `workflow_builder.html` (überall aufgerufen) | Keine Erfolgs-/Fehlermeldungen, JS-Errors |
| Backend ignoriert `test_context` | `admin_views.py:602` (`api_price_calculate()`) | Workflow-Test nur gegen echte DB-Events möglich |

## Entscheidungen

- **Distanz:** Frontend-Berechnung (Ansatz A). Haversine-Luftlinie zwischen DJ-Standort und Event-Adresse × Korrekturfaktor 1,3 (Näherung Straßenkilometer). Keine neuen externen Abhängigkeiten, kein Nominatim-Rate-Limit-Risiko bei Live-Updates. Manipulierbarkeit akzeptiert, da unverbindliche Schätzung — finale Kalkulation macht der DJ.
- **Preisanzeige Kunde:** Voller Breakdown (alle Posten und Regeln), wie bereits angelegt.
- **Builder-Test:** Voller Simulations-Test mit frei eingebbaren Event-Daten.

## Teil 1 — Live-Preis auf /buchen/

### Model & Config

- `AppConfig` bekommt zwei neue Felder: `dj_home_lat`, `dj_home_lon` (DecimalField, max_digits=9, decimal_places=6, null/blank). Migration `0009`.
- Config-Seite (`config.html`): Adress-Suchfeld mit der vorhandenen Nominatim-Suche; bei Auswahl werden lat/lon in die Felder geschrieben und gespeichert. Einmalige Einrichtung durch den DJ.

### Frontend (`event_form.html`)

- View rendert DJ-Koordinaten aus AppConfig als JS-Konstanten ins Template.
- Bei Nominatim-Adressauswahl: lat/lon des Events merken → Haversine × 1,3 → gerundetes `distance_km`.
- `doUpdatePrice()` schickt `distance_km` an `/api/price-estimate/`; Anfahrtskosten erscheinen im Breakdown.
- **Bugfix:** toten `f-distance`-Zugriff (Zeile 279) durch die berechnete Variable ersetzen.
- **Bugfix:** Submit-Typo `input[name="package"]` → `input[name="f-package"]`.
- `submit_event_form()` (`views.py`) speichert `distance_km` mit ans Event, damit der DJ-Admin denselben Wert sieht.

### Fehlerverhalten

- Keine DJ-Koordinaten hinterlegt → `distance_km: null`, Breakdown ohne Anfahrt, kein Fehler.
- Kunde wählt keine Adresse über Nominatim (nur Freitext) → ebenfalls `distance_km: null`.

## Teil 2 — Workflow-Builder stabilisieren

### Korrektur nach detaillierter Code-Recherche (2026-06-10)

Die Lückenanalyse war teilweise veraltet: `showToast()` ist in `dj_admin/base.html:237` definiert (Builder erbt davon), `getStepConfigValue()` existiert in `workflow_builder.html:667–670`, und das Test-Parameter-Panel (Gäste, Dauer, Distanz, Wochentag, Monat, Event-Typ) inkl. `test_context`-Versand ist bereits gebaut (Zeilen 143–170, 685–692). **Am Builder-Frontend ist nichts zu tun.**

### Simulations-Test (nur Backend)

- `PriceEngine.calculate()` und `calculate_workflow()` bekommen einen optionalen Parameter `context_override`: ein Dict, das nach `RuleEvaluator.build_context()` per `context.update()` angewendet wird. Die Keys entsprechen dem Context-Format (`guest_count`, `duration_hours`, `distance_km`, `date_weekday`, `date_month`, `event_type`).
- `api_price_calculate()` (`admin_views.py:602`) liest `test_context` aus dem Request, filtert auf die erlaubten Keys und reicht es als `context_override` durch. Ohne `test_context` bleibt das Verhalten unverändert.

## Bewusste Auslassungen

- `EventOffer` wird weiterhin **nicht** automatisch erstellt; Gäste-Staffelpreise bleiben Admin-only. Nicht Teil dieses Scopes.
- Kein Aufbau einer Test-Infrastruktur (pytest etc.) — separates Thema.

## Verifikation

- Haversine-Logik: kurzer Unit-Check der Formel (bekanntes Städtepaar) in der Django-Shell bzw. Browser-Konsole.
- Buchungsformular: manuell im Browser — Adresse wählen, Live-Preis mit Anfahrt prüfen, Absenden mit Paket prüfen.
- Workflow-Builder: manuell im Browser — Toasts, Simulations-Test mit verschiedenen Werten.
- Server-Log via @django-monitor auf Fehler prüfen.
