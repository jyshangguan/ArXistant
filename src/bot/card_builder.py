"""Build Feishu interactive message cards."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def build_scan_result_card(result) -> dict:
    """Build a Feishu card for a scan_paper result.

    Args:
        result: A ScanResult dataclass from src.tools.types.
    """
    from ..tools.types import ScanResult

    if not isinstance(result, ScanResult):
        return _error_card("Invalid scan result format")

    quality_emoji = {
        1: "\u274c",  # red x
        2: "\u26a0\ufe0f",  # warning
        3: "\U0001f7e2",  # green circle
        4: "\U0001f7e1",  # yellow circle
        5: "\U0001f525",  # fire
    }

    # Header color based on quality
    if result.quality_score >= 4:
        header_template = "blue"
    elif result.quality_score >= 3:
        header_template = "green"
    else:
        header_template = "yellow"

    elements = []

    # Quality score
    elements.append({
        "tag": "div",
        "fields": [
            {
                "is_short": True,
                "text": {
                    "tag": "lark_md",
                    "content": f"**Quality Score:** {result.quality_score}/5 {quality_emoji.get(result.quality_score, '')}",
                },
            },
            {
                "is_short": True,
                "text": {
                    "tag": "lark_md",
                    "content": f"**Recommend Reading:** {'Yes' if result.recommend_reading else 'No'}",
                },
            },
        ],
    })

    # Quality reason
    if result.quality_reason:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Why:** {result.quality_reason}",
            },
        })

    # Tree links
    if result.tree_links:
        link_lines = []
        for link in result.tree_links:
            link_lines.append(
                f"- **{link.node_name}** (relevance {link.relevance_score}/5): {link.relevance_reason}"
            )
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "**Tree Connections:**\n" + "\n".join(link_lines),
            },
        })

    # Rationale
    if result.rationale:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Rationale:** {result.rationale}",
            },
        })

    # Action buttons
    elements.append({
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "Read More"},
                "type": "primary",
                "value": {
                    "type": "read",
                    "arxiv_id": result.arxiv_id,
                },
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "Open on arXiv"},
                "type": "default",
                "url": f"https://arxiv.org/abs/{result.arxiv_id}",
            },
        ],
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"Scan: {result.title[:60]}"},
            "template": header_template,
        },
        "elements": elements,
    }


def build_reading_note_card(note) -> dict:
    """Build a Feishu card for a read_paper result.

    Args:
        note: A ReadingNote dataclass from src.tools.types.
    """
    from ..tools.types import ReadingNote

    if not isinstance(note, ReadingNote):
        return _error_card("Invalid reading note format")

    elements = []

    # Summary
    if note.summary:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Summary:**\n{note.summary}",
            },
        })

    # Key findings
    if note.key_findings:
        findings = "\n".join(f"- {f}" for f in note.key_findings[:5])
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Key Findings:**\n{findings}",
            },
        })

    # Methodology
    if note.methodology:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Methodology:** {note.methodology}",
            },
        })

    # Results
    if note.results:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Results:** {note.results}",
            },
        })

    # Tree connections
    if note.tree_connections:
        conn_lines = []
        for tc in note.tree_connections[:5]:
            conn_lines.append(f"- **{tc.node_name}**: {tc.connection}")
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "**Tree Connections:**\n" + "\n".join(conn_lines),
            },
        })

    # Unfamiliar concepts
    if note.unfamiliar_concepts:
        concepts = ", ".join(note.unfamiliar_concepts[:5])
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**New Concepts:** {concepts}",
            },
        })

    # Cache indicator
    if note.cached:
        elements.append({
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": "Cached result from previous reading"},
            ],
        })

    # Action buttons
    elements.append({
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "Open on arXiv"},
                "type": "default",
                "url": f"https://arxiv.org/abs/{note.arxiv_id}",
            },
        ],
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"Reading: {note.title[:60]}"},
            "template": "blue",
        },
        "elements": elements,
    }


def build_report_card(
    papers_by_category: dict[str, list[dict]],
    total_scanned: int,
    total_relevant: int,
    categories: list[str],
) -> dict:
    """Build a Feishu card for the daily report.

    Args:
        papers_by_category: Dict mapping category name to list of paper dicts.
            Each paper dict has: arxiv_id, title, quality_score, tree_links,
            quality_reason, sort_key.
        total_scanned: Total papers scanned.
        total_relevant: Total relevant papers (score >= threshold).
        categories: List of monitored categories.
    """
    elements = []

    # Summary
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    elements.append({
        "tag": "div",
        "fields": [
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**Scanned:** {total_scanned}"},
            },
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**Relevant (>=4):** {total_relevant}"},
            },
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**Categories:** {len(categories)}"},
            },
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**Sections:** {len(papers_by_category)}"},
            },
        ],
    })

    # Per-category sections
    for cat_name, papers in papers_by_category.items():
        if not papers:
            continue

        # Category header
        elements.append({
            "tag": "hr",
        })
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**{cat_name}** ({len(papers)} papers)",
            },
        })

        # Papers (max 10 per category)
        for i, p in enumerate(papers[:10]):
            # Quality score display
            qs = p.get("quality_score", 0)

            # Tree connections summary
            links = p.get("tree_links", [])
            top_links = links[:2] if links else []
            link_str = ", ".join(f"{l['node_name']} ({l['relevance_score']})" for l in top_links)
            if len(links) > 2:
                link_str += f" +{len(links)-2} more"

            # Reason (truncated)
            reason = p.get("quality_reason", "")[:100]

            text = (
                f"**{i+1}. {p['title'][:50]}**\n"
                f"Quality: {qs}/5 | {link_str}\n"
                f"{reason}"
            )

            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": text},
            })

            # Action buttons for each paper
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "Read More"},
                        "type": "primary",
                        "value": {"type": "read", "arxiv_id": p["arxiv_id"]},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "Scan"},
                        "type": "default",
                        "value": {"type": "scan", "arxiv_id": p["arxiv_id"]},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "arXiv"},
                        "type": "default",
                        "url": f"https://arxiv.org/abs/{p['arxiv_id']}",
                    },
                ],
            })

    # Footer
    elements.append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text", "content": f"Generated by ArXistant at {now}"},
        ],
    })

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": f"ArXistant Daily Report \u2014 {today}"},
            "template": "blue",
        },
        "elements": elements,
    }


def build_tree_card(nodes: list, node_children: dict) -> dict:
    """Build a Feishu card showing the knowledge tree.

    Args:
        nodes: List of TreeNode objects.
        node_children: Dict mapping node_id to list of child TreeNode objects.
    """
    elements = []

    roots = [n for n in nodes if n.parent_id is None]

    def _add_node(node, indent=0):
        prefix = "  " * indent
        text = f"{'#' * min(indent + 2, 6)} {node.name}"
        if node.description:
            text += f"\n{prefix}*{node.description}*"
        if node.categories:
            text += f"\n{prefix}Categories: `{node.categories}`"
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": text},
        })
        for child in node_children.get(node.id, []):
            _add_node(child, indent + 1)

    for root in roots:
        _add_node(root)

    if not elements:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "No tree nodes defined."},
        })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Knowledge Tree"},
            "template": "turquoise",
        },
        "elements": elements,
    }


def build_prefs_card(prefs: list[dict]) -> dict:
    """Build a Feishu card showing user preference weights.

    Args:
        prefs: List of dicts with keys: node_name, weight, interaction_count.
    """
    elements = []

    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                "**How it works:** Your interactions (scanning, reading papers) "
                "automatically adjust node weights. Higher weights push related "
                "papers to the top of daily reports."
            ),
        },
    })

    elements.append({"tag": "hr"})

    if prefs:
        # Sort by weight descending
        prefs_sorted = sorted(prefs, key=lambda x: x["weight"], reverse=True)
        for p in prefs_sorted:
            bar_len = min(int(p["weight"] / 2), 20)
            bar = "\u2588" * bar_len
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**{p['node_name']}** weight={p['weight']:.1f} "
                        f"({p['interaction_count']} interactions)\n{bar}"
                    ),
                },
            })
    else:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "No preferences recorded yet. Use /scan and /read to build them."},
        })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "User Preferences"},
            "template": "purple",
        },
        "elements": elements,
    }


def build_help_card() -> dict:
    """Build a help card listing all available commands."""
    commands = [
        ("**/scan <arxiv_id>**", "Quick relevance scan of a paper"),
        ("**/read <arxiv_id>**", "Full-text reading with structured notes"),
        ("**/report [GA|HE|all]**", "Generate daily report (default: all)"),
        ("**/tree**", "Display current knowledge tree"),
        ("**/prefs**", "Show your preference weights"),
        ("**/reset**", "Clear conversation session"),
        ("**/help**", "Show this help message"),
        ("", ""),
        ("*Any other message*", "Natural language conversation about papers"),
    ]

    elements = []
    for cmd, desc in commands:
        if not cmd:
            elements.append({"tag": "hr"})
            continue
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"{cmd}\n{desc}",
            },
        })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "ArXistant Commands"},
            "template": "indigo",
        },
        "elements": elements,
    }


def _error_card(message: str) -> dict:
    """Build a simple error card."""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Error"},
            "template": "red",
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": message},
            },
        ],
    }
