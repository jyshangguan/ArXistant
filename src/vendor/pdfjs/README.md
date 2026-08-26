# PDF.js runtime

This directory contains the minified browser runtime and worker from
`pdfjs-dist` 4.10.38. PDF.js is distributed under the Apache License 2.0; the
upstream license is included in `LICENSE`.

Only the two files needed by ArXistant's local, layout-preserving PDF reader
are vendored. They are served locally by `arxiv_db_server.py`; the reader does
not contact a CDN.
