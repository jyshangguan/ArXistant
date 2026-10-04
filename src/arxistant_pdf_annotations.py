"""PDF geometry and native review annotations. No model-generated coordinates."""
import hashlib
import importlib
import os
import re
import site
import sys


def pdf_module():
    """Load PyMuPDF, including packages installed after the server started.

    Python only adds the user site-packages directory during startup when that
    directory already exists. If ``pip install --user`` creates it while the
    ArXistant server is running, an ordinary import cannot see it until restart.
    Refreshing the site directory here makes the feature recover immediately.
    """
    errors = []

    def attempt():
        importlib.invalidate_caches()
        for name in ("pymupdf", "fitz"):
            try:
                module = importlib.import_module(name)
                if hasattr(module, "open") and hasattr(module, "Quad"):
                    return module
                errors.append(f"{name}: incompatible module at "
                              f"{getattr(module, '__file__', 'unknown location')}")
            except (ImportError, OSError) as exc:
                errors.append(f"{name}: {exc}")
        return None

    module = attempt()
    if module is not None:
        return module

    try:
        user_site = site.getusersitepackages()
        candidates = [user_site] if isinstance(user_site, str) else list(user_site)
        for candidate in candidates:
            if candidate and os.path.isdir(candidate) and candidate not in sys.path:
                site.addsitedir(candidate)
    except (AttributeError, OSError, TypeError):
        pass

    module = attempt()
    if module is not None:
        return module

    detail = errors[-1] if errors else "module not found"
    raise ValueError(
        "Manuscript review could not load PyMuPDF with "
        f"{sys.executable}: {detail}. Install it for this interpreter with "
        f"`{sys.executable} -m pip install --user PyMuPDF`, then restart "
        "ArXistant.")


def normalize(text):
    import unicodedata
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", re.sub(r"-\s*\n\s*", "", text)).strip()


def inspect_pdf(path):
    fitz = pdf_module()
    with open(path, "rb") as stream:
        digest = hashlib.sha256(stream.read()).hexdigest()
    pages = []
    with fitz.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("Unlock the PDF before reviewing it.")
        for number, page in enumerate(doc, 1):
            blocks = []
            for i, block in enumerate(page.get_text("blocks", sort=True)):
                if block[6] == 0 and block[4].strip():
                    blocks.append({"id": f"p{number}b{i}", "page": number,
                                   "text": block[4], "rect": list(block[:4])})
            pages.append({"page": number, "blocks": blocks})
    if not any(p["blocks"] for p in pages):
        raise ValueError("This PDF has no selectable text. OCR it before review.")
    return {"hash": digest, "pages": pages}


def locate(path, page_number, quote, block_id=""):
    """Return unique text quads; ambiguous matches deliberately remain page notes."""
    fitz = pdf_module()
    with fitz.open(path) as doc:
        if not 1 <= page_number <= len(doc):
            raise ValueError("Finding page is outside the manuscript.")
        page = doc[page_number - 1]
        clip = None
        if block_id:
            match = re.fullmatch(r"p(\d+)b(\d+)", block_id)
            if not match or int(match[1]) != page_number:
                raise ValueError("Invalid source block.")
            blocks = page.get_text("blocks", sort=True)
            if int(match[2]) >= len(blocks):
                raise ValueError("Unknown source block.")
            clip = fitz.Rect(blocks[int(match[2])][:4])
        text = page.get_text(clip=clip)
        if not quote or normalize(text).count(normalize(quote)) != 1:
            return {"placement": "page-note", "quads": []}
        quads = page.search_for(quote, quads=True, clip=clip)
        if not quads:
            quads = page.search_for(normalize(quote), quads=True, clip=clip)
        rectangles = []
        for quad in quads:
            rect = quad.rect * page.rotation_matrix
            rectangles.append([rect.x0 / page.rect.width, rect.y0 / page.rect.height,
                               rect.x1 / page.rect.width, rect.y1 / page.rect.height])
        return {"placement": "highlight" if quads else "page-note", "view_rects": rectangles,
                "quads": [[v for point in q for v in point] for q in quads]}


def page_image(path, page_number):
    import base64
    fitz = pdf_module()
    with fitz.open(path) as doc:
        page = doc[page_number - 1]
        scale = min(2, 1500 / max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), annots=False)
        return "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()


def existing_comments(path):
    """Expose native comments for discussion while retaining their original PDF objects."""
    fitz = pdf_module()
    comments = []
    with fitz.open(path) as doc:
        for number, page in enumerate(doc, 1):
            for annotation in page.annots() or []:
                info = annotation.info
                if not info.get('content', '').strip():
                    continue
                quads, rectangles, quotes = [], [], []
                if annotation.type[0] in (8, 9, 10, 11) and annotation.vertices:
                    vertices = annotation.vertices
                    for i in range(0, len(vertices), 4):
                        quad = fitz.Quad(vertices[i:i+4])
                        quads.append([v for point in quad for v in point])
                        rect = quad.rect * page.rotation_matrix
                        rectangles.append([rect.x0/page.rect.width, rect.y0/page.rect.height,
                                           rect.x1/page.rect.width, rect.y1/page.rect.height])
                        quotes.append(page.get_textbox(quad.rect))
                comments.append({'page': number, 'quote': '\n'.join(quotes).strip(),
                                 'block_id': '', 'comment': info['content'],
                                 'author': info.get('title') or 'Existing reviewer',
                                 'quads': quads, 'view_rects': rectangles,
                                 'placement': 'highlight' if quads else 'page-note'})
    return comments


def export_pdf(path, findings, expected_hash):
    fitz = pdf_module()
    with open(path, "rb") as stream:
        if hashlib.sha256(stream.read()).hexdigest() != expected_hash:
            raise ValueError("The source PDF changed. Open it as a new version.")
    colors = {"language": (1, .87, .4), "citation": (.65, .85, 1),
              "figure": (.7, .95, .7), "structure": (.88, .7, .95)}
    ids = set()
    with fitz.open(path) as doc:
        margins = {}
        for finding in findings:
            fid = finding["id"]
            if fid in ids:
                continue
            ids.add(fid)
            page = doc[finding["page"] - 1]
            quads = finding.get("quads", [])
            if quads:
                annot = page.add_highlight_annot([
                    fitz.Quad([fitz.Point(q[i], q[i+1]) for i in range(0, 8, 2)])
                    for q in quads])
                annot.set_colors(stroke=colors.get(finding["category"], (1, .87, .4)))
            else:
                offset = margins.get(page.number, 0)
                margins[page.number] = offset + 1
                # Notes are intentionally visible when precise placement is unavailable.
                height = max(24, page.cropbox.height - 48)
                annot = page.add_text_annot((18 + 22 * (offset // max(1, int(height/24))),
                                             24 + (offset * 24) % height), "")
            body = f"[{finding['category']} / {finding.get('severity', 'suggestion')}]\n{finding['comment']}"
            if finding.get("quote"):
                body += "\n\nPassage: " + finding["quote"]
            if finding.get("replacement"):
                body += "\n\nSuggested wording: " + finding["replacement"]
            if not quads:
                body += "\n\nLocation: page note; exact text placement unavailable."
            for evidence in finding.get("evidence", []):
                body += "\n\nEvidence: " + str(evidence)
            annot.set_info(title="ArXistant", subject="ArXistant:" + fid, content=body)
            annot.update()
        result = doc.tobytes(garbage=3, deflate=True)
    with fitz.open(stream=result, filetype="pdf") as check:
        found = {a.info.get("subject", "")[10:] for p in check for a in (p.annots() or [])
                 if a.info.get("subject", "").startswith("ArXistant:")}
        if not ids.issubset(found):
            raise ValueError("PDF annotation verification failed.")
    return result
