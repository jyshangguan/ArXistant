"""Shared dataclasses for paper reading tools."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FigureInfo:
    """Metadata about a figure in an arXiv HTML paper."""

    index: int
    url: str
    caption: str = ""
    section: str = ""


@dataclass
class ParsedPaper:
    """Structured output from parsing an arXiv HTML page."""

    arxiv_id: str
    title: str
    abstract: str
    sections: list[dict] = field(default_factory=list)  # [{number, title, text}]
    figures: list[FigureInfo] = field(default_factory=list)
    full_text_markdown: str = ""
    full_text_hash: str = ""


@dataclass
class TreeLink:
    """A relevance link between a paper and a knowledge tree node."""

    node_name: str
    relevance_score: int
    relevance_reason: str = ""


@dataclass
class ScanResult:
    """Result of a quick relevance scan of a paper."""

    arxiv_id: str
    title: str
    quality_score: int
    quality_reason: str = ""
    tree_links: list[TreeLink] = field(default_factory=list)
    recommend_reading: bool = False
    rationale: str = ""


@dataclass
class TreeConnection:
    """A connection between a paper's content and a knowledge tree node."""

    node_name: str
    connection: str = ""


@dataclass
class ReadingNote:
    """Executive reading notes for a paper."""

    arxiv_id: str
    title: str
    authors: str = ""
    background: str = ""
    key_findings: list[str] = field(default_factory=list)
    evaluation: str = ""
    tree_connections: list[TreeConnection] = field(default_factory=list)
    cached: bool = False
