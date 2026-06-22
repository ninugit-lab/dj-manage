# Design: Preiskalkulator-UI überarbeiten

**Datum:** 2026-06-10
**Status:** Vom User freigegeben

## Ziel

Die Bedienung des Preiskalkulators (`/dj-admin/workflow/`, Template `workflow_builder.html`) vereinfachen:

1. Kaputtes Drag & Drop im Workflow-Canvas durch **Klick-Bedienung** ersetzen.
2. Die vier größten UX-Schmerzpunkte der **Komponenten-Tabs** (Posten, Pakete, Regeln, Formeln) beheben.

Gewählter Ansatz: gezielte Modernisierung im bestehenden Template — kein Backend-/Engine-Umbau, funktionierende Teile (PriceEngine, CRUD-APIs, Test-Panel) bleiben unangetastet.

## Kontext

- Template: `app/templates/dj_admin/workflow_builder.html` (~860 Zeilen). Palette-Blöcke Zeilen 59–83, DnD-JS Zeilen 506–618, Komponenten-Tabs HTML Zeilen 174–471, CRUD-JS Zeilen 720–860.
- Engine: `RuleEvaluator.evaluate()` (price_engine.py:110–129) iteriert bereits über eine Bedingungs-Liste (UND-verknüpft) — mehrere Bedingungen pro Regel sind backend-seitig unterstützt, nur das UI baut stets ein Array mit genau einer Bedingung (Zeile 769).
- `SafeFormulaEvaluator` (price_engine.py) wertet Formeln sicher aus; `FORMULA_VARS = {'base', 'guests', 'hours', 'distance', 'items_total', 'package_price'}`.
- Bekannte Schmerzpunkte (Analyse 2026-06-10): `location.reload()` nach jedem Save (12 Stellen), nur 1 Bedingung pro Regel, Bedingungen beim Inline-Edit unsichtbar, keine Formel-Validierung, Emoji-Buttons ohne Label, Paket-Checkboxlisten ohne Scroll/Suche.

## Teil 1 — Workflow-Canvas: Klick-Bedienung

- Palette-Blöcke werden Buttons: **Klick → Block ans Ende des Workflows anhängen** (statt Drag).
- Jeder Canvas-Block erhält rechts drei Buttons: **▲** (hoch), **▼** (runter), **✕** (entfernen). An erster/letzter Position ist der jeweilige Pfeil deaktiviert.
- Sämtlicher DnD-Code entfällt: `draggable`-Attribute, dragstart/dragover/drop/dragleave-Handler, `.drag-over`-CSS.
- Hilfe-Box-Text an neue Bedienung anpassen.
- Block-Konfiguration (Paket-/Formel-Dropdowns im Block) bleibt unverändert; bestehende Funktionen `renderSteps()`, `saveWorkflow()`, `testWorkflow()` bleiben funktional erhalten.

## Teil 2 — Komponenten-Tabs

### a) Kein Vollseiten-Reload

Nach Anlegen/Bearbeiten/Löschen von Posten, Paketen, Regeln, Formeln wird nur die jeweilige Tab-Liste per JS neu gerendert. Datenquelle: bestehende GET-APIs (`api/price/items/`, `api/price/packages/`, `api/price/rules/`, `api/price/formulas/`). Alle 12 `location.reload()`-Aufrufe entfallen. Toast-Feedback bleibt.

Hinweis: Die Listen sind aktuell server-seitig gerendert (Django-Template-Loop). Umstellung auf JS-Rendering der Listeninhalte aus den GET-APIs beim Tab-Wechsel und nach jeder Mutation; initiales Rendering darf weiterhin vom Server kommen, solange nach Mutationen per JS aktualisiert wird.

### b) Regel-Editor mit mehreren Bedingungen

- Bedingungs-Liste mit „+ Bedingung"-Button; jede Zeile: Feld-Dropdown, Operator-Dropdown, Wert-Input, Entfernen-Button.
- Alle Bedingungen UND-verknüpft (entspricht `RuleEvaluator.evaluate()`). Keine OR-Logik (YAGNI).
- `condition_json` wird aus allen Zeilen gebaut.
- Beim Inline-Edit einer Regel werden bestehende Bedingungen angezeigt und sind änderbar.
- Beim Operator `in`: Hinweistext „kommagetrennt, z. B. wedding,corporate".

### c) Formel-Validierung

- „Prüfen"-Button neben dem Formel-Eingabefeld (im Anlege- und im Edit-Formular).
- Neuer Endpoint `POST /dj-admin/api/price/formula-validate/` (staff_member_required): nimmt `{expression: str}`, wertet mit `SafeFormulaEvaluator` und festen Beispielwerten aus (base=500, guests=100, hours=5, distance=30, items_total=150, package_price=400), gibt `{valid: true, result: <Zahl>}` oder `{valid: false, error: <Meldung>}` zurück.
- Frontend zeigt Ergebnis bzw. Fehlermeldung direkt unter dem Feld. Testen ohne Speichern möglich.

### d) Lesbare Bedienung

- Emoji-Buttons (✏️ / 🗑️) erhalten Textbeschriftung „Bearbeiten" / „Löschen".
- Paket-Item-Checkboxlisten: scrollbarer Container (max-height) + Suchfeld, das Items per Name filtert (Suchfeld immer sichtbar, Filterung clientseitig).

## Bewusste Auslassungen

- Keine OR-Verknüpfung von Bedingungen (Engine kann nur UND; reicht laut Anforderung).
- Kein Template-Split / keine Neugestaltung (Ansatz B verworfen).
- Kundenansicht /buchen/ und Angebots-Erstellung sind nicht Teil dieses Scopes.

## Verifikation

Kein Test-Framework. Browser-Tests:
1. Canvas: Block per Klick hinzufügen, mit ▲▼ umsortieren, mit ✕ entfernen, speichern, Workflow testen.
2. Regel mit 2+ Bedingungen anlegen, speichern, erneut bearbeiten (Bedingungen sichtbar und änderbar), Wirkung per Test-Panel prüfen.
3. Formel „Prüfen" mit gültiger und ungültiger Expression.
4. Nach jedem Speichern: kein Seiten-Reload, Liste aktuell, Tab bleibt aktiv.
5. `manage.py check` + Server-Log fehlerfrei.
