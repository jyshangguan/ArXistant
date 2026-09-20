// ArXistant content script for scixplorer.org (dev_sci)
//
// scixplorer.org is an AWS-WAF-protected React SPA: the local Python server
// cannot scrape its pages. This script runs inside the rendered page instead,
// reads the paper's ADS bibcode from the URL (never from the DOM), and shows a
// small ArXistant panel: show abstract, save to the library, open in Chat.
//
// All requests to the local server are relayed through the background service
// worker (chrome.runtime.sendMessage): the worker's fetches are covered by the
// extension's host permission, so page-level mixed-content and private-network
// restrictions never apply. The panel itself is fully self-contained DOM; it
// never inspects or depends on scixplorer's own markup.

(() => {
  'use strict';

  // Paper routes are /abs/<bibcode> or /detail/<bibcode>, optionally followed
  // by a sub-page segment. SciXplorer inherits ADS Classic's route shape, so a
  // live paper page is normally /abs/<bibcode>/abstract — plus /citations,
  // /references, /metrics, /graphics and /exportcitation. An earlier version
  // anchored this pattern at the end of the path, so none of those matched and
  // the panel never appeared.
  //
  // The capture stops at the first "/", "?" or "#"; the decoded value is then
  // validated separately, because location.pathname keeps percent-encoding
  // (A&A bibcodes appear as 2020A%26A...) and the "arXiv:<id>" form has a ":".
  const PAPER_ROUTE_RE = /^\/(?:abs|detail)\/([^/?#]+)/;
  const BIBCODE_RE = /^[A-Za-z0-9.&%\-_:]{6,40}$/;
  const ARXIV_ID_RE = /^(?:\d{4}\.\d{4,5}|[a-z\-]+\/\d{7})$/;
  const POLL_MS = 500;

  const state = {
    bibcode: null,          // bibcode of the current scixplorer page
    paper: null,            // resolved paper (storage key follows the rule)
    savedIds: null,         // Set of saved keys, loaded once per panel life
    collapsed: false,
    abstractOpen: false,
  };
  const resolveCache = new Map();  // bibcode -> {paper, error}

  // ---------- helpers ----------

  function send(action, extra = {}) {
    return new Promise(resolve => {
      try {
        chrome.runtime.sendMessage({ action, ...extra }, response => {
          if (chrome.runtime.lastError) {
            resolve({ success: false, error: chrome.runtime.lastError.message });
          } else {
            resolve(response || { success: false, error: 'No response' });
          }
        });
      } catch (e) {
        resolve({ success: false, error: e.message });
      }
    });
  }

  function isArxivId(key) {
    return ARXIV_ID_RE.test(String(key || '').replace(/v\d+$/, ''));
  }

  function escapeText(text) {
    const div = document.createElement('div');
    div.textContent = String(text == null ? '' : text);
    return div.innerHTML;
  }

  function log(level, msg) {
    // Diagnostics matter here: the panel is invisible when the route does not
    // match, so without a log line there is no way to tell from the page
    // whether the script ran at all or simply found no paper identifier.
    try { console[level]('[ArXistant]', msg); } catch (e) { /* no console */ }
  }

  function currentBibcode() {
    try {
      const m = PAPER_ROUTE_RE.exec(location.pathname);
      if (!m) return null;
      let raw;
      try { raw = decodeURIComponent(m[1]); } catch (e) { raw = m[1]; }
      if (!BIBCODE_RE.test(raw)) {
        log('warn', 'unrecognised paper identifier in the URL, panel skipped: ' + raw);
        return null;
      }
      return raw;
    } catch (e) {
      return null;
    }
  }

  // ---------- panel DOM ----------

  let panel = null;

  function ensurePanel() {
    if (panel && panel.isConnected) return panel;
    panel = document.createElement('div');
    panel.id = 'arxistant-panel';

    const head = document.createElement('div');
    head.className = 'arx-head';
    head.innerHTML = '<span class="arx-brand">🛰 ArXistant</span>' +
      '<button class="arx-collapse" title="Collapse panel">–</button>';
    const body = document.createElement('div');
    body.className = 'arx-body';

    head.querySelector('.arx-collapse').addEventListener('click', () => {
      state.collapsed = !state.collapsed;
      render();
    });

    panel.appendChild(head);
    panel.appendChild(body);
    // Attach under <body>. Appending to documentElement makes the panel a
    // sibling of <head> and <body>, which is invalid placement that some page
    // CSS lays out incorrectly or hides.
    (document.body || document.documentElement).appendChild(panel);
    log('info', 'panel attached');
    return panel;
  }

  function bodyEl() {
    return panel ? panel.querySelector('.arx-body') : null;
  }

  function setStatus(text, isError) {
    const el = bodyEl() && bodyEl().querySelector('.arx-status');
    if (!el) return;
    el.innerHTML = text ? escapeText(text) : '';
    el.classList.toggle('arx-error', !!isError);
  }

  // ---------- data ----------

  async function loadSavedIds() {
    const resp = await send('scixSavedPapers');
    if (resp.success && Array.isArray(resp.ids)) {
      state.savedIds = new Set(resp.ids);
    } else {
      state.savedIds = null;  // offline server: keep panel usable, show error
    }
  }

  async function resolvePaper(bibcode) {
    if (resolveCache.has(bibcode)) return resolveCache.get(bibcode);
    const resp = await send('scixResolve', { identifier: bibcode });
    const paper = (resp.success && resp.paper) ? resp.paper : null;
    const result = {
      paper,
      error: paper ? '' : (resp.error || 'SciX has no record for ' + bibcode + '.'),
    };
    resolveCache.set(bibcode, result);
    return result;
  }

  async function toggleSave() {
    if (!state.paper) return;
    const key = state.paper.id;
    const wasSaved = state.savedIds && state.savedIds.has(key);
    if (wasSaved && !confirm('Remove this paper from your ArXistant library?')) return;
    setStatus(wasSaved ? 'Removing…' : 'Saving…');
    const resp = wasSaved
      ? await send('scixDeletePaper', { key })
      : await send('scixSavePaper', { paper: state.paper });
    if (!resp.success) {
      setStatus(resp.error || 'The server rejected the change.', true);
      return;
    }
    if (state.savedIds) {
      if (wasSaved) state.savedIds.delete(key);
      else state.savedIds.add(key);
    } else {
      // The library list failed to load earlier (server was offline), but
      // this relay succeeded — start tracking from this key so the button
      // reflects the new state.
      state.savedIds = new Set([key]);
    }
    setStatus('');
    render();
  }

  function openChat() {
    if (!state.paper) return;
    // The chat page pins any resolvable identifier; the storage key keeps
    // daily-page saves and scixplorer saves unified.
    const origin = state.serverOrigin || 'http://localhost:8765';
    const href = origin + '/chat.html?paper=' + encodeURIComponent(state.paper.id);
    window.open(href, '_blank', 'noopener');
  }

  // ---------- rendering ----------

  function render() {
    const p = ensurePanel();
    const body = bodyEl();
    if (!body) return;

    p.classList.toggle('arx-collapsed', state.collapsed);

    if (!state.bibcode) {
      p.style.display = 'none';
      return;
    }
    p.style.display = '';

    if (state.collapsed) {
      body.innerHTML = '';
      return;
    }

    const paper = state.paper;
    let html = '';

    if (!paper) {
      html = '<div class="arx-msg">' +
        (state.resolveError
          ? escapeText(state.resolveError)
          : 'Resolving this paper…') +
        '</div>';
    } else {
      const keyLabel = isArxivId(paper.id)
        ? 'arXiv:' + paper.id : 'SciX:' + (paper.bibcode || paper.id);
      const saved = state.savedIds && state.savedIds.has(paper.id);
      const meta = [paper.year,
        paper.citation_count ? paper.citation_count + ' citations' : '']
        .filter(Boolean).join(' · ');
      html =
        '<div class="arx-key">' + escapeText(keyLabel) + '</div>' +
        '<div class="arx-title">' + escapeText(paper.title || paper.id) + '</div>' +
        (meta ? '<div class="arx-meta">' + escapeText(meta) + '</div>' : '') +
        '<button class="arx-abs-btn">' +
        (state.abstractOpen ? '▾ Hide abstract' : '▸ Show abstract') + '</button>' +
        (state.abstractOpen
          ? '<div class="arx-abstract">' +
            escapeText(paper.abstract || 'No abstract available.') + '</div>'
          : '') +
        '<div class="arx-actions">' +
        '<button class="arx-save' + (saved ? ' arx-saved' : '') + '">' +
        (saved ? '✓ Saved' : '💾 Save') + '</button>' +
        '<button class="arx-chat">💬 Chat</button>' +
        '</div>' +
        '<div class="arx-status"></div>';
    }
    body.innerHTML = html;

    const absBtn = body.querySelector('.arx-abs-btn');
    if (absBtn) absBtn.addEventListener('click', () => {
      state.abstractOpen = !state.abstractOpen;
      render();
    });
    const saveBtn = body.querySelector('.arx-save');
    if (saveBtn) saveBtn.addEventListener('click', toggleSave);
    const chatBtn = body.querySelector('.arx-chat');
    if (chatBtn) chatBtn.addEventListener('click', openChat);

    if (state.serverError) {
      setStatus(state.serverError, true);
    } else if (state.savedIds === null) {
      setStatus('Could not reach the ArXistant server — start it from the ' +
        'extension popup, then reload this page.', true);
    }
  }

  // ---------- route watching (SPA-safe) ----------

  let lastPath = null;

  async function onRouteChange() {
    const bibcode = currentBibcode();
    if (bibcode === state.bibcode) return;
    state.bibcode = bibcode;
    state.paper = null;
    state.resolveError = '';
    state.serverError = '';
    state.abstractOpen = false;
    log('info', bibcode ? 'paper page, bibcode ' + bibcode
                       : 'not a paper page (' + location.pathname + ')');
    render();

    if (!bibcode) return;

    if (state.savedIds === null && !state.savedIdsTried) {
      state.savedIdsTried = true;
      const resp = await send('getSettings');
      if (resp.success && resp.settings && resp.settings.serverUrl) {
        try {
          state.serverOrigin = new URL(resp.settings.serverUrl).origin;
        } catch (e) { /* default handled at openChat */ }
      }
      await loadSavedIds();
      if (state.savedIds === null) state.serverError =
        'Could not reach the ArXistant server.';
    }

    if (state.serverOrigin == null) {
      const resp = await send('getSettings');
      if (resp.success && resp.settings && resp.settings.serverUrl) {
        try { state.serverOrigin = new URL(resp.settings.serverUrl).origin; } catch (e) {}
      }
    }

    try {
      const { paper, error } = await resolvePaper(bibcode);
      if (state.bibcode !== bibcode) return;  // navigated away meanwhile
      state.paper = paper;
      state.resolveError = error;
    } catch (e) {
      if (state.bibcode === bibcode) state.resolveError = e.message;
    }
    render();
  }

  function watch() {
    const fail = e => log('error', 'route handling failed: ' + (e && e.message));
    setInterval(() => {
      if (location.pathname !== lastPath) {
        lastPath = location.pathname;
        onRouteChange().catch(fail);
      } else if (state.bibcode && panel && !panel.isConnected) {
        // The SPA replaced the DOM underneath us; put the panel back.
        panel = null;
        render();
      }
    }, POLL_MS);
    window.addEventListener('popstate', () => {
      lastPath = null;
      onRouteChange().catch(fail);
    });
  }

  function start() {
    if (window.__arxistantScixPanel) return;  // guard double injection
    window.__arxistantScixPanel = true;
    log('info', 'content script active on ' + location.host);
    watch();
    // Do not wait for the first poll tick on a page that is already a paper.
    onRouteChange().catch(e => log('error', e && e.message));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
