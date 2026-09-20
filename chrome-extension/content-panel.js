// Shared ArXistant page panel.
//
// One implementation of the floating panel — show abstract / save / chat —
// driven by a small per-site adapter, so scixplorer.org and arxiv.org behave
// identically instead of each carrying a copy of the same logic.
//
// An adapter supplies:
//   site              name used in console diagnostics
//   poll              true for SPA sites whose pathname changes without a reload
//   parse(pathname)   the paper identifier for this URL, or null
//   fetchPaper(id)    -> {paper, error}; from the page itself or via the server
//   label(paper)      the identifier line shown at the top of the panel
//   guard             window property preventing double injection
//
// All server access goes through the background service worker, whose fetches
// are covered by the extension's host permission. A page script fetching
// http://localhost from an https page would be blocked as mixed content, and
// would also run into Private Network Access restrictions.

(function () {
  'use strict';

  const ARXIV_ID_RE = /^(?:\d{4}\.\d{4,5}|[a-z\-]+\/\d{7})$/;
  const POLL_MS = 500;

  function send(action, extra) {
    return new Promise(resolve => {
      try {
        chrome.runtime.sendMessage(Object.assign({ action: action }, extra || {}),
          response => {
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

  function init(adapter) {
    if (window[adapter.guard]) return;   // double-injection guard
    window[adapter.guard] = true;

    const state = {
      id: null,             // paper identifier for the current page
      paper: null,          // resolved paper; .id is the storage key
      error: '',            // why the paper could not be resolved
      savedIds: null,       // Set of saved keys, or null when unavailable
      savedIdsTried: false,
      serverOrigin: null,
      serverError: '',
      collapsed: false,
      abstractOpen: false,
    };

    let panel = null;
    let lastPath = null;

    function log(level, msg) {
      // Diagnostics matter here: the panel is invisible when the route does
      // not match, so without a log line there is no way to tell from the page
      // whether the script ran at all or simply found no paper identifier.
      try { console[level]('[ArXistant ' + adapter.site + ']', msg); } catch (e) {}
    }

    // ---------- panel DOM ----------

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
      // sibling of <head> and <body>, which is invalid placement that some
      // page CSS lays out incorrectly or hides.
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
      const resp = await send('savedPapers');
      if (resp.success && Array.isArray(resp.ids)) {
        state.savedIds = new Set(resp.ids);
      } else {
        state.savedIds = null;  // offline server: keep the panel usable
      }
    }

    async function loadServerOrigin() {
      const resp = await send('getSettings');
      if (resp.success && resp.settings && resp.settings.serverUrl) {
        try { state.serverOrigin = new URL(resp.settings.serverUrl).origin; } catch (e) {}
      }
    }

    async function toggleSave() {
      if (!state.paper) return;
      const key = state.paper.id;
      const wasSaved = state.savedIds && state.savedIds.has(key);
      if (wasSaved && !confirm('Remove this paper from your ArXistant library?')) return;
      setStatus(wasSaved ? 'Removing…' : 'Saving…');
      const resp = wasSaved
        ? await send('deletePaper', { key: key })
        : await send('savePaper', { paper: state.paper });
      if (!resp.success) {
        setStatus(resp.error || 'The server rejected the change.', true);
        return;
      }
      if (state.savedIds) {
        if (wasSaved) state.savedIds.delete(key);
        else state.savedIds.add(key);
      } else {
        // The library list failed to load earlier (server was offline) but this
        // relay succeeded — start tracking from this key so the button reflects
        // the new state.
        state.savedIds = new Set([key]);
      }
      setStatus('');
      render();
    }

    function openChat() {
      if (!state.paper) return;
      // The Chat page pins any resolvable identifier; using the storage key
      // keeps daily-page saves and page-panel saves on one record.
      const origin = state.serverOrigin || 'http://localhost:8765';
      window.open(origin + '/chat.html?paper=' + encodeURIComponent(state.paper.id),
                  '_blank', 'noopener');
    }

    // ---------- rendering ----------

    function render() {
      const p = ensurePanel();
      const body = bodyEl();
      if (!body) return;

      p.classList.toggle('arx-collapsed', state.collapsed);

      if (!state.id) {
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
          escapeText(state.error || 'Loading this paper…') + '</div>';
      } else {
        const saved = state.savedIds && state.savedIds.has(paper.id);
        const meta = [paper.year,
          paper.citation_count ? paper.citation_count + ' citations' : '']
          .filter(Boolean).join(' · ');
        html =
          '<div class="arx-key">' + escapeText(adapter.label(paper)) + '</div>' +
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

    // ---------- route watching ----------

    async function onRouteChange() {
      let id = null;
      try { id = adapter.parse(location.pathname); } catch (e) { id = null; }
      if (id === state.id) return;
      state.id = id;
      state.paper = null;
      state.error = '';
      state.serverError = '';
      state.abstractOpen = false;
      log('info', id ? 'paper page, identifier ' + id
                     : 'not a paper page (' + location.pathname + ')');
      render();

      if (!id) return;

      if (!state.savedIdsTried) {
        state.savedIdsTried = true;
        await loadServerOrigin();
        await loadSavedIds();
        if (state.savedIds === null) {
          state.serverError = 'Could not reach the ArXistant server.';
        }
      }
      if (state.serverOrigin == null) await loadServerOrigin();

      try {
        const result = await adapter.fetchPaper(id, send);
        if (state.id !== id) return;   // navigated away meanwhile
        state.paper = result.paper;
        state.error = result.error || '';
      } catch (e) {
        if (state.id === id) state.error = e.message;
      }
      render();
    }

    function watch() {
      const fail = e => log('error', 'route handling failed: ' + (e && e.message));
      if (adapter.poll) {
        setInterval(() => {
          if (location.pathname !== lastPath) {
            lastPath = location.pathname;
            onRouteChange().catch(fail);
          } else if (state.id && panel && !panel.isConnected) {
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
      // Server-rendered pages need no polling, and SPA pages should not wait
      // for the first tick when the page already is a paper.
      onRouteChange().catch(fail);
    }

    function start() {
      log('info', 'content script active on ' + location.host);
      watch();
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', start, { once: true });
    } else {
      start();
    }
  }

  window.ArXistantPanel = { init: init, send: send, isArxivId: isArxivId };
})();
