"""Exercise the rendered workflow builder: CSRF header on reference cases, block reordering."""
import json
import subprocess
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from wishlist.models import AppConfig


SCENARIO = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const html = require('node:fs').readFileSync(0, 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(m => m[1]).find(s => s && s.includes("getElementById('add-block')"));
assert.ok(script, 'rendered workflow builder script missing');
assert.ok(script.includes("getElementById('reference-case-form')"),
  'activation block missing: the page must render the reference case form');

const TOKEN = 'csrf-sentinel';

class Element {
  constructor(tag = 'div') {
    this.tag = tag; this.children = []; this._text = ''; this.value = '';
    this.dataset = {}; this.listeners = {}; this.parentNode = null;
    this.classList = {add() {}, remove() {}}; this.style = {};
  }
  set textContent(value) { this.children = []; this._text = String(value); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set innerHTML(value) { throw Error('unsafe innerHTML write: ' + value); }
  append(...nodes) {
    for (const node of nodes) {
      if (!(node instanceof Element)) continue;
      node.parentNode = this; this.children.push(node);
    }
  }
  // Mirrors the DOM contract: insertBefore requires exactly two arguments.
  insertBefore(node, reference) {
    if (arguments.length < 2) {
      throw new TypeError("Failed to execute 'insertBefore' on 'Node': 2 arguments required, but only "
        + arguments.length + ' present.');
    }
    if (!(node instanceof Element)) throw new TypeError('insertBefore: node is not an Element');
    if (reference !== null && !(reference instanceof Element)) {
      throw new TypeError('insertBefore: reference is not an Element or null');
    }
    const existing = this.children.indexOf(node);
    if (existing !== -1) this.children.splice(existing, 1);
    const at = reference === null ? this.children.length : this.children.indexOf(reference);
    assert.notEqual(at, -1, 'insertBefore: reference node is not a child of this node');
    this.children.splice(at, 0, node);
    node.parentNode = this;
    return node;
  }
  remove() {
    if (!this.parentNode) return;
    const at = this.parentNode.children.indexOf(this);
    if (at !== -1) this.parentNode.children.splice(at, 1);
    this.parentNode = null;
  }
  _sibling(offset) {
    if (!this.parentNode) return null;
    const at = this.parentNode.children.indexOf(this);
    return this.parentNode.children[at + offset] || null;
  }
  get previousElementSibling() { return this._sibling(-1); }
  get nextElementSibling() { return this._sibling(1); }
  closest(selector) {
    for (let node = this; node; node = node.parentNode) {
      if (selector === 'button[data-action]' && node.tag === 'button' && node.dataset.action) return node;
      if (selector === 'li' && node.tag === 'li') return node;
    }
    return null;
  }
  querySelector(selector) {
    if (selector === '[name=csrfmiddlewaretoken]') return {value: TOKEN};
    if (selector === '[name=name]') return {value: 'Testablauf'};
    const wanted = selector === 'input' || selector === '.block-config' ? 'input' : null;
    if (!wanted) return null;
    const walk = node => {
      for (const child of node.children) {
        if (child.tag === wanted) return child;
        const deeper = walk(child); if (deeper) return deeper;
      }
      return null;
    };
    return walk(this);
  }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  dispatch(name, event = {}) {
    for (const callback of this.listeners[name] || []) {
      callback({preventDefault() {}, currentTarget: this, target: this, ...event});
    }
  }
  setAttribute() {}
}

class FormData {
  constructor(form) { this._entries = Object.entries(form._fields || {}); }
  [Symbol.iterator]() { return this._entries[Symbol.iterator](); }
  entries() { return this._entries[Symbol.iterator](); }
  getAll(name) { return this._entries.filter(([key]) => key === name).map(([, value]) => value); }
}

const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, new Element(id === 'canvas' ? 'ol' : 'div'));
  return elements.get(id);
};
get('block-type').value = 'package';
get('reference-case-form')._fields = {
  name: 'baseline', expected_v1_total: '100', expected_v2_total: '100',
  expected_difference: '0', tolerance_threshold: '',
  result: '{"v1_gross":"100","v2_gross":"100","difference":"0"}',
};

const calls = [];
const sandbox = {
  document: {getElementById: get, createElement: tag => new Element(tag), addEventListener() {}},
  console, window: {}, setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0,
  FormData, location: {reload() {}, href: ''},
  fetch: (url, options) => {
    calls.push({url, options});
    return Promise.resolve({ok: true, json: async () => ({id: 1})});
  },
};
vm.createContext(sandbox);
vm.runInContext(script, sandbox);

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const canvas = get('canvas');
const configOf = li => li.querySelector('input').value;

(async () => {
  // Reordering: the "down" button must move a block past its successor.
  canvas.children.length = 0;
  for (const name of ['first', 'second', 'third']) {
    canvas.append(sandbox.row('package', JSON.stringify({name})));
  }
  const order = () => canvas.children.map(li => JSON.parse(configOf(li)).name);
  assert.deepEqual(order(), ['first', 'second', 'third'], 'precondition: three blocks in order');

  const down = li => li.children.find(c => c.dataset.action === 'down');
  const up = li => li.children.find(c => c.dataset.action === 'up');

  canvas.dispatch('click', {target: down(canvas.children[0])});
  assert.deepEqual(order(), ['second', 'first', 'third'], 'down must move the block one position later');

  canvas.dispatch('click', {target: up(canvas.children[1])});
  assert.deepEqual(order(), ['first', 'second', 'third'], 'up must be the inverse of down');

  // Moving the last-but-one block down must land it at the end, not crash.
  canvas.dispatch('click', {target: down(canvas.children[1])});
  assert.deepEqual(order(), ['first', 'third', 'second'], 'down must work next to the end of the list');

  // The final block has no successor: the button must do nothing.
  canvas.dispatch('click', {target: down(canvas.children[2])});
  assert.deepEqual(order(), ['first', 'third', 'second'], 'down on the last block must be a no-op');

  // Saving a reference case must carry the CSRF token as a real header.
  get('reference-case-form').dispatch('submit');
  await flush();
  assert.equal(calls.length, 1, 'reference case form must send exactly one request');
  const headers = calls[0].options.headers;
  for (const key of Object.keys(headers)) {
    assert.ok(!key.includes(':'), 'malformed header name carries its value inside the key: ' + key);
  }
  assert.equal(headers['X-CSRFToken'], TOKEN, 'reference case request must carry the CSRF token');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.django_db
@override_settings(STORAGES={'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
def test_workflow_builder_reorders_blocks_and_sends_csrf_token_for_reference_cases(client, django_user_model):
    AppConfig.objects.get_or_create(pk=1, defaults={'pricing_engine_mode': 'legacy'})
    client.force_login(django_user_model.objects.create_user(username='workflow-ui', is_staff=True))
    page = client.get(reverse('dj_admin:pricing_v2_activation'))
    assert page.status_code == 200
    run = subprocess.run(['node', '-e', SCENARIO], input=page.content.decode(),
                         text=True, capture_output=True, timeout=15)
    assert run.returncode == 0, run.stderr
