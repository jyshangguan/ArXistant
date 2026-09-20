// ArXistant adapter for scixplorer.org.
//
// scixplorer.org is an AWS-WAF-protected React SPA, so the local server cannot
// fetch its pages. This script runs inside the already-rendered page and reads
// the paper's ADS bibcode from the URL only — never from scixplorer's own
// markup — so a site redesign can at most make the panel disappear, never break
// the page. Metadata comes from the server's /api/scix/resolve relay.
//
// The panel itself lives in content-panel.js, shared with arxiv.org.

(function () {
  'use strict';

  // Paper routes are /abs/<bibcode> or /detail/<bibcode>, optionally followed
  // by a sub-page segment. SciXplorer inherits ADS Classic's route shape, so a
  // live paper page is normally /abs/<bibcode>/abstract — plus /citations,
  // /references, /metrics, /graphics and /exportcitation. An earlier version
  // anchored this pattern at end-of-path, so none of those matched and the
  // panel never appeared.
  //
  // The capture stops at the first "/", "?" or "#"; the decoded value is then
  // validated separately, because location.pathname keeps percent-encoding
  // (A&A bibcodes appear as 2020A%26A...) and the "arXiv:<id>" form has a ":".
  const PAPER_ROUTE_RE = /^\/(?:abs|detail)\/([^/?#]+)/;
  const BIBCODE_RE = /^[A-Za-z0-9.&%\-_:]{6,40}$/;

  const cache = new Map();   // bibcode -> {paper, error}

  function parse(pathname) {
    const m = PAPER_ROUTE_RE.exec(pathname);
    if (!m) return null;
    let raw;
    try { raw = decodeURIComponent(m[1]); } catch (e) { raw = m[1]; }
    return BIBCODE_RE.test(raw) ? raw : null;
  }

  async function fetchPaper(bibcode, send) {
    if (cache.has(bibcode)) return cache.get(bibcode);
    const resp = await send('scixResolve', { identifier: bibcode });
    const paper = (resp.success && resp.paper) ? resp.paper : null;
    const result = {
      paper: paper,
      error: paper ? '' : (resp.error || 'SciX has no record for ' + bibcode + '.'),
    };
    cache.set(bibcode, result);
    return result;
  }

  window.ArXistantPanel.init({
    site: 'scixplorer',
    poll: true,          // SPA: the pathname changes without a document reload
    parse: parse,
    fetchPaper: fetchPaper,
    guard: '__arxistantScixPanel',
    label: function (paper) {
      // A record with an arXiv version is stored under its arXiv ID, so say
      // which identifier the user is actually looking at.
      return window.ArXistantPanel.isArxivId(paper.id)
        ? 'arXiv:' + paper.id
        : 'SciX:' + (paper.bibcode || paper.id);
    },
  });
})();
