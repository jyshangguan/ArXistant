"""Fetch and parse arXiv HTML pages into structured text."""

from __future__ import annotations

import hashlib
import logging
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from .types import FigureInfo, ParsedPaper

logger = logging.getLogger(__name__)

ARXIV_HTML_BASE = "https://arxiv.org/html/"


class PaperHtmlUnavailableError(Exception):
    """Raised when the arXiv HTML version of a paper is not available."""


def fetch_arxiv_html(arxiv_id: str, timeout: int = 30) -> BeautifulSoup:
    """Fetch the arXiv HTML page for a paper and return a parsed BeautifulSoup.

    Raises PaperHtmlUnavailableError on 404 or other HTTP errors.
    """
    url = f"{ARXIV_HTML_BASE}{arxiv_id}"
    logger.info("Fetching arXiv HTML: %s", url)
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 404:
            raise PaperHtmlUnavailableError(
                f"HTML version not available for {arxiv_id} (404)"
            )
        resp.raise_for_status()
    except requests.RequestException as e:
        if isinstance(e, PaperHtmlUnavailableError):
            raise
        raise PaperHtmlUnavailableError(
            f"Failed to fetch HTML for {arxiv_id}: {e}"
        ) from e

    return BeautifulSoup(resp.text, "lxml")


def _resolve_base_url(soup: BeautifulSoup, arxiv_id: str) -> str:
    """Get the base URL for resolving relative image URLs."""
    base_tag = soup.find("base", href=True)
    if base_tag:
        href = base_tag["href"]
        if not href.startswith("http"):
            href = f"https://arxiv.org{href}"
        return href
    return f"{ARXIV_HTML_BASE}{arxiv_id}/"


def _extract_title(soup: BeautifulSoup) -> str:
    """Extract the paper title from the HTML."""
    tag = soup.find("h1", class_="ltx_title")
    if tag:
        return tag.get_text(strip=True)
    # Fallback to document title
    title_tag = soup.find("title")
    return title_tag.get_text(strip=True) if title_tag else ""


def _extract_abstract(soup: BeautifulSoup) -> str:
    """Extract the abstract text."""
    abstract_div = soup.find("div", class_="ltx_abstract")
    if not abstract_div:
        return ""
    # Get all paragraph text within abstract
    parts = []
    for p in abstract_div.find_all("p"):
        text = p.get_text(strip=True)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _strip_unwanted(soup: BeautifulSoup) -> None:
    """Remove navigation, bibliography, and other non-content elements."""
    for selector in [
        "nav",                          # navigation bars
        ".ltx_page_navbar",
        ".ltx_bibliography",
        ".ltx_appendix",
        "footer",
        "header.ltx_page_header",
        "div.ltx_page_navigation",
    ]:
        for el in soup.select(selector):
            el.decompose()


def _extract_sections(soup: BeautifulSoup) -> list[dict]:
    """Extract sections with their titles and text content."""
    sections = []
    seen_titles = set()

    for section in soup.find_all("section"):
        # Look for section heading
        heading = section.find(
            ["h1", "h2", "h3"], class_=lambda c: c and "ltx_title" in c
        )
        if not heading:
            continue

        # Get section number if available
        number = ""
        number_tag = heading.find(class_="ltx_tag")
        if number_tag:
            number = number_tag.get_text(strip=True)
            number_tag.decompose()

        title = heading.get_text(strip=True)

        # Skip duplicates (sections can be nested)
        if title in seen_titles:
            continue
        seen_titles.add(title)

        # Extract text from paragraphs in this section (direct children only,
        # not from nested sections)
        text_parts = []
        for child in section.children:
            if isinstance(child, Tag) and child.name == "section":
                continue  # skip nested sections
            if isinstance(child, Tag) and child.name in ("p", "div"):
                text = child.get_text(strip=True, separator=" ")
                if text:
                    text_parts.append(text)

        section_text = "\n".join(text_parts)
        sections.append({
            "number": number,
            "title": title,
            "text": section_text,
        })

    return sections


def _extract_figures(
    soup: BeautifulSoup, base_url: str, sections: list[dict]
) -> list[FigureInfo]:
    """Extract figure metadata (URL + caption) from the document."""
    figures = []
    idx = 0

    for fig in soup.find_all("figure"):
        img = fig.find("img", class_="ltx_graphics")
        if not img:
            continue

        src = img.get("src", "")
        url = urljoin(base_url, src) if src else ""

        caption = ""
        caption_tag = fig.find("figcaption")
        if caption_tag:
            caption = caption_tag.get_text(strip=True, separator=" ")

        # Find which section contains this figure
        section_title = ""
        parent_section = fig.find_parent("section")
        if parent_section:
            h = parent_section.find(
                ["h1", "h2", "h3"], class_=lambda c: c and "ltx_title" in c
            )
            if h:
                section_title = h.get_text(strip=True)

        figures.append(FigureInfo(
            index=idx,
            url=url,
            caption=caption,
            section=section_title,
        ))
        idx += 1

    return figures


def _build_full_text_markdown(
    title: str, abstract: str, sections: list[dict], figures: list[FigureInfo]
) -> str:
    """Build a clean markdown representation of the paper."""
    parts = []

    parts.append(f"# {title}\n")

    if abstract:
        parts.append("## Abstract\n")
        parts.append(abstract)
        parts.append("")

    for s in sections:
        prefix = f"{s['number']} " if s["number"] else ""
        parts.append(f"## {prefix}{s['title']}\n")
        if s["text"]:
            parts.append(s["text"])
            parts.append("")

    if figures:
        parts.append("## Figure Captions\n")
        for fig in figures:
            parts.append(f"**Figure {fig.index + 1}**")
            if fig.section:
                parts.append(f"*(Section: {fig.section})*")
            if fig.caption:
                parts.append(fig.caption)
            parts.append("")

    return "\n".join(parts)


def compute_hash(text: str) -> str:
    """Compute SHA-256 hash of text for cache invalidation."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fetch_and_parse(arxiv_id: str, timeout: int = 30) -> ParsedPaper:
    """Fetch an arXiv HTML page and parse it into structured data.

    Args:
        arxiv_id: The arXiv identifier (e.g. "2504.12345").
        timeout: HTTP request timeout in seconds.

    Returns:
        ParsedPaper with title, abstract, sections, figures, and full text.

    Raises:
        PaperHtmlUnavailableError: If the HTML version is not available.
    """
    soup = fetch_arxiv_html(arxiv_id, timeout=timeout)
    base_url = _resolve_base_url(soup, arxiv_id)

    title = _extract_title(soup)
    abstract = _extract_abstract(soup)

    # Strip nav/bibliography before extracting sections and figures
    _strip_unwanted(soup)

    sections = _extract_sections(soup)
    figures = _extract_figures(soup, base_url, sections)

    full_text_markdown = _build_full_text_markdown(title, abstract, sections, figures)
    full_text_hash = compute_hash(full_text_markdown)

    return ParsedPaper(
        arxiv_id=arxiv_id,
        title=title,
        abstract=abstract,
        sections=sections,
        figures=figures,
        full_text_markdown=full_text_markdown,
        full_text_hash=full_text_hash,
    )
