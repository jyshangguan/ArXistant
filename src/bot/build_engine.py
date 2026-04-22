"""Build engine: LLM-powered knowledge tree generation from user interests."""

from __future__ import annotations

import json
import logging
import re

import yaml

from ..config import Settings
from ..llm_client import create_client, chat_completion
from ..storage import get_build_session, upsert_build_session

logger = logging.getLogger(__name__)

_BUILD_SYSTEM_PROMPT = """\
You are a knowledge tree architect for an arXiv paper recommendation system.

Given a user's research interests, generate a hierarchical knowledge tree that \
covers their areas of focus. The tree will be used to:
1. Fetch relevant papers from arXiv
2. Score and link papers to specific research topics
3. Organize daily reports by research area

## Rules

- Produce 2-8 root nodes (major research areas)
- Each root node should have 2-6 children (sub-topics)
- Total node count should be 10-60
- Every node must have a 'name' (concise, specific) and 'description' (1-2 sentences)
- Root nodes must have 'categories' mapped to valid arXiv astrophysics categories:
  astro-ph.GA, astro-ph.HE, astro-ph.CO, astro-ph.SR, astro-ph.EP, astro-ph.IM, astro-ph.CE
- Children inherit categories from their parent; they may add new ones
- Use descriptive names, not generic ones (e.g. "Bar Formation & Secular Evolution" not "Bar Studies")
- No duplicate node names

## Response format

Respond ONLY with a JSON object, no other text:
```json
{
  "tree": [
    {
      "name": "Root Node Name",
      "description": "Brief description of the area.",
      "categories": ["astro-ph.GA"],
      "children": [
        {
          "name": "Sub-topic Name",
          "description": "Brief description.",
        },
        {
          "name": "Another Sub-topic",
          "description": "Brief description.",
          "categories": ["astro-ph.HE"]
        }
      ]
    }
  ]
}
```
"""


def generate_tree_from_interests(
    interests: str,
    settings: Settings,
) -> list[dict]:
    """Use the LLM to generate a knowledge tree from user's research interests.

    Args:
        interests: User's description of their research interests.
        settings: Application settings (LLM config).

    Returns:
        List of node dicts with 'name', 'description', 'categories', and 'children'.
        Returns empty list on failure.
    """
    client = create_client(settings)

    user_prompt = (
        f"Generate a knowledge tree for a researcher with the following interests:\n\n"
        f"> {interests}\n\n"
        f"Produce a JSON tree structure following the rules above."
    )

    try:
        response_text = chat_completion(
            client=client,
            model=settings.llm_model,
            system_prompt=_BUILD_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.3,
        )
    except Exception:
        logger.exception("LLM call failed for tree generation")
        return []

    # Parse response
    nodes = _parse_tree_response(response_text)
    if not nodes:
        logger.warning("Failed to parse tree generation response")
        return []

    # Validate
    is_valid, reason = validate_tree(nodes)
    if not is_valid:
        logger.warning("Generated tree failed validation: %s", reason)
        # Still return it — let the user decide

    return nodes


def validate_tree(nodes: list[dict]) -> tuple[bool, str]:
    """Validate a generated tree structure.

    Checks:
    - 2-8 root nodes
    - 10-60 total nodes
    - Valid arXiv categories
    - No duplicate names

    Returns (is_valid, reason).
    """
    valid_categories = {
        "astro-ph.GA", "astro-ph.HE", "astro-ph.CO", "astro-ph.SR",
        "astro-ph.EP", "astro-ph.IM", "astro-ph.CE",
    }

    if not nodes:
        return False, "Empty tree"

    if len(nodes) < 2:
        return False, f"Too few root nodes: {len(nodes)} (minimum 2)"
    if len(nodes) > 8:
        return False, f"Too many root nodes: {len(nodes)} (maximum 8)"

    # Count total nodes and check duplicates
    all_names: list[str] = []

    def _count_and_check(node_list: list[dict], depth: int) -> int:
        count = 0
        for node in node_list:
            name = node.get("name", "").strip()
            if not name:
                return count
            if name in all_names:
                return count
            all_names.append(name)

            # Validate categories on root nodes
            if depth == 0:
                cats = node.get("categories", [])
                for cat in cats:
                    if cat not in valid_categories:
                        return count

            count += 1
            children = node.get("children", [])
            if children:
                count += _count_and_check(children, depth + 1)
        return count

    total = _count_and_check(nodes, 0)
    duplicate_names = [n for i, n in enumerate(all_names) if n in all_names[:i]]

    if duplicate_names:
        return False, f"Duplicate node names: {duplicate_names}"

    if total < 10:
        return False, f"Too few total nodes: {total} (minimum 10)"
    if total > 60:
        return False, f"Too many total nodes: {total} (maximum 60)"

    return True, "OK"


def _parse_tree_response(text: str) -> list[dict]:
    """Extract tree nodes from the LLM response.

    Tries: direct JSON -> code fence -> brace extraction.
    """
    text = text.strip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict) and "tree" in result:
            return result["tree"]
    except json.JSONDecodeError:
        pass

    # Try extracting from code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        try:
            result = json.loads(fence_match.group(1).strip())
            if isinstance(result, dict) and "tree" in result:
                return result["tree"]
        except json.JSONDecodeError:
            pass

    # Try finding first { ... } brace pair
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            result = json.loads(brace_match.group(0))
            if isinstance(result, dict) and "tree" in result:
                return result["tree"]
        except json.JSONDecodeError:
            pass

    return []
