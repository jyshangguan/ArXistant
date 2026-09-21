// Minimal DOM/extension stub that runs a REAL ArXistant content script and
// reports what it did, so the panel logic can be tested without a browser.
//
// This exists because a route-matching unit test passed while the feature was
// broken end to end: the test agreed with the source instead of with reality.
// Running the actual script catches that class of bug.
//
// Deliberately dependency-free (the repo has no package.json / jsdom).
// innerHTML is stored as a string and querySelector resolves simple .class /
// #id selectors against real children, so this verifies structure, content
// and messaging — not real CSS layout.
//
// Usage: node panel-harness.js <scriptCsv> <pathname> <host> [metaJson]
//   scriptCsv  comma-separated content scripts in manifest load order,
//              e.g. "content-panel.js,content-scix.js"
// Prints one JSON object on stdout.

'use strict';
const fs = require('fs');
const vm = require('vm');

const [, , scriptCsv, pathname, host, metaJson] = process.argv;
const scriptPaths = String(scriptCsv || '').split(',').map(s => s.trim()).filter(Boolean);

// ---- element stub ----------------------------------------------------------
let uid = 0;
let focusedEl = null;   // last element that received focus()
class El {
  constructor(tag) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.uid = ++uid;
    this.id = '';
    this.className = '';
    this.children = [];
    this.parent = null;
    this.isConnected = false;
    this.style = {};
    this.dataset = {};
    this.attrs = {};
    this.listeners = {};
    this._html = '';
    this._text = '';
    this.value = '';
    const self = this;
    this.classList = {
      add: (c) => { self.className += (self.className ? ' ' : '') + c; },
      remove: (c) => {
        self.className = self.className.split(/\s+/).filter(x => x && x !== c).join(' ');
      },
      toggle: (c, on) => {
        const has = self.className.split(/\s+/).includes(c);
        const want = on === undefined ? !has : !!on;
        if (want && !has) self.classList.add(c);
        if (!want && has) self.classList.remove(c);
      },
      contains: (c) => self.className.split(/\s+/).includes(c),
    };
  }
  set innerHTML(v) {
    this._html = String(v);
    // Crude flat parse: create a child per opening tag, carrying its class and
    // id, so tests can locate rendered controls and fire their listeners.
    // Nesting is not preserved, which is enough for querySelector-by-class.
    this.children = [];
    const re = /<([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>/g;
    let m;
    while ((m = re.exec(this._html)) !== null) {
      const el = new El(m[1]);
      const cls = /class="([^"]*)"/.exec(m[2]);
      if (cls) el.className = cls[1];
      const idm = /id="([^"]*)"/.exec(m[2]);
      if (idm) el.id = idm[1];
      const txt = /^([^<]*)/.exec(this._html.slice(re.lastIndex));
      if (txt) el._text = txt[1];
      el.parent = this;
      el.isConnected = this.isConnected;
      this.children.push(el);
    }
  }
  get innerHTML() { return this._html; }
  set textContent(v) {
    this._text = String(v);
    // Mirror the browser: reading innerHTML after assigning textContent yields
    // the escaped text. The panel's escapeText() relies on exactly this.
    this._html = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
  get textContent() { return this._text; }
  appendChild(child) {
    child.parent = this;
    child.isConnected = this.isConnected || this === sandbox.document.body
      || this === sandbox.document.documentElement;
    this.children.push(child);
    return child;
  }
  // Resolves simple `.class` / `#id` selectors against real children so the
  // harness can assert on rendered content; anything else gets a permissive
  // stub so event wiring still executes without a real parser.
  _find(sel) {
    const m = /^([.#])([A-Za-z0-9_-]+)$/.exec(sel);
    if (!m) return null;
    const byId = m[1] === '#';
    const want = m[2];
    const stack = this.children.slice();
    while (stack.length) {
      const el = stack.shift();
      if (byId ? el.id === want : el.className.split(/\s+/).includes(want)) return el;
      stack.push(...el.children);
    }
    return null;
  }
  _findAll(sel) {
    const m = /^([.#])([A-Za-z0-9_-]+)$/.exec(sel);
    if (!m) return [];
    const byId = m[1] === '#';
    const out = [];
    const stack = this.children.slice();
    while (stack.length) {
      const el = stack.shift();
      if (byId ? el.id === m[2] : el.className.split(/\s+/).includes(m[2])) out.push(el);
      stack.push(...el.children);
    }
    return out;
  }
  querySelector(sel) { return this._find(sel) || new El('div'); }
  querySelectorAll(sel) { return this._findAll(sel); }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  focus() { focusedEl = this; this._focused = true; }
  blur() { if (focusedEl === this) focusedEl = null; this._focused = false; }
  setSelectionRange(a, b) { this._selStart = a; this._selEnd = b; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  get firstChild() { return this.children[0] || null; }
}

// ---- document / window / location -----------------------------------------
const bodyEl = new El('body');
const htmlEl = new El('html');
bodyEl.isConnected = true;
htmlEl.isConnected = true;
htmlEl.appendChild(bodyEl);

const documentStub = {
  readyState: 'complete',
  body: bodyEl,
  documentElement: htmlEl,
  title: 'stub page',
  createElement: (t) => new El(t),
  addEventListener: () => {},
  querySelector: () => null,
  querySelectorAll: () => [],
  head: new El('head'),
};
Object.defineProperty(documentStub, 'activeElement', { get: () => focusedEl });

// ---- recorded side effects -------------------------------------------------
const messages = [];   // chrome.runtime.sendMessage payloads
const logs = [];       // console output
const attached = [];   // elements appended to body/documentElement

for (const fn of ['appendChild']) {
  const orig = bodyEl[fn].bind(bodyEl);
  bodyEl[fn] = (c) => { attached.push({ parent: 'body', id: c.id, cls: c.className }); return orig(c); };
  const origH = htmlEl[fn].bind(htmlEl);
  htmlEl[fn] = (c) => { attached.push({ parent: 'html', id: c.id, cls: c.className }); return origH(c); };
}

// ---- extension + timer stubs ----------------------------------------------
const META = metaJson ? JSON.parse(metaJson) : {};
const paperFixture = META.__paper || {
  id: '1929PNAS...15..168H',
  bibcode: '1929PNAS...15..168H',
  title: 'A Relation between Distance and Radial Velocity',
  authors: ['Hubble, Edwin'],
  abstract: 'Determinations of the motion of the sun.',
  year: '1929', citation_count: 1275, is_arxiv: false, source: 'scix',
};

const chromeStub = {
  runtime: {
    lastError: null,
    sendMessage(msg, cb) {
      messages.push(JSON.parse(JSON.stringify(msg)));
      let reply;
      switch (msg.action) {
        case 'getSettings':
          reply = {
            success: true,
            settings: Object.assign(
              { serverUrl: 'http://localhost:8765/daily.html',
                panelOnArxiv: true, panelOnScix: true },
              META.__settings || {})
          };
          break;
        case 'savedPapers':
          reply = {
            success: true,
            ids: META.__savedIds || [],
            tagsById: META.__tagsById || {},
            vocab: META.__vocab || []
          };
          break;
        case 'updateTags':
          reply = { success: true, tags: msg.tags || [] };
          break;
        case 'scixResolve':
          reply = META.__resolveFails
            ? { success: false, error: 'SciX has no record for ' + msg.identifier }
            : { success: true, paper: paperFixture };
          break;
        case 'arxivResolve':
          reply = { success: true, paper: paperFixture };
          break;
        case 'savePaper':
          reply = META.__saveFails
            ? { success: false, error: 'The server rejected the change.' }
            : { success: true, message: 'ok' };
          break;
        case 'deletePaper':
          reply = { success: true, message: 'ok' };
          break;
        default:
          reply = { success: true };
      }
      // Resolve asynchronously, like the real messaging channel.
      Promise.resolve().then(() => cb(reply));
    },
  },
};

const intervals = [];
const sandbox = {
  document: documentStub,
  chrome: chromeStub,
  location: { pathname, host: host || 'scixplorer.org', href: 'https://' + (host || 'scixplorer.org') + pathname, search: '' },
  console: {
    log: (...a) => logs.push(a.join(' ')),
    info: (...a) => logs.push(a.join(' ')),
    warn: (...a) => logs.push('WARN ' + a.join(' ')),
    error: (...a) => logs.push('ERROR ' + a.join(' ')),
    debug: (...a) => logs.push(a.join(' ')),
  },
  confirm: () => true,
  setInterval: (fn, ms) => { intervals.push({ fn, ms }); return intervals.length; },
  clearInterval: () => {},
  setTimeout: (fn) => { Promise.resolve().then(fn); return 0; },
  clearTimeout: () => {},
  URL, Map, Set, Promise, encodeURIComponent, decodeURIComponent,
  JSON, Math, String, Number, Boolean, Array, Object, RegExp, Error, Date, isNaN, parseInt, parseFloat,
  // window is the sandbox itself, so these must exist as globals.
  addEventListener: () => {},
  removeEventListener: () => {},
  open: () => null,
  navigator: { userAgent: 'stub' },
  // citation_* meta tags for the arXiv adapter
  __meta: META.meta || {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;

// document.querySelector('meta[name="..."]') support for the arXiv adapter.
documentStub.querySelector = (sel) => {
  const m = /^meta\[name="([^"]+)"\]$/.exec(sel);
  if (m && sandbox.__meta[m[1]] !== undefined) {
    const el = new El('meta');
    el.setAttribute('content', sandbox.__meta[m[1]]);
    return el;
  }
  return null;
};
documentStub.querySelectorAll = (sel) => {
  const m = /^meta\[name="([^"]+)"\]$/.exec(sel);
  if (m && Array.isArray(sandbox.__meta[m[1]])) {
    return sandbox.__meta[m[1]].map((v) => {
      const el = new El('meta');
      el.setAttribute('content', v);
      return el;
    });
  }
  return [];
};

// ---- run the real content script ------------------------------------------
const context = vm.createContext(sandbox);
// Load in manifest order: the shared panel module defines
// window.ArXistantPanel, then the site adapter calls init() on it.
for (const p of scriptPaths) {
  try {
    vm.runInContext(fs.readFileSync(p, 'utf8'), context, { filename: p });
  } catch (e) {
    console.log(JSON.stringify({ fatal: p + ': ' + String(e && e.message), logs, messages }));
    process.exit(0);
  }
}

// Flush the async chain (send() resolves on microtasks), then fire one poll
// tick so SPA re-creation logic is exercised too.
function findDeep(root, sel) {
  const m = /^([.#])([A-Za-z0-9_-]+)$/.exec(sel);
  if (!m) return null;
  const byId = m[1] === '#';
  const stack = root.children.slice();
  while (stack.length) {
    const el = stack.shift();
    if (byId ? el.id === m[2] : el.className.split(/\s+/).includes(m[2])) return el;
    stack.push(...el.children);
  }
  return null;
}
function findDeepAll(root, cls) {
  const out = [];
  const stack = root.children.slice();
  while (stack.length) {
    const el = stack.shift();
    if (el.className.split(/\s+/).includes(cls)) out.push(el);
    stack.push(...el.children);
  }
  return out;
}
function bodyEl0() {
  return bodyEl.children.find(c => c.id === 'arxistant-panel') || null;
}

let clicked = null;
let typed = null;

(async () => {
  for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r));
  for (const iv of intervals) { try { iv.fn(); } catch (e) { logs.push('ERROR poll ' + e.message); } }
  for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r));

  // Optionally type into a rendered input (and press a key) before clicking,
  // so the tag editor can be exercised.
  if (META.__type) {
    const el = findDeep(bodyEl0(), META.__type.selector);
    if (el) {
      // A real user has the caret in the box before typing or pressing Enter,
      // so focus it first: the panel's focus-preservation depends on that.
      el.focus();
      el.value = META.__type.value;
      if (typeof META.__type.caret === 'number') el.setSelectionRange(META.__type.caret, META.__type.caret);
      for (const fn of (el.listeners.input || [])) {
        try { fn({}); } catch (e) { logs.push('ERROR input ' + e.message); }
      }
      if (META.__type.key) {
        for (const fn of (el.listeners.keydown || [])) {
          try { fn({ key: META.__type.key, preventDefault() {} }); }
          catch (e) { logs.push('ERROR keydown ' + e.message); }
        }
      }
      typed = META.__type;
    } else {
      typed = META.__type.selector + ' (not found)';
    }
    for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r));
  }

  // Optionally click a rendered control (e.g. '.arx-save') to exercise the
  // relay payloads, then flush again.
  // __click may be one selector or a comma-separated sequence, fired in order
  // with a flush between, so "stage a tag then save" can be exercised.
  const clickSeq = META.__click
    ? String(META.__click).split(',').map(x => x.trim()).filter(Boolean) : [];
  const clickReport = [];
  for (const sel of clickSeq) {
    const root = bodyEl0();
    const target = root ? findDeep(root, sel) : null;
    if (target) {
      if (typeof target.focus === 'function') target.focus();
      for (const fn of (target.listeners.click || [])) {
        try { fn({ stopPropagation() {}, preventDefault() {} }); }
        catch (e) { logs.push('ERROR click ' + e.message); }
      }
      clickReport.push(sel);
    } else {
      clickReport.push(sel + ' (not found)');
    }
    for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r));
  }
  clicked = clickSeq.length ? clickReport.join(',') : null;

  const panel = attached.find(a => a.id === 'arxistant-panel');
  const panelEl = bodyEl.children.find(c => c.id === 'arxistant-panel')
    || htmlEl.children.find(c => c.id === 'arxistant-panel');
  const bodyDiv = panelEl ? panelEl.children.find(c => c.className === 'arx-body') : null;
  console.log(JSON.stringify({
    attached,
    panelAttached: !!panel,
    panelParent: panel ? panel.parent : null,
    panelVisible: panelEl ? panelEl.style.display !== 'none' : false,
    panelHtml: bodyDiv ? bodyDiv.innerHTML : '',
    // The suggestion list is rewritten in place after render, so it is not
    // part of the body's innerHTML snapshot; report it separately.
    suggestHtml: (() => {
      const pe = bodyEl0();
      const box = pe ? findDeep(pe, '.arx-suggest') : null;
      return box ? box.innerHTML : '';
    })(),
    // setStatus() writes into the .arx-status child, so it is not part of the
    // body's innerHTML snapshot either.
    statusHtml: (() => {
      const pe = bodyEl0();
      const el = pe ? findDeep(pe, '.arx-status') : null;
      return el ? el.innerHTML : '';
    })(),
    focusedClass: focusedEl ? focusedEl.className : null,
    focusedValue: focusedEl && focusedEl.value !== undefined ? focusedEl.value : null,
    statusClass: (() => {
      const pe = bodyEl0();
      const el = pe ? findDeep(pe, '.arx-status') : null;
      return el ? el.className : '';
    })(),
    tagInputValue: (() => {
      const pe = bodyEl0();
      const el = pe ? findDeep(pe, '.arx-tag-input') : null;
      return el ? (el.value || '') : null;
    })(),
    messages: messages.map(m => m.action),
    resolveIdentifier: (messages.find(m => m.action === 'scixResolve') || {}).identifier || null,
    clicked: clicked,
    typed: typed,
    tagChips: null,
    tagChips: (() => {
      const pe = bodyEl0();
      if (!pe) return [];
      return findDeepAll(pe, 'arx-tag').map(e => e._text || '');
    })(),
    tagSent: ((messages.find(m => m.action === 'savePaper') || {}).tags) || null,
    updateTagsPayload: (() => {
      const m = messages.filter(x => x.action === 'updateTags');
      return m.length ? m[m.length - 1] : null;
    })(),
    savePayload: (messages.find(m => m.action === 'savePaper') || {}).paper || null,
    saveKey: ((messages.find(m => m.action === 'savePaper') || {}).paper || {}).id || null,
    deleteKey: (messages.find(m => m.action === 'deletePaper') || {}).key || null,
    logs,
    intervalCount: intervals.length,
  }, null, 0));
})();
