"""Execute the rendered vanilla-JS form against a small DOM, not source regexes."""
import subprocess

import pytest
from django.test import override_settings

from wishlist.models import AppConfig


SCENARIO = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const html = require('node:fs').readFileSync(0, 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(m => m[1]).find(s => s.includes('function updatePrice()'));
assert.ok(script, 'rendered price script missing');
class Element {
  constructor() { this.children = []; this._text = ''; this.value = ''; this.dataset = {}; this.classList = {add(){},remove(){}}; this.style = {}; }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set innerHTML(value) { throw Error('unsafe innerHTML write: ' + value); }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; this._text = ''; }
  addEventListener() {}
  querySelector() { return null; }
  setAttribute() {}
  removeAttribute() {}
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
let packageId = '1';
const selected = [];
const pending = [];
const timers = [];
const document = {
  getElementById: get, createElement: () => new Element(), addEventListener() {},
  querySelector: selector => selector === 'input[name="f-package"]:checked' ? {value: packageId} : null,
  querySelectorAll: selector => selector === '.price-checkbox:checked' ? selected.filter(item => item.checked) : []
};
const sandbox = {document, console, AbortController, setTimeout: f => {timers.push(f); return timers.length;}, clearTimeout: () => {},
  window: {scrollTo(){}}, fetch: (url, options) => new Promise(resolve => pending.push({resolve, options}))};
vm.createContext(sandbox);
vm.runInContext(script, sandbox);
timers.length = 0; // Ignore the form's initial debounce before supplying test inputs.
get('f-date').value = '2026-12-02';
get('f-time-start').value = '18:00';
get('f-time-end').value = '21:00';
const flush = async () => { for (let n = 0; n < 8; n++) await Promise.resolve(); };
const result = (name, total) => ({ok:true, engine_version:'2', status:'ok', gross_total:total,
  lines:[{label:name, amount:total, unit_price:total}], warnings:[]});
(async () => {
  sandbox.updatePrice(); timers.shift()();
  assert.equal(pending.length, 1);
  packageId = '2'; sandbox.updatePrice(); timers.shift()();
  assert.equal(pending[0].options.signal.aborted, true);
  pending[1].resolve({json:async () => result('<img src=x onerror=alert(1)>', '230.00')});
  await flush();
  assert.match(get('price-total').textContent, /230/);
  assert.equal(get('price-breakdown').children[0].textContent.includes('<img src=x onerror=alert(1)>'), true);
  pending[0].resolve({json:async () => result('stale', '900.00')});
  await flush();
  assert.doesNotMatch(get('price-total').textContent, /900/);
  packageId = '';
  sandbox.updatePrice();
  assert.doesNotMatch(get('price-total').textContent, /230/);
  await flush();
  timers.shift()();
  assert.equal(pending.length, 3, 'package removal must request an individual-items estimate');
  pending[2].resolve({json:async () => result('No package', '0.00')});
  await flush();
  packageId = '3'; sandbox.updatePrice(); timers.shift()();
  pending[3].resolve({json:async () => ({ok:false,status:'individual_review',message:'Preis wird individuell geprüft'})});
  await flush();
  assert.match(get('price-total').textContent, /individuell geprüft/);
  pending[3].resolve({json:async () => result('late', '999.00')});
  await flush();
  assert.doesNotMatch(get('price-total').textContent, /999/);
  const required = new Element(); required.value = '17'; required.checked = true; required.disabled = true;
  selected.push(required);
  packageId = '';
  sandbox.updatePrice(); timers.shift()();
  assert.equal(pending.length, 5, 'individual items request must be sent without a package');
  const individual = JSON.parse(pending[4].options.body);
  assert.equal(individual.package_id, null);
  assert.deepEqual(individual.selected_item_ids, [17], 'checked disabled required checkbox must be included');
  packageId = '3'; sandbox.updatePrice();
  pending[4].resolve({json:async () => result('obsolete', '999.00')});
  await flush();
  assert.doesNotMatch(get('price-total').textContent, /999/);
  timers.shift()(); // This request is superseded by the next no-package selection.
  packageId = ''; sandbox.updatePrice(); timers.shift()();
  assert.equal(pending.length, 7);
  assert.deepEqual(JSON.parse(pending[6].options.body).selected_item_ids, [17]);
  pending[6].resolve({json:async () => result('Required', '20.00')});
  await flush();
  assert.match(get('price-total').textContent, /20,00/);
})().catch(err => { console.error(err); process.exitCode = 1; });
"""


@pytest.mark.django_db
@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
def test_rendered_form_suppresses_stale_prices_and_treats_labels_as_text(client):
    AppConfig.objects.create(event_form_enabled=True)
    page = client.get('/buchen/')
    assert page.status_code == 200
    run = subprocess.run(['node', '-e', SCENARIO], input=page.content.decode(),
                         text=True, capture_output=True, timeout=15)
    assert run.returncode == 0, run.stderr
