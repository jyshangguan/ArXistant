"""Tests for the arXiv HTML parser."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.tools.html_parser import (
    PaperHtmlUnavailableError,
    fetch_and_parse,
    fetch_arxiv_html,
    compute_hash,
)
from src.tools.types import ParsedPaper


SAMPLE_ARXIV_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Dynamics of Barred Spiral Galaxies</title>
  <base href="/html/2504.12345v1/">
</head>
<body>
  <nav class="ltx_page_navbar">
    <a href="/abs/2504.12345">Abstract</a>
    <a href="/pdf/2504.12345">PDF</a>
  </nav>

  <div class="ltx_page_header">
    <span class="ltx_tag">astro-ph.GA</span>
  </div>

  <header class="ltx_page_header">
    <h1 class="ltx_title ltx_title_document">Dynamics of Barred Spiral Galaxies in the Local Universe</h1>
    <div class="ltx_authors"><span class="ltx_person">Alice Smith</span>, <span class="ltx_person">Bob Jones</span></div>
  </header>

  <div class="ltx_abstract">
    <h2 class="ltx_title ltx_title_abstract">Abstract</h2>
    <p>We present a comprehensive study of barred spiral galaxies using data from the SDSS and Gaia surveys. Our sample includes over 5000 galaxies within 100 Mpc, and we analyze the bar fraction as a function of stellar mass and environment.</p>
  </div>

  <section class="ltx_section">
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">1</span> Introduction</h2>
    <p>Barred spiral galaxies are a common morphological class in the local universe. Approximately 30% of disk galaxies host a bar, and this fraction increases with stellar mass.</p>
    <p>In this paper we study the dynamics of barred galaxies using a large sample from SDSS DR18.</p>
  </section>

  <section class="ltx_section">
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">2</span> Methodology</h2>
    <p>We use a combination of visual classification and automated bar detection to identify barred galaxies in our sample.</p>
    <figure class="ltx_figure">
      <img class="ltx_graphics" src="fig1.png" alt="Bar fraction vs stellar mass">
      <figcaption class="ltx_caption">
        <span class="ltx_tag">Figure 1:</span> Bar fraction as a function of stellar mass for our sample.
      </figcaption>
    </figure>
    <p>The bar detection algorithm uses Fourier decomposition of the azimuthal light distribution.</p>
  </section>

  <section class="ltx_section">
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">3</span> Results</h2>
    <p>We find that the bar fraction increases from 15% at log(M*/Msun) = 10 to 45% at log(M*/Msun) = 11.5. The bar length scales with disk scale length as Lbar/hR ~ 0.3-0.5.</p>
    <figure class="ltx_figure">
      <img class="ltx_graphics" src="fig2.png" alt="Bar length distribution">
      <figcaption class="ltx_caption">
        <span class="ltx_tag">Figure 2:</span> Distribution of relative bar lengths.
      </figcaption>
    </figure>
    <p>Environment plays a significant role: bars are more common in isolated galaxies compared to cluster members.</p>
  </section>

  <section class="ltx_section">
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">4</span> Conclusions</h2>
    <p>We have presented the largest study of bar fraction to date. Our results confirm that bar formation is strongly correlated with stellar mass and inversely correlated with environmental density.</p>
  </section>

  <section class="ltx_bibliography">
    <h2 class="ltx_title ltx_title_section">References</h2>
    <p>[1] Kormendy &amp; Kennicutt (2004), ARA&amp;A, 42, 603</p>
    <p>[2] Masters et al. (2011), MNRAS, 411, 2029</p>
  </section>

  <footer class="ltx_page_footer">arXiv.org</footer>
</body>
</html>
"""

SAMPLE_ARXIV_HTML_NO_BASE = """<!DOCTYPE html>
<html>
<head>
  <title>Simple Paper</title>
</head>
<body>
  <h1 class="ltx_title ltx_title_document">A Simple Test Paper</h1>
  <div class="ltx_abstract">
    <p>This is a test abstract.</p>
  </div>
  <section class="ltx_section">
    <h2 class="ltx_title ltx_title_section"><span class="ltx_tag">1</span> Test Section</h2>
    <p>Some test content here.</p>
  </section>
</body>
</html>
"""


def _mock_response(text: str, status_code: int = 200) -> MagicMock:
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    return resp


class TestFetchArxivHtml:
    """Tests for fetch_arxiv_html with mocked HTTP."""

    @patch("src.tools.html_parser.requests.get")
    def test_raises_on_404(self, mock_get):
        mock_get.return_value = _mock_response("", status_code=404)
        with pytest.raises(PaperHtmlUnavailableError, match="404"):
            fetch_arxiv_html("2504.00000", timeout=5)

    @patch("src.tools.html_parser.requests.get")
    def test_raises_on_connection_error(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("connection refused")
        with pytest.raises(PaperHtmlUnavailableError, match="Failed to fetch"):
            fetch_arxiv_html("2504.00000", timeout=5)

    @patch("src.tools.html_parser.requests.get")
    def test_returns_soup_on_success(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_ARXIV_HTML)
        soup = fetch_arxiv_html("2504.12345", timeout=5)
        assert soup.find("h1", class_="ltx_title") is not None

    @patch("src.tools.html_parser.requests.get")
    def test_raises_on_500(self, mock_get):
        mock_get.return_value = _mock_response("Server Error", status_code=500)
        with pytest.raises(PaperHtmlUnavailableError, match="Failed to fetch"):
            fetch_arxiv_html("2504.00000", timeout=5)


class TestFetchAndParse:
    """Integration tests for fetch_and_parse with mocked HTTP."""

    @patch("src.tools.html_parser.requests.get")
    def test_full_parse(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_ARXIV_HTML)
        result = fetch_and_parse("2504.12345", timeout=5)

        assert isinstance(result, ParsedPaper)
        assert result.arxiv_id == "2504.12345"
        assert "Barred Spiral Galaxies" in result.title
        assert "comprehensive study" in result.abstract

    @patch("src.tools.html_parser.requests.get")
    def test_sections_extracted(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_ARXIV_HTML)
        result = fetch_and_parse("2504.12345", timeout=5)

        assert len(result.sections) == 4  # Intro, Methodology, Results, Conclusions
        assert result.sections[0]["number"] == "1"
        assert result.sections[0]["title"] == "Introduction"
        assert "barred spiral galaxies" in result.sections[0]["text"].lower()

    @patch("src.tools.html_parser.requests.get")
    def test_bibliography_stripped(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_ARXIV_HTML)
        result = fetch_and_parse("2504.12345", timeout=5)

        section_titles = [s["title"] for s in result.sections]
        assert "References" not in section_titles

    @patch("src.tools.html_parser.requests.get")
    def test_figures_extracted(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_ARXIV_HTML)
        result = fetch_and_parse("2504.12345", timeout=5)

        assert len(result.figures) == 2
        assert result.figures[0].section == "Methodology"
        assert "fig1.png" in result.figures[0].url

    @patch("src.tools.html_parser.requests.get")
    def test_figure_url_resolution(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_ARXIV_HTML)
        result = fetch_and_parse("2504.12345", timeout=5)

        # Base URL from <base href="/html/2504.12345v1/">
        assert result.figures[0].url == "https://arxiv.org/html/2504.12345v1/fig1.png"

    @patch("src.tools.html_parser.requests.get")
    def test_no_base_tag_fallback(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_ARXIV_HTML_NO_BASE)
        result = fetch_and_parse("2504.99999", timeout=5)
        assert result.title == "A Simple Test Paper"
        assert result.abstract == "This is a test abstract."

    @patch("src.tools.html_parser.requests.get")
    def test_full_text_markdown_contains_sections(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_ARXIV_HTML)
        result = fetch_and_parse("2504.12345", timeout=5)

        md = result.full_text_markdown
        assert "# Dynamics of Barred Spiral Galaxies" in md
        assert "## 1 Introduction" in md
        assert "## 2 Methodology" in md
        assert "## Figure Captions" in md
        assert "Bar fraction" in md  # figure caption

    @patch("src.tools.html_parser.requests.get")
    def test_full_text_hash_is_stable(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_ARXIV_HTML)
        result1 = fetch_and_parse("2504.12345", timeout=5)
        result2 = fetch_and_parse("2504.12345", timeout=5)

        assert result1.full_text_hash == result2.full_text_hash
        assert len(result1.full_text_hash) == 64  # SHA-256 hex

    @patch("src.tools.html_parser.requests.get")
    def test_nav_stripped_from_full_text(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_ARXIV_HTML)
        result = fetch_and_parse("2504.12345", timeout=5)

        # Navigation links should not appear in full text
        assert "/abs/2504.12345" not in result.full_text_markdown


class TestComputeHash:
    """Tests for compute_hash utility."""

    def test_deterministic(self):
        h1 = compute_hash("hello")
        h2 = compute_hash("hello")
        assert h1 == h2

    def test_different_inputs(self):
        h1 = compute_hash("hello")
        h2 = compute_hash("world")
        assert h1 != h2

    def test_sha256_length(self):
        h = compute_hash("test")
        assert len(h) == 64
