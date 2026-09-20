// ArXistant adapter for arxiv.org abstract pages.
//
// arxiv.org is server-rendered, so no route polling is needed and the manifest
// scopes this script to /abs/* only — it never loads on list or search pages.
//
// Every /abs/ page carries complete citation_* metadata (citation_arxiv_id,
// citation_title, citation_author, citation_abstract, citation_doi,
// citation_date). That is the same Highwire/Google-Scholar convention browser
// reference managers read, so the panel needs no ADS token, no network
// round-trip and no arXiv API call — which also keeps it clear of arXiv's rate
// limiter. The server is contacted only to save, and as a fallback if the meta
// tags are ever missing.

(function () {
  'use strict';

  const ABS_ROUTE_RE = /^\/abs\/(.+)$/;

  function meta(name) {
    const el = document.querySelector('meta[name="' + name + '"]');
    return el ? (el.getAttribute('content') || '').trim() : '';
  }

  function metaAll(name) {
    return Array.prototype.slice
      .call(document.querySelectorAll('meta[name="' + name + '"]'))
      .map(el => (el.getAttribute('content') || '').trim())
      .filter(Boolean);
  }

  function stripVersion(id) {
    return String(id || '').replace(/v\d+$/, '');
  }

  function parse(pathname) {
    const m = ABS_ROUTE_RE.exec(pathname);
    if (!m) return null;
    let raw = m[1];
    try { raw = decodeURIComponent(raw); } catch (e) { /* keep as-is */ }
    // Old-style IDs are /abs/math/0102001, so interior slashes are kept.
    raw = raw.replace(/\/+$/, '');
    // Drop the version so the storage key matches the Daily page's, which is
    // version-less; otherwise the same paper would save as a second row.
    return stripVersion(raw) || null;
  }

  async function fetchPaper(urlId, send) {
    const id = stripVersion(meta('citation_arxiv_id') || urlId);
    if (!id) {
      return { paper: null, error: 'Could not read an arXiv identifier from this page.' };
    }
    const title = meta('citation_title');
    const abstract = meta('citation_abstract');
    if (!title && !abstract) {
      // Unusual page shape: fall back to the server, which looks the paper up
      // on the arXiv API. That path needs no ADS token.
      const resp = await send('arxivResolve', { identifier: id });
      if (resp.success && resp.paper) return { paper: resp.paper, error: '' };
      return { paper: null, error: resp.error || 'Could not read this paper on the page or from the server.' };
    }
    return {
      paper: {
        id: id,
        title: title || document.title,
        authors: metaAll('citation_author'),
        abstract: abstract,
        year: meta('citation_date').slice(0, 4),
        doi: meta('citation_doi'),
        bibcode: '',
        citation_count: 0,
        source: 'arxiv',
        is_arxiv: true,
      },
      error: '',
    };
  }

  window.ArXistantPanel.init({
    site: 'arxiv',
    poll: false,         // server-rendered: every navigation is a fresh document
    parse: parse,
    fetchPaper: fetchPaper,
    guard: '__arxistantArxivPanel',
    label: function (paper) { return 'arXiv:' + paper.id; },
  });
})();
