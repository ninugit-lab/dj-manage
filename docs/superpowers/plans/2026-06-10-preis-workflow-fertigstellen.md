# Preis-Workflow fertigstellen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live-Preisschätzung auf `/buchen/` mit automatischer Distanzberechnung (Haversine aus Nominatim-Koordinaten) plus funktionierender Simulations-Test im Workflow-Builder.

**Architecture:** DJ-Standort-Koordinaten kommen als neue AppConfig-Felder (Migration 0009) und werden auf der Config-Seite per Nominatim-Suche gesetzt. Das Buchungsformular berechnet die Distanz clientseitig (Haversine × 1,3) und schickt sie an `/api/price-estimate/` sowie beim Submit mit. Die `PriceEngine` bekommt einen optionalen `context_override`-Parameter, über den `api_price_calculate()` das bereits vom Builder-Frontend gesendete `test_context` durchreicht.

**Tech Stack:** Django (App `wishlist`), Vanilla JS in Templates, Nominatim (bereits eingebunden), SQLite. Kein Test-Framework vorhanden — Verifikation per `manage.py shell -c` und Browser. **Kein Git-Repo** — Commit-Schritte entfallen; stattdessen Verifikations-Checkpoints.

**Hinweise zur Umgebung:**
- Alle `manage.py`-Befehle laufen im Container: `docker compose exec web python manage.py <cmd>` (Arbeitsverzeichnis `/home/camp/Server/Rene/dj-manage`).
- `entrypoint.sh` migriert automatisch beim Start — nach Schema-Änderung reicht aber `migrate` im laufenden Container.
- Wichtig (Stand heute, bereits verifiziert): `showToast()` ist in `app/templates/dj_admin/base.html:237` definiert, `getStepConfigValue()` in `workflow_builder.html:667–670`, das Test-Parameter-Panel (`tp-guests`, `tp-hours`, `tp-distance`, `tp-weekday`, `tp-month`, `tp-type`) existiert in `workflow_builder.html:143–170` und `testWorkflow()` sendet `test_context` bereits (Zeilen 685–692). **Am Builder-Template ist NICHTS zu ändern.**

---

### Task 1: AppConfig um DJ-Standort-Koordinaten erweitern (Model + Migration + Config-UI)

**Files:**
- Modify: `app/wishlist/models.py` (AppConfig, nach `dj_address` ~Zeile 229)
- Modify: `app/wishlist/admin_views.py` (`config_page`, Zeilen 346–370)
- Modify: `app/templates/dj_admin/config.html` (nach dem `dj_address`-Textarea, ~Zeile 49)
- Create: `app/wishlist/migrations/0009_*.py` (via makemigrations)

- [ ] **Step 1: Model-Felder hinzufügen**

In `app/wishlist/models.py`, direkt nach der Zeile `dj_address = models.TextField(blank=True, verbose_name="Adresse")`:

```python
    dj_home_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        verbose_name="Standort Breitengrad",
        help_text="Für die Anfahrts-Berechnung im Buchungsformular")
    dj_home_lon = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        verbose_name="Standort Längengrad")
```

- [ ] **Step 2: Migration erstellen und anwenden**

Run: `docker compose exec web python manage.py makemigrations wishlist`
Expected: `0009_appconfig_dj_home_lat_appconfig_dj_home_lon.py` (o. ä.) wird erstellt.

Run: `docker compose exec web python manage.py migrate wishlist`
Expected: `Applying wishlist.0009_... OK`

- [ ] **Step 3: POST-Handling in config_page**

In `app/wishlist/admin_views.py`, in `config_page()` innerhalb des `if action == 'save_config':`-Blocks, direkt vor `config.save()` (nach `config.event_form_enabled = ...`, Zeile 368):

```python
            for coord_field in ['dj_home_lat', 'dj_home_lon']:
                raw = request.POST.get(coord_field, '').strip().replace(',', '.')
                try:
                    setattr(config, coord_field, Decimal(raw) if raw else None)
                except InvalidOperation:
                    setattr(config, coord_field, None)
```

Oben in `admin_views.py` prüfen, ob `from decimal import Decimal, InvalidOperation` importiert ist (`grep -n 'from decimal' app/wishlist/admin_views.py`). Falls nicht, den Import bei den anderen Imports ergänzen:

```python
from decimal import Decimal, InvalidOperation
```

- [ ] **Step 4: Config-UI mit Nominatim-Suche**

In `app/templates/dj_admin/config.html`, direkt nach dem `dj_address`-Feld (Block mit `<textarea name="dj_address" ...>{{ config.dj_address }}</textarea>`, ~Zeile 49, dessen umschließendes `</div>` abwarten):

```html
      <div class="form-group" style="position:relative">
        <label class="form-label">Standort für Anfahrts-Berechnung</label>
        <input type="text" id="cfg-addr-search" class="form-input" placeholder="Adresse suchen, um Koordinaten zu setzen…" autocomplete="off">
        <div id="cfg-addr-results" style="display:none;position:absolute;left:0;right:0;z-index:50;background:var(--card-solid,#1a1a2e);border:1px solid var(--border);border-radius:var(--radius);max-height:220px;overflow-y:auto"></div>
        <div style="display:flex;gap:.75rem;margin-top:.5rem">
          <div style="flex:1"><label class="form-label" style="font-size:.7rem">Breitengrad</label>
            <input type="text" name="dj_home_lat" id="cfg-home-lat" class="form-input" value="{{ config.dj_home_lat|default_if_none:'' }}"></div>
          <div style="flex:1"><label class="form-label" style="font-size:.7rem">Längengrad</label>
            <input type="text" name="dj_home_lon" id="cfg-home-lon" class="form-input" value="{{ config.dj_home_lon|default_if_none:'' }}"></div>
        </div>
        <div style="font-size:.72rem;color:var(--muted);margin-top:.3rem">Wird genutzt, um Kunden im Buchungsformular die Anfahrt live einzupreisen.</div>
      </div>
```

Dann im `<script>`-Bereich des Templates (bzw. am Ende vor `{% endblock %}` einen Script-Block ergänzen, falls keiner existiert — mit `grep -n '<script>' app/templates/dj_admin/config.html` prüfen):

```javascript
(function(){
  var si=document.getElementById('cfg-addr-search'),rb=document.getElementById('cfg-addr-results'),dt;
  if(!si)return;
  si.addEventListener('input',function(){
    clearTimeout(dt);var q=this.value.trim();
    if(q.length<3){rb.style.display='none';return}
    dt=setTimeout(function(){
      fetch('https://nominatim.openstreetmap.org/search?format=json&limit=5&countrycodes=de,at,ch&q='+encodeURIComponent(q))
      .then(function(r){return r.json()})
      .then(function(data){
        if(!data.length){rb.style.display='none';return}
        rb.innerHTML='';
        data.forEach(function(item){
          var d=document.createElement('div');
          d.textContent=item.display_name;
          d.style.cssText='padding:.5rem .75rem;font-size:.82rem;cursor:pointer;border-bottom:1px solid var(--border)';
          d.onclick=function(){
            document.getElementById('cfg-home-lat').value=parseFloat(item.lat).toFixed(6);
            document.getElementById('cfg-home-lon').value=parseFloat(item.lon).toFixed(6);
            si.value=item.display_name;rb.style.display='none';
          };
          rb.appendChild(d);
        });
        rb.style.display='block';
      }).catch(function(){rb.style.display='none'});
    },350);
  });
  document.addEventListener('click',function(e){if(!si.contains(e.target)&&!rb.contains(e.target))rb.style.display='none'});
})();
```

- [ ] **Step 5: Verifikation Model + Save-Roundtrip**

Run:
```bash
docker compose exec web python manage.py shell -c "
from wishlist.models import AppConfig
from decimal import Decimal
c = AppConfig.load()
c.dj_home_lat = Decimal('52.520008'); c.dj_home_lon = Decimal('13.404954')
c.save()
c2 = AppConfig.load()
print('OK', c2.dj_home_lat, c2.dj_home_lon)
c2.dj_home_lat = None; c2.dj_home_lon = None; c2.save()
"
```
Expected: `OK 52.520008 13.404954`

- [ ] **Step 6: Browser-Check Config-Seite**

`/dj-admin/config/` öffnen: Adresssuche tippen → Vorschlag klicken → lat/lon-Felder gefüllt → Speichern → Felder nach Reload weiterhin gefüllt.

---

### Task 2: Distanz im Buchungsformular + Formular-Bugfixes

**Files:**
- Modify: `app/wishlist/views.py` (`event_form_view` Zeile 234–249, `api_price_estimate` Zeile 540–583)
- Modify: `app/templates/wishlist/event_form.html` (JS, Zeilen 218–336)

- [ ] **Step 1: DJ-Koordinaten in den View-Context**

In `app/wishlist/views.py`, `event_form_view()` (Zeile 234): vor dem `render`-Aufruf einfügen und ins Context-Dict aufnehmen:

```python
    import json as _json
    dj_coords_json = _json.dumps({
        'lat': float(config.dj_home_lat) if config.dj_home_lat is not None else None,
        'lon': float(config.dj_home_lon) if config.dj_home_lon is not None else None,
    })
```

Context-Dict erweitern: `'dj_coords_json': dj_coords_json,`

- [ ] **Step 2: api_price_estimate nimmt distance_km an**

In `app/wishlist/views.py`, `api_price_estimate()`: nach `event.time_end = ...` (Zeile 555) einfügen:

```python
    try:
        event.distance_km = Decimal(str(data['distance_km'])) if data.get('distance_km') is not None else None
    except (InvalidOperation, ValueError):
        event.distance_km = None
```

Der `Decimal`-Import existiert bereits lokal in der Funktion (Zeile 545); `InvalidOperation` dort ergänzen: `from decimal import Decimal, InvalidOperation`.

- [ ] **Step 3: Haversine + Distanz-Variable im Template**

In `app/templates/wishlist/event_form.html`, im `<script>`-Block nach `var dateAvailable=false;` (Zeile 189):

```javascript
var DJ_HOME={{ dj_coords_json|safe }};
var eventDistanceKm=null;
function haversineKm(lat1,lon1,lat2,lon2){
  var R=6371,toRad=Math.PI/180;
  var dLat=(lat2-lat1)*toRad,dLon=(lon2-lon1)*toRad;
  var a=Math.sin(dLat/2)*Math.sin(dLat/2)+Math.cos(lat1*toRad)*Math.cos(lat2*toRad)*Math.sin(dLon/2)*Math.sin(dLon/2);
  return R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
}
function setEventCoords(lat,lon){
  if(DJ_HOME.lat===null||DJ_HOME.lon===null||lat===null){eventDistanceKm=null;return}
  // Faktor 1.3: Näherung Luftlinie -> Straßenkilometer
  eventDistanceKm=Math.round(haversineKm(DJ_HOME.lat,DJ_HOME.lon,lat,lon)*1.3);
}
```

- [ ] **Step 4: Nominatim-Auswahl setzt Koordinaten + Preis-Update**

Im Nominatim-`onclick`-Handler (Zeile 312–325), nach `document.getElementById('f-city').value = city;` einfügen:

```javascript
              setEventCoords(parseFloat(item.lat), parseFloat(item.lon));
              updatePrice();
```

- [ ] **Step 5: distance_km in doUpdatePrice senden**

In `doUpdatePrice()` (Zeile 218–230), im `body`-Objekt nach `time_end:...,` einfügen:

```javascript
    distance_km:eventDistanceKm,
```

- [ ] **Step 6: Anfahrt im Breakdown sichtbar machen**

Keine Code-Änderung nötig: Distanz-Regeln erscheinen als Einträge in `d.rules_applied` und werden bereits gerendert (Zeile 241). Nur verifizieren (Step 9).

- [ ] **Step 7: Submit-Bugfixes**

In `submitForm()` (Zeile 259–287) zwei Zeilen ersetzen:

Zeile 279 alt:
```javascript
      distance_km:document.getElementById('f-distance')? document.getElementById('f-distance').value||null : null,
```
neu:
```javascript
      distance_km:eventDistanceKm,
```

Zeile 281 alt:
```javascript
      package_id:(function(){var p=document.querySelector('input[name="package"]:checked');return p?parseInt(p.value):null})()
```
neu:
```javascript
      package_id:(function(){var p=document.querySelector('input[name="f-package"]:checked');return p?parseInt(p.value):null})()
```

Hinweis: `submit_event_form()` (views.py:329) speichert `distance_km` bereits — backendseitig keine Änderung nötig.

- [ ] **Step 8: API-Verifikation per curl/shell**

Vorbereitung: DJ-Koordinaten setzen (Berlin) und eine Distanz-Regel anlegen, falls keine existiert:

```bash
docker compose exec web python manage.py shell -c "
from wishlist.models import AppConfig, PricingRule
from decimal import Decimal
c = AppConfig.load(); c.dj_home_lat = Decimal('52.520008'); c.dj_home_lon = Decimal('13.404954'); c.save()
r, created = PricingRule.objects.get_or_create(name='TEST Anfahrt ab 50km', defaults=dict(
    condition_json=[{'field':'distance_km','op':'gte','value':50}],
    effect_type='flat_add', effect_value=Decimal('100'), is_active=True))
print('rule', r.pk, 'created', created)
"
```

Hinweis: Vorher mit `docker compose exec web python manage.py shell -c "from wishlist.models import PricingRule; print(PricingRule.objects.values('condition_json').first())"` das tatsächliche Operator-Format (`gte` vs. `>=`) eines bestehenden Datensatzes prüfen und im Test-Datensatz dasselbe Format verwenden.

Dann Engine direkt testen:

```bash
docker compose exec web python manage.py shell -c "
from wishlist.models import Event
from wishlist.price_engine import PriceEngine
from decimal import Decimal
e = Event(); e.distance_km = Decimal('80')
r = PriceEngine.calculate(event=e)
print([x['name'] for x in r['rules_applied']], r['grand_total'])
"
```
Expected: `['TEST Anfahrt ab 50km'] 100…` (Regel greift).

Danach Testregel wieder löschen:
```bash
docker compose exec web python manage.py shell -c "from wishlist.models import PricingRule; PricingRule.objects.filter(name='TEST Anfahrt ab 50km').delete()"
```

- [ ] **Step 9: Browser-Check /buchen/**

1. Datum wählen (verfügbar) → Formular klappt auf.
2. Adresse über Nominatim-Suche wählen (weit entfernt vom DJ-Standort) → Live-Preis aktualisiert sich, Distanz-Regel erscheint im Breakdown.
3. Paket wählen, Anfrage absenden → Erfolgsansicht.
4. Im DJ-Admin das Event öffnen: `distance_km` und Paket sind gesetzt.
5. Gegenprobe: DJ-Koordinaten in Config leeren → /buchen/ funktioniert ohne Fehler, kein Anfahrts-Posten.

---

### Task 3: Simulations-Test im Workflow-Builder (Backend `test_context`)

**Files:**
- Modify: `app/wishlist/price_engine.py` (`calculate` Zeile 137, `calculate_workflow` Zeile 258)
- Modify: `app/wishlist/admin_views.py` (`api_price_calculate` Zeile 600–637)

Das Builder-Frontend sendet `test_context` bereits (workflow_builder.html:685–692) mit den Keys: `guest_count`, `duration_hours`, `distance_km`, `date_weekday`, `date_month`, `event_type`. Diese entsprechen exakt den Keys aus `RuleEvaluator.build_context()` (price_engine.py:100–107).

- [ ] **Step 1: `context_override` in PriceEngine.calculate**

Signatur (Zeile 138–140) erweitern:

```python
    def calculate(event, package_id=None, selected_item_ids=None,
                  formula_id=None, custom_items=None, discount_percent=0,
                  offer_data=None, context_override=None):
```

Nach `context = RuleEvaluator.build_context(event, offer)` (Zeile 205):

```python
        if context_override:
            context.update(context_override)
```

- [ ] **Step 2: `context_override` in PriceEngine.calculate_workflow**

Signatur (Zeile 259–261) erweitern:

```python
    def calculate_workflow(event, workflow_id, package_id=None,
                           selected_item_ids=None, formula_id=None,
                           discount_percent=0, custom_items=None, offer_data=None,
                           context_override=None):
```

Nach der Zeile `context = RuleEvaluator.build_context(event, offer)` innerhalb von `calculate_workflow` (ca. Zeile 315, mit `grep -n 'build_context' app/wishlist/price_engine.py` lokalisieren — der zweite Treffer):

```python
        if context_override:
            context.update(context_override)
```

- [ ] **Step 3: api_price_calculate reicht test_context durch**

In `app/wishlist/admin_views.py`, `api_price_calculate()`: das `kwargs`-Dict (Zeile 614–621) erweitern. Direkt davor:

```python
    ALLOWED_CONTEXT_KEYS = {'guest_count', 'duration_hours', 'distance_km',
                            'date_weekday', 'date_month', 'event_type'}
    test_context = data.get('test_context') or None
    if test_context:
        test_context = {k: v for k, v in test_context.items() if k in ALLOWED_CONTEXT_KEYS}
```

Und im `kwargs`-Dict ergänzen:

```python
        context_override=test_context,
```

- [ ] **Step 4: Engine-Verifikation per Shell**

```bash
docker compose exec web python manage.py shell -c "
from wishlist.models import Event, PricingRule
from wishlist.price_engine import PriceEngine
from decimal import Decimal
r, _ = PricingRule.objects.get_or_create(name='TEST Sa-Zuschlag', defaults=dict(
    condition_json=[{'field':'date_weekday','op':'eq','value':5}],
    effect_type='flat_add', effect_value=Decimal('50'), is_active=True))
res = PriceEngine.calculate(event=Event(), context_override={'date_weekday': 5, 'guest_count': 100})
print('rules:', [x['name'] for x in res['rules_applied']])
res2 = PriceEngine.calculate(event=Event(), context_override={'date_weekday': 2})
print('rules2:', [x['name'] for x in res2['rules_applied']])
PricingRule.objects.filter(name='TEST Sa-Zuschlag').delete()
"
```
Expected: `rules: ['TEST Sa-Zuschlag']` und `rules2: []`. (Operator-Format `eq` vorher wie in Task 2 Step 8 gegen Bestandsdaten prüfen.)

- [ ] **Step 5: Browser-Check Workflow-Builder**

1. `/dj-admin/workflow/` öffnen, Workflow mit mind. einem Rules-Block laden/speichern.
2. „Test-Parameter" öffnen, Wochentag = Samstag, Gäste = 100, Distanz = 80 setzen.
3. „Testen" klicken → Ergebnis zeigt Schritte; Regeln, die zu den Parametern passen, greifen.
4. Parameter ändern (z. B. Wochentag = Mittwoch) → erneut testen → Regel greift nicht mehr.
5. Regressions-Check: Preisberechnung im Event-Edit des DJ-Admin (nutzt denselben Endpoint ohne `test_context`) funktioniert unverändert.

---

### Task 4: Abschluss-Verifikation

- [ ] **Step 1: Django-Check + Server-Log**

Run: `docker compose exec web python manage.py check`
Expected: `System check identified no issues`

Run: `docker compose logs web --since 10m 2>&1 | grep -iE 'error|traceback' | head -20`
Expected: keine neuen Fehler.

- [ ] **Step 2: Spec-Abgleich**

Gegen `docs/superpowers/specs/2026-06-10-preis-workflow-fertigstellen-design.md` prüfen: Distanz auto aus Adresse ✓, voller Breakdown ✓, beide Formular-Bugs ✓, Simulations-Test ✓, Fehlerverhalten ohne Koordinaten ✓.
