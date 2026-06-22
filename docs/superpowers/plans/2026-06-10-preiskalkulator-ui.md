# Preiskalkulator-UI Überarbeitung — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Den Preiskalkulator-Workflow-Builder (workflow_builder.html) auf Klick-Bedienung umstellen, alle 12 `location.reload()` entfernen, Regel-Editor um Mehrfach-Bedingungen erweitern, Formel-Validierung ergänzen und Buttons lesbar machen.

**Architecture:** Alle Änderungen sind auf eine Datei konzentriert (`app/templates/dj_admin/workflow_builder.html`, ~860 Zeilen) plus einen neuen Backend-Endpoint in `admin_views.py` + `admin_urls.py`. Kein Backend-Umbau — PriceEngine, CRUD-APIs und Test-Panel bleiben unverändert.

**Tech Stack:** Django Template, Vanilla JS (kein Framework), bestehende GET-APIs `/dj-admin/api/price/{items,packages,rules,formulas}/`, `SafeFormulaEvaluator` aus `price_engine.py`.

---

## Datei-Übersicht

| Datei | Aktion | Verantwortung |
|---|---|---|
| `app/templates/dj_admin/workflow_builder.html` | Modifizieren | Alle 4 UI-Änderungen |
| `app/wishlist/admin_views.py` | Modifizieren | Neuer `api_formula_validate`-Endpoint |
| `app/wishlist/admin_urls.py` | Modifizieren | URL für `api_formula_validate` registrieren |

---

## Task 1: DnD durch Klick-Bedienung ersetzen (Canvas)

**Files:**
- Modify: `app/templates/dj_admin/workflow_builder.html:10-13,59-87,506-614`

### Kontext

Die Palette-Blöcke (Zeilen 59–87) haben `draggable="true"` und die `renderSteps()`-Funktion (Z. 545–613) baut jeden Canvas-Block ebenfalls mit `draggable`. Der DnD-Code umfasst: `dragstart`-Handler auf Palette, `dragover`/`dragleave`/`drop` auf Canvas, `dragstart`/`dragover`/`drop` auf jedem `.wb-step`. Das alles fällt weg.

Neu: Palette-Blöcke sind Buttons (onclick → `addStep(type)`). Canvas-Blöcke erhalten drei Buttons rechts: ▲ ▼ ✕.

- [ ] **Schritt 1: Palette-Blöcke zu Buttons umschreiben**

Ersetze in `workflow_builder.html` die sechs `.wb-block`-Divs (Zeilen 59–86) durch `<button>`-Elemente mit `onclick`. CSS-Klasse `wb-block` bleibt, `draggable="true"` entfällt, `cursor:grab` → `cursor:pointer`.

Vorher (Muster):
```html
<div class="wb-block" draggable="true" data-type="package">
  ...
</div>
```

Nachher:
```html
<button type="button" class="wb-block" onclick="addStep('package')">
  ...
</button>
```

Alle sechs Palette-Blöcke so anpassen (package, items, offer, rules, formula, discount). Außerdem in der `<style>`-Sektion (Z. 10): `.wb-block{...cursor:grab...}` → `cursor:pointer`.

- [ ] **Schritt 2: DnD-Event-Listener aus JS entfernen**

Entferne in `workflow_builder.html` den Block Zeilen 506–518 vollständig:
```javascript
// Drag from palette
document.querySelectorAll('.wb-block[draggable]').forEach(function(b){
  b.addEventListener('dragstart',...);
});
var canvas=document.getElementById('wf-canvas');
canvas.addEventListener('dragover',...);
canvas.addEventListener('dragleave',...);
canvas.addEventListener('drop',...);
```

- [ ] **Schritt 3: renderSteps() — draggable + DnD-Handler entfernen, ▲▼✕ einbauen**

In `renderSteps()` (Z. 545–613):

1. `el.draggable=true;` entfernen (Z. 555)
2. Die drei `el.addEventListener('dragstart'...)`, `el.addEventListener('dragover'...)`, `el.addEventListener('drop'...)` (Z. 593–613) entfernen
3. Den `el.querySelectorAll('select,input').forEach(...)` mousedown/dragstart-Guard (Z. 589–592) entfernen
4. Das `wb-rm`-Span in `el.innerHTML` (Z. 587) durch drei Buttons ersetzen:

```javascript
var isFirst = i === 0;
var isLast  = i === steps.length - 1;
var ctrlHtml =
  '<span style="display:flex;gap:.25rem;flex-shrink:0">'
  + '<button type="button" onclick="moveStep('+i+',-1)" '
  + (isFirst ? 'disabled style="opacity:.3;cursor:not-allowed"' : '')
  + ' title="Nach oben" style="background:none;border:none;cursor:pointer;padding:.1rem .3rem;font-size:.9rem;color:var(--text)">&#9650;</button>'
  + '<button type="button" onclick="moveStep('+i+',1)" '
  + (isLast ? 'disabled style="opacity:.3;cursor:not-allowed"' : '')
  + ' title="Nach unten" style="background:none;border:none;cursor:pointer;padding:.1rem .3rem;font-size:.9rem;color:var(--text)">&#9660;</button>'
  + '<button type="button" onclick="removeStep('+i+')" title="Entfernen" '
  + 'style="background:none;border:none;cursor:pointer;padding:.1rem .3rem;font-size:.9rem;color:var(--muted)" '
  + 'onmouseover="this.style.color=\'var(--danger)\'" onmouseout="this.style.color=\'var(--muted)\'">&times;</button>'
  + '</span>';
el.innerHTML = '<span class="wb-num">'+(i+1)+'</span>'
  + '<span class="wb-label">'+s.label+detailHtml+configHtml+'</span>'
  + ctrlHtml;
```

- [ ] **Schritt 4: moveStep()-Funktion hinzufügen**

Nach der Zeile `function removeStep(i){steps.splice(i,1);renderSteps()}` einfügen:

```javascript
function moveStep(i, dir) {
  var j = i + dir;
  if (j < 0 || j >= steps.length) return;
  var tmp = steps[i]; steps[i] = steps[j]; steps[j] = tmp;
  renderSteps();
}
```

- [ ] **Schritt 5: CSS für .wb-step — cursor:grab entfernen**

In der `<style>`-Sektion (Z. 18): `.wb-step{...cursor:grab...}` → `cursor:default`.

- [ ] **Schritt 6: Kurzanleitung-Text anpassen**

Im `.wb-guide-body` (Z. 93–110) die drei Absätze über Drag & Drop ersetzen:

```html
<strong>So funktioniert der Preis-Workflow:</strong><br><br>
1. <strong>Block hinzufügen</strong> — Klicke auf einen Berechnungs-Block links, um ihn ans Ende des Workflows anzuhängen.<br><br>
2. <strong>Reihenfolge bestimmt Berechnung</strong> — Blöcke werden von oben nach unten abgearbeitet. Jeder Block addiert seinen Betrag auf die bisherige Summe.<br><br>
3. <strong>Umsortieren</strong> — ▲ / ▼ verschieben einen Block nach oben oder unten. ✕ entfernt ihn.<br><br>
4. <strong>Als Standard setzen</strong> — Ein Standard-Workflow wird automatisch bei neuen Events und auf der Buchungsseite verwendet.<br><br>
5. <strong>Testen</strong> — Mit dem Test-Button eine Probeberechnung starten.<br><br>
```

- [ ] **Schritt 7: Canvas-Leer-Text anpassen**

In Z. 130 den Text im `wb-empty`-Div aktualisieren:

```html
<div class="wb-empty" id="wf-empty">Klicke auf einen Block links, um ihn zum Workflow hinzuzufügen.<br><span style="font-size:.72rem">Die Reihenfolge bestimmt die Berechnung — von oben nach unten.</span></div>
```

- [ ] **Schritt 8: Drag-over CSS-Regel entfernen**

In `<style>`: `.wb-canvas.drag-over{...}` entfernen (Z. 17). Die Klasse wird nicht mehr gesetzt.

- [ ] **Schritt 9: Manuell im Browser testen**

Server läuft auf Port 8500. Im Browser:
1. `/dj-admin/workflow/` öffnen
2. Auf „Paket"-Block klicken → erscheint im Canvas
3. Zweiten Block hinzufügen → ▲ disabled beim ersten, ▼ disabled beim letzten
4. ▲ klicken → Reihenfolge tauscht sich
5. ✕ klicken → Block entfernt sich
6. Workflow speichern → kein Fehler im Console

- [ ] **Schritt 10: Commit**

```bash
git add app/templates/dj_admin/workflow_builder.html
git commit -m "feat: replace DnD with click-based workflow canvas (▲▼✕)"
```

---

## Task 2: Kein Vollseiten-Reload — JS-Listen-Rendering

**Files:**
- Modify: `app/templates/dj_admin/workflow_builder.html:183-471,730-858`

### Kontext

Aktuell rufen alle 12 Stellen nach einem CRUD-Erfolg `location.reload()` auf:
- Z. 741: `saveItem()` → `location.reload()`
- Z. 743: `deleteItem()` → `location.reload()`
- Z. 755: `savePkg()` → `location.reload()`
- Z. 757: `deletePkg()` → `location.reload()`
- Z. 773: `saveRule()` → `location.reload()`
- Z. 775: `deleteRule()` → `location.reload()`
- Z. 783: `saveFormula()` → `location.reload()`
- Z. 785: `deleteFormula()` → `location.reload()`
- Z. 834: `saveEditItem()` → `location.reload()`
- Z. 844: `saveEditPkg()` → `location.reload()`
- Z. 851: `saveEditRule()` → `location.reload()`
- Z. 856: `saveEditFormula()` → `location.reload()`

Ersatz: Nach jeder Mutation die entsprechende Liste per GET-API frisch laden und im DOM ersetzen.

GET-API Responses:
- `GET /dj-admin/api/price/items/` → `{items: [{id, name, category, price, is_default, is_required, is_public}]}`
- `GET /dj-admin/api/price/packages/` → `{packages: [{id, name, description, base_price, included_item_ids, badge_label, is_active}]}`
- `GET /dj-admin/api/price/rules/` → `{rules: [{id, name, description, condition_json, effect_type, effect_value, is_active, sort_order}]}`
- `GET /dj-admin/api/price/formulas/` → `{formulas: [{id, name, expression, description, is_active}]}`

### Plan

- [ ] **Schritt 1: Tab-Merkung einbauen**

Oberhalb von `function toggleForm(id)` eine Variable ergänzen:

```javascript
var activeTab = 'comp-items'; // merkt den aktiven Tab
```

In `switchCompTab()` ergänzen:

```javascript
function switchCompTab(btn, id) {
  document.querySelectorAll('.wb-comp-tab').forEach(function(t){t.classList.remove('active')});
  document.querySelectorAll('.wb-comp-panel').forEach(function(p){p.classList.remove('active')});
  btn.classList.add('active');
  document.getElementById(id).classList.add('active');
  activeTab = id;
  refreshTab(id); // Beim Tab-Wechsel frisch laden
}
```

- [ ] **Schritt 2: refreshTab()-Dispatcher hinzufügen**

Nach `switchCompTab`:

```javascript
function refreshTab(tabId) {
  if (tabId === 'comp-items')    refreshItems();
  if (tabId === 'comp-packages') refreshPackages();
  if (tabId === 'comp-rules')    refreshRules();
  if (tabId === 'comp-formulas') refreshFormulas();
}
```

- [ ] **Schritt 3: refreshItems() implementieren**

Füge folgende Funktion ein (nach `refreshTab`):

```javascript
var CATEGORY_LABELS = {base:'Grundpreis',tech:'Technik',equipment:'Ausrüstung',travel:'Anfahrt',extra:'Extras',service:'Service'};

function refreshItems() {
  pFetch('api/price/items/', 'GET').then(function(data) {
    var tbody = document.querySelector('#comp-items table tbody');
    if (!tbody) return; // Noch keine Items → Tabelle nicht im DOM
    tbody.innerHTML = '';
    data.items.forEach(function(item) {
      var tr = document.createElement('tr');
      tr.id = 'item-row-' + item.id;
      tr.innerHTML =
        '<td>'
          + '<span class="item-display">' + escHtml(item.name) + '</span>'
          + '<span class="item-edit" style="display:none">'
            + '<input type="text" data-field="name" class="form-input" value="' + escHtml(item.name) + '" style="margin-bottom:.2rem">'
          + '</span>'
        + '</td>'
        + '<td>'
          + '<span class="item-display"><span class="badge badge-inquiry">' + (CATEGORY_LABELS[item.category] || item.category) + '</span></span>'
          + '<span class="item-edit" style="display:none"><select data-field="category" class="form-input" style="appearance:auto">'
            + Object.entries(CATEGORY_LABELS).map(function(e){return '<option value="'+e[0]+'"'+(item.category===e[0]?' selected':'')+'>'+e[1]+'</option>'}).join('')
          + '</select></span>'
        + '</td>'
        + '<td>'
          + '<span class="item-display" style="font-family:\'Space Mono\',monospace">' + item.price.toFixed(2) + ' €</span>'
          + '<span class="item-edit" style="display:none"><input type="number" step="0.01" data-field="price" class="form-input" value="' + item.price + '" style="max-width:90px"></span>'
        + '</td>'
        + '<td><span class="item-display">' + (item.is_default ? '&#10003;' : '') + '</span>'
          + '<span class="item-edit" style="display:none"><input type="checkbox" data-field="is_default"' + (item.is_default ? ' checked' : '') + '></span></td>'
        + '<td><span class="item-display">' + (item.is_required ? '&#10003;' : '') + '</span>'
          + '<span class="item-edit" style="display:none"><input type="checkbox" data-field="is_required"' + (item.is_required ? ' checked' : '') + '></span></td>'
        + '<td><span class="item-display">' + (item.is_public ? '&#10003;' : '—') + '</span>'
          + '<span class="item-edit" style="display:none"><input type="checkbox" data-field="is_public"' + (item.is_public ? ' checked' : '') + '></span></td>'
        + '<td style="white-space:nowrap">'
          + '<button type="button" class="btn btn-ghost btn-sm item-edit-btn" onclick="toggleEditRow(\'item\',' + item.id + ')">Bearbeiten</button>'
          + '<button type="button" class="btn btn-primary btn-sm item-save-btn" style="display:none" onclick="saveEditItem(' + item.id + ')">&#10003;</button>'
          + '<button type="button" class="btn btn-ghost btn-sm item-cancel-btn" style="display:none" onclick="cancelEditRow(\'item\',' + item.id + ')">&#10005;</button>'
          + '<button type="button" class="btn btn-danger btn-sm" onclick="deleteItem(' + item.id + ')">Löschen</button>'
        + '</td>';
      tbody.appendChild(tr);
    });
    // Leermeldung ein-/ausblenden
    var empty = document.querySelector('#comp-items .wb-comp-empty');
    if (empty) empty.style.display = data.items.length ? 'none' : '';
  }).catch(function(){});
}
```

- [ ] **Schritt 4: escHtml-Hilfsfunktion hinzufügen**

Vor `refreshItems()` einfügen:

```javascript
function escHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
```

- [ ] **Schritt 5: refreshPackages() implementieren**

```javascript
function refreshPackages() {
  pFetch('api/price/packages/', 'GET').then(function(data) {
    var list = document.getElementById('pkg-list');
    if (!list) return;
    list.innerHTML = '';
    if (!data.packages.length) {
      list.innerHTML = '<div class="wb-comp-empty">Noch keine Pakete.</div>';
      return;
    }
    data.packages.forEach(function(pkg) {
      var row = document.createElement('div');
      row.className = 'wb-comp-row';
      row.id = 'pkg-row-' + pkg.id;
      var badgeHtml = pkg.badge_label ? ' <span class="badge badge-inquiry">' + escHtml(pkg.badge_label) + '</span>' : '';
      row.innerHTML =
        '<div style="flex:1">'
          + '<span class="pkg-display"><strong>' + escHtml(pkg.name) + '</strong>' + badgeHtml
            + '<br><span style="font-size:.68rem;color:var(--muted)">' + (pkg.description ? escHtml(pkg.description).split(' ').slice(0,12).join(' ') : '—') + ' · inkl. ' + (pkg.included_item_ids ? pkg.included_item_ids.length : 0) + ' Posten</span>'
          + '</span>'
          + '<div class="pkg-edit" style="display:none">'
            + '<div style="display:flex;gap:.4rem;margin-bottom:.3rem;flex-wrap:wrap">'
              + '<input type="text" data-field="name" class="form-input" value="' + escHtml(pkg.name) + '" style="flex:1;min-width:120px">'
              + '<input type="number" step="0.01" data-field="base_price" class="form-input" value="' + pkg.base_price + '" style="width:100px">'
              + '<input type="text" data-field="badge_label" class="form-input" value="' + escHtml(pkg.badge_label || '') + '" style="width:100px" placeholder="Badge">'
            + '</div>'
            + '<input type="text" data-field="description" class="form-input" value="' + escHtml(pkg.description || '') + '" placeholder="Beschreibung" style="margin-bottom:.3rem">'
            + '<div id="pkg-edit-items-' + pkg.id + '" style="display:flex;flex-wrap:wrap;gap:.3rem"></div>'
          + '</div>'
        + '</div>'
        + '<span class="pkg-display" style="font-family:\'Space Mono\',monospace;font-size:.85rem;color:var(--accent)">' + pkg.base_price.toFixed(2) + ' €</span>'
        + '<span style="font-size:.68rem;color:var(--muted)">' + (pkg.is_active ? 'aktiv' : 'inaktiv') + '</span>'
        + '<button type="button" class="btn btn-ghost btn-sm pkg-edit-btn" onclick="toggleEditComp(\'pkg\',' + pkg.id + ')">Bearbeiten</button>'
        + '<button type="button" class="btn btn-primary btn-sm pkg-save-btn" style="display:none" onclick="saveEditPkg(' + pkg.id + ')">&#10003;</button>'
        + '<button type="button" class="btn btn-ghost btn-sm pkg-cancel-btn" style="display:none" onclick="cancelEditComp(\'pkg\',' + pkg.id + ')">&#10005;</button>'
        + '<button type="button" class="btn btn-danger btn-sm" onclick="deletePkg(' + pkg.id + ')">Löschen</button>';
      list.appendChild(row);
      // Item-Checkboxen für Edit befüllen
      pFetch('api/price/items/', 'GET').then(function(itemData) {
        var container = document.getElementById('pkg-edit-items-' + pkg.id);
        if (!container) return;
        container.innerHTML = itemData.items.map(function(item) {
          var checked = (pkg.included_item_ids || []).indexOf(item.id) !== -1;
          return '<label style="font-size:.72rem;display:flex;align-items:center;gap:.2rem;padding:.15rem .4rem;background:var(--card);border-radius:4px;cursor:pointer">'
            + '<input type="checkbox" class="pkg-edit-item-' + pkg.id + '" value="' + item.id + '"' + (checked ? ' checked' : '') + '> ' + escHtml(item.name)
            + '</label>';
        }).join('');
      }).catch(function(){});
    });
  }).catch(function(){});
}
```

- [ ] **Schritt 6: refreshRules() implementieren**

```javascript
var EFFECT_LABELS = {percent_add:'Prozent-Aufschlag',flat_add:'Pauschale hinzufügen',flat_set:'Pauschale setzen'};

function refreshRules() {
  pFetch('api/price/rules/', 'GET').then(function(data) {
    var list = document.getElementById('rule-list');
    if (!list) return;
    list.innerHTML = '';
    if (!data.rules.length) {
      list.innerHTML = '<div class="wb-comp-empty">Noch keine Regeln.</div>';
      return;
    }
    data.rules.forEach(function(rule) {
      var row = document.createElement('div');
      row.className = 'wb-comp-row';
      row.id = 'rule-row-' + rule.id;
      var effLabel = EFFECT_LABELS[rule.effect_type] || rule.effect_type;
      var effSuffix = rule.effect_type === 'percent_add' ? '%' : ' €';
      row.innerHTML =
        '<div style="flex:1">'
          + '<span class="rule-display"><strong>' + escHtml(rule.name) + '</strong>'
            + '<br><span style="font-size:.68rem;color:var(--muted)">' + (rule.description || '—') + ' · ' + effLabel + ': ' + rule.effect_value + effSuffix + '</span>'
          + '</span>'
          + '<div class="rule-edit" style="display:none">'
            + '<div style="display:flex;gap:.4rem;margin-bottom:.3rem;flex-wrap:wrap">'
              + '<input type="text" data-field="name" class="form-input" value="' + escHtml(rule.name) + '" style="flex:1;min-width:120px">'
              + '<input type="text" data-field="description" class="form-input" value="' + escHtml(rule.description || '') + '" style="flex:1;min-width:120px">'
            + '</div>'
            + '<div style="display:flex;gap:.4rem;flex-wrap:wrap">'
              + '<select data-field="effect_type" class="form-input" style="appearance:auto;width:auto">'
                + Object.entries(EFFECT_LABELS).map(function(e){return '<option value="'+e[0]+'"'+(rule.effect_type===e[0]?' selected':'')+'>'+e[1]+'</option>'}).join('')
              + '</select>'
              + '<input type="number" step="0.01" data-field="effect_value" class="form-input" value="' + rule.effect_value + '" style="width:80px">'
              + '<input type="number" data-field="sort_order" class="form-input" value="' + rule.sort_order + '" style="width:60px">'
              + '<label style="font-size:.72rem;display:flex;align-items:center;gap:.2rem"><input type="checkbox" data-field="is_active"' + (rule.is_active ? ' checked' : '') + '> Aktiv</label>'
            + '</div>'
            + '<div id="rule-edit-conds-' + rule.id + '" style="margin-top:.4rem"></div>'
          + '</div>'
        + '</div>'
        + '<span class="rule-display" style="font-size:.68rem;color:var(--muted)">' + (rule.is_active ? 'aktiv' : 'inaktiv') + '</span>'
        + '<button type="button" class="btn btn-ghost btn-sm rule-edit-btn" onclick="toggleEditComp(\'rule\',' + rule.id + ')">Bearbeiten</button>'
        + '<button type="button" class="btn btn-primary btn-sm rule-save-btn" style="display:none" onclick="saveEditRule(' + rule.id + ')">&#10003;</button>'
        + '<button type="button" class="btn btn-ghost btn-sm rule-cancel-btn" style="display:none" onclick="cancelEditComp(\'rule\',' + rule.id + ')">&#10005;</button>'
        + '<button type="button" class="btn btn-danger btn-sm" onclick="deleteRule(' + rule.id + ')">Löschen</button>';
      list.appendChild(row);
    });
  }).catch(function(){});
}
```

- [ ] **Schritt 7: refreshFormulas() implementieren**

```javascript
function refreshFormulas() {
  pFetch('api/price/formulas/', 'GET').then(function(data) {
    var list = document.getElementById('formula-list');
    if (!list) return;
    list.innerHTML = '';
    if (!data.formulas.length) {
      list.innerHTML = '<div class="wb-comp-empty">Noch keine Formeln.</div>';
      return;
    }
    data.formulas.forEach(function(f) {
      var row = document.createElement('div');
      row.className = 'wb-comp-row';
      row.id = 'formula-row-' + f.id;
      row.innerHTML =
        '<div style="flex:1">'
          + '<span class="formula-display"><strong>' + escHtml(f.name) + '</strong>'
            + '<br><code style="font-size:.7rem;color:var(--accent)">' + escHtml(f.expression) + '</code>'
            + (f.description ? '<br><span style="font-size:.68rem;color:var(--muted)">' + escHtml(f.description) + '</span>' : '')
          + '</span>'
          + '<div class="formula-edit" style="display:none">'
            + '<input type="text" data-field="name" class="form-input" value="' + escHtml(f.name) + '" placeholder="Name" style="margin-bottom:.2rem">'
            + '<div style="display:flex;gap:.4rem;align-items:center;margin-bottom:.2rem">'
              + '<input type="text" data-field="expression" class="form-input" value="' + escHtml(f.expression) + '" placeholder="Formel" style="font-family:\'Space Mono\',monospace;flex:1">'
              + '<button type="button" class="btn btn-sm" style="white-space:nowrap;background:var(--surface);border:1px solid var(--border)" onclick="validateFormula(this)">Prüfen</button>'
            + '</div>'
            + '<div class="formula-validate-msg" style="font-size:.72rem;margin-bottom:.2rem"></div>'
            + '<input type="text" data-field="description" class="form-input" value="' + escHtml(f.description || '') + '" placeholder="Beschreibung">'
          + '</div>'
        + '</div>'
        + '<button type="button" class="btn btn-ghost btn-sm formula-edit-btn" onclick="toggleEditComp(\'formula\',' + f.id + ')">Bearbeiten</button>'
        + '<button type="button" class="btn btn-primary btn-sm formula-save-btn" style="display:none" onclick="saveEditFormula(' + f.id + ')">&#10003;</button>'
        + '<button type="button" class="btn btn-ghost btn-sm formula-cancel-btn" style="display:none" onclick="cancelEditComp(\'formula\',' + f.id + ')">&#10005;</button>'
        + '<button type="button" class="btn btn-danger btn-sm" onclick="deleteFormula(' + f.id + ')">Löschen</button>';
      list.appendChild(row);
    });
  }).catch(function(){});
}
```

- [ ] **Schritt 8: Alle 12 location.reload() entfernen, refreshTab(activeTab) einsetzen**

Ersetze in den CRUD-Funktionen alle `location.reload()` durch `refreshTab(activeTab)`:

| Funktion | Vorher | Nachher |
|---|---|---|
| `saveItem()` Z. 741 | `showToast('Posten erstellt');location.reload()` | `showToast('Posten erstellt');refreshTab(activeTab)` |
| `deleteItem()` Z. 743 | `showToast('Gelöscht');location.reload()` | `showToast('Gelöscht');refreshTab(activeTab)` |
| `savePkg()` Z. 755 | `showToast('Paket erstellt');location.reload()` | `showToast('Paket erstellt');refreshTab(activeTab)` |
| `deletePkg()` Z. 757 | `showToast('Gelöscht');location.reload()` | `showToast('Gelöscht');refreshTab(activeTab)` |
| `saveRule()` Z. 773 | `showToast('Regel erstellt');location.reload()` | `showToast('Regel erstellt');refreshTab(activeTab)` |
| `deleteRule()` Z. 775 | `showToast('Gelöscht');location.reload()` | `showToast('Gelöscht');refreshTab(activeTab)` |
| `saveFormula()` Z. 783 | `showToast('Formel erstellt');location.reload()` | `showToast('Formel erstellt');refreshTab(activeTab)` |
| `deleteFormula()` Z. 785 | `showToast('Gelöscht');location.reload()` | `showToast('Gelöscht');refreshTab(activeTab)` |
| `saveEditItem()` Z. 834 | `showToast('Posten gespeichert');location.reload()` | `showToast('Posten gespeichert');refreshTab(activeTab)` |
| `saveEditPkg()` Z. 844 | `showToast('Paket gespeichert');location.reload()` | `showToast('Paket gespeichert');refreshTab(activeTab)` |
| `saveEditRule()` Z. 851 | `showToast('Regel gespeichert');location.reload()` | `showToast('Regel gespeichert');refreshTab(activeTab)` |
| `saveEditFormula()` Z. 856 | `showToast('Formel gespeichert');location.reload()` | `showToast('Formel gespeichert');refreshTab(activeTab)` |

Außerdem in `saveItem()`, `savePkg()`, `saveRule()`, `saveFormula()` nach dem Toast-Aufruf das Formular schließen: `toggleForm('item-add')` / `toggleForm('pkg-add')` / `toggleForm('rule-add')` / `toggleForm('formula-add')`.

- [ ] **Schritt 9: Manuell im Browser testen**

1. Preis-Posten-Tab: Neuen Posten anlegen → Kein Reload, Tabelle aktualisiert sich
2. Tab wechseln zu Pakete, zurück → Liste neu geladen
3. Posten bearbeiten (Inline-Edit) → Kein Reload
4. Posten löschen → Kein Reload, verschwindet aus Tabelle
5. Gleiche Tests für Pakete, Regeln, Formeln

- [ ] **Schritt 10: Commit**

```bash
git add app/templates/dj_admin/workflow_builder.html
git commit -m "feat: replace location.reload() with JS list re-rendering in component tabs"
```

---

## Task 3: Regel-Editor mit mehreren Bedingungen

**Files:**
- Modify: `app/templates/dj_admin/workflow_builder.html:328-375,760-774,848-852`

### Kontext

Das aktuelle Anlege-Formular für Regeln (Z. 328–375) hat genau eine Bedingung (Feld, Operator, Wert). `saveRule()` (Z. 760–774) baut `condition_json` mit genau einem Element: `[{field:..., op:..., value:...}]`. Das Backend (`RuleEvaluator.evaluate()`) akzeptiert bereits beliebig viele Bedingungen im Array (UND-verknüpft).

Das Inline-Edit einer Regel (Z. 384–399) zeigt keine Bedingungen. Der neue Editor braucht:
- Eine dynamische Liste von Bedingungszeilen
- „+ Bedingung"-Button
- Beim Inline-Edit: bestehende `condition_json` aus API anzeigen

- [ ] **Schritt 1: Feld/Operator/Wert-Optionen als JS-Konstanten definieren**

Nach `var EFFECT_LABELS = ...` einfügen:

```javascript
var COND_FIELDS = [
  {v:'guest_count', l:'Gästeanzahl'},
  {v:'duration_hours', l:'Dauer (Std.)'},
  {v:'date_weekday', l:'Wochentag (0=Mo … 6=So)'},
  {v:'event_type', l:'Event-Typ'},
  {v:'distance_km', l:'Entfernung (km)'},
  {v:'date_month', l:'Monat (1–12)'}
];
var COND_OPS = [
  {v:'gt',l:'größer als'},{v:'gte',l:'größer/gleich'},{v:'lt',l:'kleiner als'},
  {v:'lte',l:'kleiner/gleich'},{v:'eq',l:'gleich'},{v:'neq',l:'ungleich'},
  {v:'in',l:'in Liste (kommagetrennt)'},{v:'is_weekend',l:'ist Wochenende'}
];
```

- [ ] **Schritt 2: buildCondRow() und addCondRow() hinzufügen**

```javascript
function buildCondRow(containerId, cond) {
  // cond ist optional: {field, op, value}
  var fieldSel = '<select class="form-input cond-field" style="appearance:auto">'
    + COND_FIELDS.map(function(f){return '<option value="'+f.v+'"'+(cond&&cond.field===f.v?' selected':'')+'>'+f.l+'</option>'}).join('')
    + '</select>';
  var opSel = '<select class="form-input cond-op" style="appearance:auto" onchange="updateCondHint(this)">'
    + COND_OPS.map(function(o){return '<option value="'+o.v+'"'+(cond&&cond.op===o.v?' selected':'')+'>'+o.l+'</option>'}).join('')
    + '</select>';
  var val = cond ? (Array.isArray(cond.value) ? cond.value.join(',') : String(cond.value||'')) : '';
  var inHint = (cond && cond.op === 'in') ? '<span class="cond-in-hint" style="font-size:.68rem;color:var(--muted)">kommagetrennt, z.B. wedding,corporate</span>' : '<span class="cond-in-hint" style="display:none;font-size:.68rem;color:var(--muted)">kommagetrennt, z.B. wedding,corporate</span>';
  var row = document.createElement('div');
  row.className = 'cond-row';
  row.style.cssText = 'display:flex;gap:.4rem;align-items:flex-start;margin-bottom:.3rem;flex-wrap:wrap';
  row.innerHTML = fieldSel + opSel
    + '<div style="flex:1;min-width:80px"><input type="text" class="form-input cond-val" value="' + escHtml(val) + '" placeholder="Wert">' + inHint + '</div>'
    + '<button type="button" style="background:none;border:none;cursor:pointer;color:var(--muted);font-size:1rem;padding:.2rem .4rem" onclick="this.closest(\'.cond-row\').remove()">&times;</button>';
  document.getElementById(containerId).appendChild(row);
}

function addCondRow(containerId) {
  buildCondRow(containerId, null);
}

function updateCondHint(sel) {
  var hint = sel.closest('.cond-row').querySelector('.cond-in-hint');
  hint.style.display = sel.value === 'in' ? '' : 'none';
}

function collectConditions(containerId) {
  var rows = document.getElementById(containerId).querySelectorAll('.cond-row');
  var result = [];
  rows.forEach(function(row) {
    var field = row.querySelector('.cond-field').value;
    var op    = row.querySelector('.cond-op').value;
    var raw   = row.querySelector('.cond-val').value.trim();
    var parsedVal = raw;
    if (op === 'in') parsedVal = raw.split(',').map(function(v){return isNaN(v.trim())?v.trim():parseFloat(v.trim())});
    else if (op !== 'is_weekend') parsedVal = isNaN(raw) ? raw : parseFloat(raw);
    result.push({field: field, op: op, value: parsedVal});
  });
  return result;
}
```

- [ ] **Schritt 3: Anlege-Formular für Regeln umbauen**

Den Block `BEDINGUNG` im `#rule-add`-Formular (Z. 333–357) ersetzen:

Vorher:
```html
<div style="font-size:.72rem;color:var(--muted);margin-bottom:.5rem;font-family:'Space Mono',monospace">BEDINGUNG — Wann greift diese Regel?</div>
<div class="form-row-3">
  <div class="form-group"><label class="form-label">Feld</label>
    <select id="rule-cond-field" ...>...</select>
  </div>
  <div class="form-group"><label class="form-label">Operator</label>
    <select id="rule-cond-op" ...>...</select>
  </div>
  <div class="form-group"><label class="form-label">Wert</label><input type="text" id="rule-cond-val" ...></div>
</div>
```

Nachher:
```html
<div style="font-size:.72rem;color:var(--muted);margin-bottom:.5rem;font-family:'Space Mono',monospace">BEDINGUNGEN (UND-verknüpft) — Wann greift diese Regel?</div>
<div id="rule-add-conds"></div>
<button type="button" class="btn btn-ghost btn-sm" style="margin-bottom:.75rem" onclick="addCondRow('rule-add-conds')">+ Bedingung</button>
```

Außerdem `saveRule()` anpassen, um `collectConditions('rule-add-conds')` zu nutzen:

Vorher in `saveRule()` (Z. 760–774):
```javascript
function saveRule(){
  var val=document.getElementById('rule-cond-val').value.trim();
  var op=document.getElementById('rule-cond-op').value;
  var parsedVal=val;
  if(op==='in') parsedVal=val.split(',').map(...);
  else if(op!=='is_weekend') parsedVal=isNaN(val)?val:parseFloat(val);
  pFetch('api/price/rules/','POST',{
    ...
    condition_json:[{field:document.getElementById('rule-cond-field').value,op:op,value:parsedVal}],
    ...
  }).then(function(d){if(d.success){showToast('Regel erstellt');location.reload()}...});
}
```

Nachher:
```javascript
function saveRule(){
  var conds = collectConditions('rule-add-conds');
  if (!conds.length) { showToast('Mindestens eine Bedingung erforderlich', true); return; }
  pFetch('api/price/rules/','POST',{
    name:document.getElementById('rule-name').value,
    description:document.getElementById('rule-desc').value,
    condition_json: conds,
    effect_type:document.getElementById('rule-effect-type').value,
    effect_value:parseFloat(document.getElementById('rule-effect-val').value)||0,
    sort_order:parseInt(document.getElementById('rule-sort').value)||0,
  }).then(function(d){
    if(d.success){
      showToast('Regel erstellt');
      document.getElementById('rule-add-conds').innerHTML = '';
      toggleForm('rule-add');
      refreshTab(activeTab);
    } else showToast(d.error||'Fehler',true);
  });
}
```

Außerdem: Beim Öffnen von `#rule-add` eine leere Bedingungszeile vor-befüllen. In `toggleForm()` oder per `onclick` des `+ Neue Regel`-Buttons:

Den Button in Z. 325 anpassen:
```html
<button type="button" class="btn btn-primary btn-sm" onclick="toggleForm('rule-add');if(document.getElementById('rule-add').style.display==='block'&&!document.getElementById('rule-add-conds').children.length){addCondRow('rule-add-conds')}">+ Neue Regel</button>
```

- [ ] **Schritt 4: Inline-Edit für Regeln mit Bedingungen erweitern**

In `refreshRules()` (Task 2, Schritt 6) nach dem `row.innerHTML`-Zuweisungsblock, im Abschnitt für die `rule-edit-conds-{id}`, die bestehenden Bedingungen rendern:

Ergänze nach `list.appendChild(row)`:

```javascript
// Bestehende Bedingungen in Edit-Ansicht laden
var condContainer = document.getElementById('rule-edit-conds-' + rule.id);
if (condContainer) {
  condContainer.innerHTML = '<div style="font-size:.68rem;color:var(--muted);margin-bottom:.3rem;font-family:\'Space Mono\',monospace">BEDINGUNGEN (UND-verknüpft)</div>';
  var conds = rule.condition_json || [];
  conds.forEach(function(c) { buildCondRow('rule-edit-conds-' + rule.id, c); });
  var addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.className = 'btn btn-ghost btn-sm';
  addBtn.style.marginTop = '.3rem';
  addBtn.textContent = '+ Bedingung';
  addBtn.onclick = function() { addCondRow('rule-edit-conds-' + rule.id); };
  condContainer.appendChild(addBtn);
}
```

- [ ] **Schritt 5: saveEditRule() anpassen um Bedingungen zu lesen**

In `saveEditRule()` (Z. 848–852) die `condition_json` aus dem Edit-Container lesen:

Vorher:
```javascript
function saveEditRule(pk){
  var data=collectFields(document.getElementById('rule-row-'+pk));
  pFetch('api/price/rules/'+pk+'/','PUT',data).then(function(d){
    if(d.success){showToast('Regel gespeichert');location.reload()}else showToast('Fehler',true);
  }).catch(function(){showToast('Fehler',true)});
}
```

Nachher:
```javascript
function saveEditRule(pk){
  var data = collectFields(document.getElementById('rule-row-'+pk));
  var condContainerId = 'rule-edit-conds-' + pk;
  if (document.getElementById(condContainerId)) {
    data.condition_json = collectConditions(condContainerId);
  }
  pFetch('api/price/rules/'+pk+'/','PUT',data).then(function(d){
    if(d.success){showToast('Regel gespeichert');refreshTab(activeTab)}else showToast('Fehler',true);
  }).catch(function(){showToast('Fehler',true)});
}
```

- [ ] **Schritt 6: Manuell im Browser testen**

1. `+ Neue Regel` → Formular öffnet sich, leere Bedingungszeile sichtbar
2. `+ Bedingung` → zweite Zeile erscheint
3. Beide Bedingungen ausfüllen, Effekt ausfüllen, speichern → Regel erscheint in Liste
4. `Bearbeiten` auf dieser Regel → beide Bedingungen sichtbar, editierbar
5. Bedingung entfernen (×), speichern → nur eine Bedingung bleibt
6. Im Test-Panel: Workflow mit Regel testen, prüfen ob Bedingungen greifen

- [ ] **Schritt 7: Commit**

```bash
git add app/templates/dj_admin/workflow_builder.html
git commit -m "feat: rule editor supports multiple AND-conditions"
```

---

## Task 4: Formel-Validierung (Backend + Frontend)

**Files:**
- Create: Zeilen in `app/wishlist/admin_views.py` (neue Funktion `api_formula_validate`)
- Modify: `app/wishlist/admin_urls.py`
- Modify: `app/templates/dj_admin/workflow_builder.html:421-444`

### Kontext

`SafeFormulaEvaluator` (price_engine.py Z. 30–78) wertet Ausdrücke sicher aus.
`FORMULA_VARS = {'base', 'guests', 'hours', 'distance', 'items_total', 'package_price'}` (Z. 132)

Neuer Endpoint: `POST /dj-admin/api/price/formula-validate/`
Request: `{"expression": "base + guests * 5"}`
Response OK: `{"valid": true, "result": 1450.0}`
Response Fehler: `{"valid": false, "error": "Unbekannte Variable: foo"}`

Testwerte: base=500, guests=100, hours=5, distance=30, items_total=150, package_price=400

- [ ] **Schritt 1: Backend-Endpoint api_formula_validate hinzufügen**

In `app/wishlist/admin_views.py`, nach `api_pricing_formula_detail` (nach Z. 848) einfügen:

```python
@staff_member_required
@require_POST
def api_formula_validate(request):
    from .price_engine import SafeFormulaEvaluator, FORMULA_VARS
    data = json.loads(request.body)
    expression = data.get('expression', '').strip()
    if not expression:
        return JsonResponse({'valid': False, 'error': 'Kein Ausdruck angegeben'})
    evaluator = SafeFormulaEvaluator(FORMULA_VARS)
    test_vars = {
        'base': Decimal('500'), 'guests': Decimal('100'), 'hours': Decimal('5'),
        'distance': Decimal('30'), 'items_total': Decimal('150'), 'package_price': Decimal('400'),
    }
    try:
        result = evaluator.evaluate(expression, test_vars)
        return JsonResponse({'valid': True, 'result': float(result)})
    except Exception as e:
        return JsonResponse({'valid': False, 'error': str(e)})
```

- [ ] **Schritt 2: URL registrieren**

In `app/wishlist/admin_urls.py`, nach der Formel-Zeile (Z. 52), vor `# Workflow Builder` einfügen:

```python
path('api/price/formula-validate/', admin_views.api_formula_validate, name='formula_validate'),
```

- [ ] **Schritt 3: Backend testen**

```bash
cd /home/camp/Server/Rene/dj-manage
python app/manage.py check
```
Erwartete Ausgabe: `System check identified no issues (0 silenced).`

Manuell testen (im Browser-DevTools Console oder curl):
```bash
curl -s -X POST http://100.74.102.46:8500/dj-admin/api/price/formula-validate/ \
  -H "Content-Type: application/json" \
  -H "X-CSRFToken: <token>" \
  --cookie "sessionid=<session>" \
  -d '{"expression":"base + guests * 5"}' | python3 -m json.tool
```
Erwartete Ausgabe: `{"valid": true, "result": 1000.0}` (500 + 100*5)

- [ ] **Schritt 4: „Prüfen"-Button im Anlege-Formular ergänzen**

In `#formula-add` (Z. 421–444), den Block für `formula-expr` anpassen.

Vorher:
```html
<div class="form-group">
  <label class="form-label">Formel-Ausdruck</label>
  <input type="text" id="formula-expr" class="form-input" placeholder="..." style="font-family:'Space Mono',monospace">
  <div style="font-size:.68rem;...">...</div>
</div>
<div style="display:flex;gap:.5rem">
  <button type="button" class="btn btn-primary btn-sm" onclick="saveFormula()">Speichern</button>
  <button type="button" class="btn btn-ghost btn-sm" onclick="toggleForm('formula-add')">Abbrechen</button>
</div>
```

Nachher:
```html
<div class="form-group">
  <label class="form-label">Formel-Ausdruck</label>
  <div style="display:flex;gap:.4rem;align-items:flex-start">
    <input type="text" id="formula-expr" class="form-input" placeholder="z.B. base + (guests * 5) + (hours * 50)" style="font-family:'Space Mono',monospace;flex:1">
    <button type="button" class="btn btn-sm" style="white-space:nowrap;background:var(--surface);border:1px solid var(--border)" onclick="validateFormulaAdd()">Prüfen</button>
  </div>
  <div id="formula-add-validate-msg" style="font-size:.72rem;margin-top:.25rem"></div>
  <div style="font-size:.68rem;color:var(--muted);margin-top:.35rem;line-height:1.6">
    <strong>Variablen:</strong>
    <code>base</code> = bisherige Zwischensumme,
    <code>guests</code> = Gästeanzahl,
    <code>hours</code> = Dauer in Stunden,
    <code>distance</code> = Entfernung in km,
    <code>items_total</code> = Summe aller Posten,
    <code>package_price</code> = Paketpreis<br>
    <strong>Operatoren:</strong> <code>+</code> <code>-</code> <code>*</code> <code>/</code> <code>%</code> — Vergleiche: <code>></code> <code>&lt;</code> <code>==</code><br>
    <strong>Beispiel:</strong> <code>base + (guests * 3) + (distance > 50) * 75</code>
  </div>
</div>
<div style="display:flex;gap:.5rem">
  <button type="button" class="btn btn-primary btn-sm" onclick="saveFormula()">Speichern</button>
  <button type="button" class="btn btn-ghost btn-sm" onclick="toggleForm('formula-add')">Abbrechen</button>
</div>
```

- [ ] **Schritt 5: validateFormula()-Funktion hinzufügen**

```javascript
function validateFormulaExpr(expression, msgElId) {
  var msgEl = document.getElementById(msgElId);
  if (!expression.trim()) {
    msgEl.innerHTML = '';
    return;
  }
  msgEl.innerHTML = '<span style="color:var(--muted)">Prüfe...</span>';
  pFetch('api/price/formula-validate/', 'POST', {expression: expression})
    .then(function(d) {
      if (d.valid) {
        msgEl.innerHTML = '<span style="color:var(--green,#22c55e)">&#10003; Ergebnis mit Testwerten: ' + d.result.toFixed(2) + ' € (base=500, guests=100, hours=5, distance=30)</span>';
      } else {
        msgEl.innerHTML = '<span style="color:var(--danger,#ef4444)">&#9888; ' + escHtml(d.error) + '</span>';
      }
    })
    .catch(function() {
      msgEl.innerHTML = '<span style="color:var(--danger)">Verbindungsfehler</span>';
    });
}

function validateFormulaAdd() {
  validateFormulaExpr(document.getElementById('formula-expr').value, 'formula-add-validate-msg');
}

function validateFormula(btn) {
  var row = btn.closest('.formula-edit');
  if (!row) return;
  var exprInput = row.querySelector('[data-field="expression"]');
  var msgEl = row.querySelector('.formula-validate-msg');
  if (!exprInput || !msgEl) return;
  if (!msgEl.id) msgEl.id = 'fv-' + Date.now();
  validateFormulaExpr(exprInput.value, msgEl.id);
}
```

- [ ] **Schritt 6: Manuell im Browser testen**

1. `+ Neue Formel` → Formular öffnet sich
2. `base + guests * 5` eingeben, „Prüfen" → grüne Meldung: `Ergebnis: 1000.00 €`
3. `foo + bar` eingeben, „Prüfen" → rote Meldung: `Unbekannte Variable: foo`
4. Formel speichern, dann „Bearbeiten" → Edit-Modus öffnet sich mit „Prüfen"-Button
5. In Edit-Modus „Prüfen" → gleiche Validierung

- [ ] **Schritt 7: Commit**

```bash
git add app/wishlist/admin_views.py app/wishlist/admin_urls.py app/templates/dj_admin/workflow_builder.html
git commit -m "feat: formula validation endpoint and Prüfen button in formula editor"
```

---

## Task 5: Lesbare Buttons + scrollbare Paket-Checkboxlisten

**Files:**
- Modify: `app/templates/dj_admin/workflow_builder.html:246-251,308-313,401-406,461-465,278-279`

### Kontext

Aktuell sind alle Bearbeiten/Löschen-Buttons nur Emoji-HTML-Entities (&#9998; / &#128465;). Die Paket-Item-Checkboxliste im Anlege-Formular (Z. 278–279) hat keinen Scroll und kein Suchfeld.

**Achtung:** Task 2 (refreshItems, refreshPackages usw.) hat bereits Text-Labels für die dynamisch gerenderten Zeilen erzeugt. Dieser Task fixiert nur die **server-seitig gerenderten** Zeilen, die beim initialen Seitenaufruf vorhanden sind, SOWIE die statische Paket-Checkboxliste im Anlege-Formular.

- [ ] **Schritt 1: Alle server-seitig gerenderten Emoji-Buttons durch Text ersetzen**

In der Preis-Posten-Tabelle (Z. 247–250):
```html
<!-- Vorher -->
<button type="button" class="btn btn-ghost btn-sm item-edit-btn" onclick="toggleEditRow('item',{{ item.pk }})">&#9998;</button>
...
<button type="button" class="btn btn-danger btn-sm" onclick="deleteItem({{ item.pk }})">&#128465;</button>

<!-- Nachher -->
<button type="button" class="btn btn-ghost btn-sm item-edit-btn" onclick="toggleEditRow('item',{{ item.pk }})">Bearbeiten</button>
...
<button type="button" class="btn btn-danger btn-sm" onclick="deleteItem({{ item.pk }})">Löschen</button>
```

In der Paket-Liste (Z. 309–313):
```html
<!-- Vorher -->
<button type="button" class="btn btn-ghost btn-sm pkg-edit-btn" onclick="toggleEditComp('pkg',{{ pkg.pk }})">&#9998;</button>
...
<button type="button" class="btn btn-danger btn-sm" onclick="deletePkg({{ pkg.pk }})">&#128465;</button>

<!-- Nachher -->
<button type="button" class="btn btn-ghost btn-sm pkg-edit-btn" onclick="toggleEditComp('pkg',{{ pkg.pk }})">Bearbeiten</button>
...
<button type="button" class="btn btn-danger btn-sm" onclick="deletePkg({{ pkg.pk }})">Löschen</button>
```

In der Regel-Liste (Z. 402–406):
```html
<!-- Vorher -->
<button type="button" class="btn btn-ghost btn-sm rule-edit-btn" onclick="toggleEditComp('rule',{{ rule.pk }})">&#9998;</button>
...
<button type="button" class="btn btn-danger btn-sm" onclick="deleteRule({{ rule.pk }})">&#128465;</button>

<!-- Nachher -->
<button type="button" class="btn btn-ghost btn-sm rule-edit-btn" onclick="toggleEditComp('rule',{{ rule.pk }})">Bearbeiten</button>
...
<button type="button" class="btn btn-danger btn-sm" onclick="deleteRule({{ rule.pk }})">Löschen</button>
```

In der Formel-Liste (Z. 461–465):
```html
<!-- Vorher -->
<button type="button" class="btn btn-ghost btn-sm formula-edit-btn" onclick="toggleEditComp('formula',{{ f.pk }})">&#9998;</button>
...
<button type="button" class="btn btn-danger btn-sm" onclick="deleteFormula({{ f.pk }})">&#128465;</button>

<!-- Nachher -->
<button type="button" class="btn btn-ghost btn-sm formula-edit-btn" onclick="toggleEditComp('formula',{{ f.pk }})">Bearbeiten</button>
...
<button type="button" class="btn btn-danger btn-sm" onclick="deleteFormula({{ f.pk }})">Löschen</button>
```

Auch in `#pkg-list` den Workflow-Löschen-Button (Z. 124) aktualisieren:
```html
<!-- Vorher -->
<button class="btn btn-sm" onclick="deleteWorkflow()" id="btn-delete-wf" style="display:none" title="Workflow löschen">&#x1f5d1;</button>

<!-- Nachher -->
<button class="btn btn-sm" onclick="deleteWorkflow()" id="btn-delete-wf" style="display:none">Workflow löschen</button>
```

- [ ] **Schritt 2: Paket-Checkboxliste im Anlege-Formular scrollbar + suchbar machen**

Den Block in `#pkg-add` (Z. 276–280) ersetzen:

Vorher:
```html
<div class="form-group">
  <label class="form-label">Enthaltene Posten ...</label>
  <div style="display:flex;flex-wrap:wrap;gap:.4rem">
    {% for item in price_items %}<label ...><input type="checkbox" class="pkg-item-cb" value="{{ item.pk }}"> {{ item.name }} ({{ item.price }} €)</label>{% endfor %}
  </div>
</div>
```

Nachher:
```html
<div class="form-group">
  <label class="form-label">Enthaltene Posten <span style="font-weight:400;font-size:.68rem;color:var(--muted)">— Diese Posten sind im Paketpreis inbegriffen</span></label>
  <input type="text" id="pkg-item-search" class="form-input" placeholder="Posten suchen..." oninput="filterPkgItems(this.value)" style="margin-bottom:.4rem">
  <div id="pkg-item-list" style="display:flex;flex-wrap:wrap;gap:.4rem;max-height:140px;overflow-y:auto;padding:.4rem;border:1px solid var(--border);border-radius:var(--radius-sm)">
    {% for item in price_items %}<label class="pkg-item-label" style="font-size:.78rem;display:flex;align-items:center;gap:.3rem;padding:.2rem .5rem;background:var(--card);border-radius:4px;cursor:pointer"><input type="checkbox" class="pkg-item-cb" value="{{ item.pk }}"> {{ item.name }} ({{ item.price }} €)</label>{% endfor %}
  </div>
</div>
```

- [ ] **Schritt 3: filterPkgItems()-Funktion hinzufügen**

```javascript
function filterPkgItems(query) {
  var q = query.toLowerCase();
  document.querySelectorAll('#pkg-item-list .pkg-item-label').forEach(function(lbl) {
    lbl.style.display = lbl.textContent.toLowerCase().indexOf(q) !== -1 ? '' : 'none';
  });
}
```

- [ ] **Schritt 4: Manuell im Browser testen**

1. `/dj-admin/workflow/` öffnen
2. Preis-Posten-Tab: Buttons zeigen „Bearbeiten" / „Löschen" statt Icons
3. Pakete-Tab: `+ Neues Paket` → Suchfeld erscheint, tippen filtert Live
4. Bei >5 Posten: Container scrollt, bleibt bei max-height
5. Regeln-Tab, Formeln-Tab: gleiche Button-Labels

- [ ] **Schritt 5: Commit**

```bash
git add app/templates/dj_admin/workflow_builder.html
git commit -m "feat: text labels for edit/delete buttons, scrollable/searchable package item list"
```

---

## Verifikations-Checkliste (nach allen Tasks)

- [ ] `python app/manage.py check` — keine Fehler
- [ ] Canvas: Block klicken → anhängen, ▲▼ umordnen, ✕ entfernen, Workflow speichern + testen
- [ ] Regel mit 2+ Bedingungen anlegen, bearbeiten (Bedingungen sichtbar), Workflow-Test prüft Bedingungen
- [ ] Formel „Prüfen" mit `base + guests` → Ergebnis, mit `foo` → Fehler
- [ ] Nach jedem Speichern: kein Reload, Liste aktuell, Tab bleibt aktiv
- [ ] Buttons überall mit Text-Labels
- [ ] Paket-Checkboxliste: Suche funktioniert, scrollt bei vielen Einträgen
