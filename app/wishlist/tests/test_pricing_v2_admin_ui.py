"""Exercise the rendered admin calculator with a minimal DOM and asynchronous responses."""
import subprocess

import pytest
from django.test import override_settings
from django.urls import reverse

from wishlist.models import AppConfig


SCENARIO = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const html = require('node:fs').readFileSync(0, 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(m => m[1]).find(s => s.includes('function doRecalculate('));
assert.ok(script, 'rendered admin calculator missing');
class Element {
  constructor(value = '') { this.value = value; this.children = []; this._text = ''; this.dataset = {}; this.listeners = {}; this.classList = {add(){}, remove(){}}; this.style = {}; }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set innerHTML(value) { throw Error('unsafe innerHTML write: ' + value); }
  replaceChildren(...children) { this.children = children; this._text = ''; }
  append(...children) { this.children.push(...children); }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatch(name) { for (const callback of this.listeners[name] || []) callback(); }
  setAttribute() {}
  querySelector() { return null; }
}
const elements = new Map();
const get = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
const fields = new Map(Object.entries({date:'2999-06-20', time_start:'18:00', time_end:'21:00', distance_km:'12', guest_count:'10', event_type:'party'}).map(([name,value]) => [name,new Element(value)]));
get('workflow-select').value = '2'; get('pricing-discount').value = '12.34';
const checks = [new Element('17')]; checks[0].checked = true;
const pending = []; const timers = [];
const document = { getElementById:get, createElement:() => new Element(), addEventListener(){},
  querySelector: selector => selector === 'input[name="pricing_package_id"]:checked' ? {value:'1'} :
    fields.get(selector.match(/^\[name="([^"]+)"\]$/)?.[1]) || null,
  querySelectorAll: selector => selector === '.admin-price-cb:checked' ? checks.filter(c => c.checked) :
    selector === '.admin-price-cb' ? checks : [] };
const sandbox = {document, console, window:{}, csrfToken:() => 'test', setInterval:()=>0,
  setTimeout: callback => { timers.push(callback); return timers.length; }, clearTimeout:()=>{},
  fetch: (url, options) => new Promise(resolve => pending.push({resolve, options}))};
vm.createContext(sandbox); vm.runInContext(script, sandbox);
const flush = async () => { for(let i=0;i<8;i++) await Promise.resolve(); };
const result = (total,label) => ({ok:true, gross_total:total, lines:[{label,amount:total}]});
(async () => {
  sandbox.doRecalculate();
  assert.equal(JSON.parse(pending[0].options.body).discount_percent, '12.34');
  for (const name of fields.keys()) {
    const before = timers.length;
    fields.get(name).dispatch('change');
    assert.ok(timers.length > before, name + ' must recalculate when edited');
  }
  checks[0].dispatch('change');
  assert.ok(timers.length > 6, 'item checkbox must recalculate');
  timers.at(-1)();
  assert.equal(pending.length, 2);
  pending[1].resolve({json:async () => result('131.49','<img src=x onerror=alert(1)>')});
  await flush();
  assert.match(get('unified-breakdown').textContent, /131.49/);
  pending[0].resolve({json:async () => result('999.00','stale')});
  await flush();
  assert.doesNotMatch(get('unified-breakdown').textContent, /999.00/);
  assert.match(get('unified-breakdown').textContent, /<img src=x onerror=alert\(1\)>/);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.django_db
@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
def test_admin_preview_reacts_to_edits_sends_decimal_and_suppresses_stale_responses(client, django_user_model):
    config = AppConfig.load()
    config.pricing_engine_mode = 'v2'
    config.save()
    client.force_login(django_user_model.objects.create_user(username='admin-ui', is_staff=True))
    page = client.get(reverse('dj_admin:event_create'))
    assert page.status_code == 200
    run = subprocess.run(['node', '-e', SCENARIO], input=page.content.decode(),
                         text=True, capture_output=True, timeout=15)
    assert run.returncode == 0, run.stderr
