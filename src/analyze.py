"""Analyze papers: quality scoring, tree-linking, and candidate node proposals."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from .collector import RawPaper
from .config import Settings
from .llm_client import create_client, chat_completion

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    paper: RawPaper
    quality_score: int
    quality_reason: str
    tree_links: list[dict] = field(default_factory=list)
    candidate_node: dict | None = None


ANALYSIS_SYSTEM_PROMPT = """\
You are a research paper analyst for an arXiv paper recommendation system.

You evaluate papers on TWO axes:
1. **Quality score** (1-5): How important/significant is this paper in its field?
2. **Tree relevance** (1-5 per node): How relevant is this paper to specific knowledge tree topics?

## Quality scoring (1-5)

- **1**: Routine/derivative work of limited significance
- **2**: Competent work but no major advance
- **3**: Solid contribution, moderately interesting results
- **4**: Important result, advances the field significantly
- **5**: Breakthrough or must-read paper

## Tree linking rules

- For each knowledge tree node, assess relevance on 1-5 scale
- Only create a link if relevance >= 3 (moderate or higher)
- A paper can link to multiple nodes
- The relevance reason should explain the specific connection

## Candidate node proposal (optional)

If a paper's quality >= 3 AND it discusses a concept not well-covered by existing tree nodes, propose a new child node:
- Pick the most relevant existing node as parent
- Give the new node a concise, specific name
- Write a 1-2 sentence description

Only propose at most ONE candidate node per paper. Set to null if no proposal warranted.

## Response format

Respond ONLY with a JSON object, no other text:
```json
{
  "papers": [
    {
      "index": 0,
      "quality_score": 3,
      "quality_reason": "Brief reason for quality score",
      "tree_links": [
        {
          "node_name": "Bar Formation",
          "relevance_score": 4,
          "relevance_reason": "Directly studies bar-driven..."
        }
      ],
      "candidate_node": null
    }
  ]
}
```
"""


def _format_papers_for_analysis(papers: list[RawPaper]) -> str:
    """Format papers for the LLM analysis prompt."""
    parts = []
    for i, p in enumerate(papers):
        authors_str = ", ".join(p.authors[:3])
        if len(p.authors) > 3:
            authors_str += f" et al. ({len(p.authors)} authors)"
        parts.append(
            f"[{i}] Title: {p.title}\n"
            f"    Authors: {authors_str}\n"
            f"    Categories: {', '.join(p.categories)}\n"
            f"    Abstract: {p.abstract}"
        )
    return "\n\n".join(parts)


def _parse_analysis_response(text: str) -> list[dict]:
    """Extract JSON object with 'papers' key from the LLM response.

    Tries: direct JSON → code fence → brace extraction.
    """
    text = text.strip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict) and "papers" in result:
            return result["papers"]
    except json.JSONDecodeError:
        pass

    # Try extracting from code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        try:
            result = json.loads(fence_match.group(1).strip())
            if isinstance(result, dict) and "papers" in result:
                return result["papers"]
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } brace pair
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            result = json.loads(brace_match.group(0))
            if isinstance(result, dict) and "papers" in result:
                return result["papers"]
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse analysis response as JSON")
    return []


def analyze_papers(
    papers: list[RawPaper],
    tree_prompt: str,
    settings: Settings,
) -> list[AnalysisResult]:
    """Analyze papers for quality, tree links, and candidate proposals.

    Uses the LLM to score and link papers against the knowledge tree.
    Processes papers in batches based on settings.batch_size.
    """
    if not papers:
        return []

    client = create_client(settings)
    batch_size = settings.batch_size
    results: list[AnalysisResult] = []

    total_batches = (len(papers) + batch_size - 1) // batch_size

    for batch_num in range(total_batches):
        start = batch_num * batch_size
        end = min(start + batch_size, len(papers))
        batch = papers[start:end]
        logger.info("Analyzing batch %d/%d (papers %d-%d of %d)",
                     batch_num + 1, total_batches, start, end - 1, len(papers))

        papers_block = _format_papers_for_analysis(batch)
        user_prompt = (
            f"## Knowledge Tree\n\n{tree_prompt}\n\n"
            f"## Papers to Analyze\n\n{papers_block}"
        )

        try:
            response_text = chat_completion(
                client=client,
                model=settings.llm_model,
                system_prompt=ANALYSIS_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=settings.llm_temperature,
            )
            parsed = _parse_analysis_response(response_text)
        except Exception:
            logger.exception("LLM call failed for analysis batch %d, skipping", batch_num + 1)
            continue

        for item in parsed:
            try:
                idx = item["index"]
                if idx < 0 or idx >= len(batch):
                    continue

                quality_score = max(1, min(5, int(item.get("quality_score", 1))))
                quality_reason = item.get("quality_reason", "")

                tree_links = []
                for link in item.get("tree_links", []):
                    relevance = max(1, min(5, int(link.get("relevance_score", 1))))
                    if relevance >= 3:
                        tree_links.append({
                            "node_name": link["node_name"],
                            "relevance_score": relevance,
                            "relevance_reason": link.get("relevance_reason", ""),
                        })

                candidate = item.get("candidate_node")
                if candidate and quality_score < 3:
                    candidate = None  # Only quality >= 3 papers can propose candidates

                results.append(AnalysisResult(
                    paper=batch[idx],
                    quality_score=quality_score,
                    quality_reason=quality_reason,
                    tree_links=tree_links,
                    candidate_node=candidate,
                ))
            except (KeyError, ValueError, TypeError):
                logger.warning("Malformed analysis item in batch %d: %s", batch_num + 1, item)

    logger.info("Analysis complete: %d/%d papers analyzed", len(results), len(papers))
    return results
