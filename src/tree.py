"""Knowledge tree: load from YAML, import into DB, format for LLM prompts."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def load_tree_yaml(path: str | Path) -> list[dict]:
    """Load the knowledge tree definition from a YAML file.

    Returns a list of node dicts with 'name', 'description', 'categories',
    and optional 'children' (nested dicts with the same structure).
    """
    path = Path(path)
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("tree", [])


def import_tree_from_yaml(
    conn: sqlite3.Connection, tree_path: str | Path
) -> int:
    """Import the knowledge tree from YAML into the database.

    Only imports if the tree is empty (no active nodes). Returns the number
    of nodes imported, or 0 if the tree was already populated.
    """
    from .storage import count_tree_nodes, get_tree_node_by_name, insert_tree_node

    if count_tree_nodes(conn) > 0:
        logger.info("Knowledge tree already exists (%d nodes), skipping import",
                     count_tree_nodes(conn))
        return 0

    nodes = load_tree_yaml(tree_path)
    if not nodes:
        logger.warning("No tree nodes found in %s", tree_path)
        return 0

    imported = 0

    def _import_children(node_list: list[dict], parent_id: int | None, level: int,
                         inherited_cats: set[str]) -> None:
        nonlocal imported
        for node_def in node_list:
            name = node_def["name"]
            description = node_def.get("description", "")
            node_cats = set(node_def.get("categories", []))
            all_cats = inherited_cats | node_cats
            cat_str = ",".join(sorted(all_cats))

            nid = insert_tree_node(
                conn,
                name=name,
                description=description,
                parent_id=parent_id,
                level=level,
                source="user",
                categories=cat_str,
            )
            imported += 1

            children = node_def.get("children", [])
            if children:
                _import_children(children, nid, level + 1, all_cats)

    _import_children(nodes, None, 0, set())
    logger.info("Imported %d knowledge tree nodes from %s", imported, tree_path)
    return imported


def format_tree_for_prompt(conn: sqlite3.Connection) -> str:
    """Format the knowledge tree as a text block suitable for LLM prompts.

    Returns a string like:
        Knowledge Tree:
        1. Galactic Dynamics (Level 0)
           Description: ...
           Categories: astro-ph.GA
           Children:
             1.1. Bar Formation (Level 1)
                Description: ...
                Categories: astro-ph.GA
        2. High-Energy Astrophysical Transients (Level 0)
           ...
    """
    from .storage import get_all_tree_nodes

    nodes = get_all_tree_nodes(conn)
    if not nodes:
        return "No knowledge tree nodes defined."

    # Build a map for quick lookup
    node_map: dict[int, dict] = {n.id: {"node": n, "children": []} for n in nodes}
    roots: list[int] = []

    for n in nodes:
        if n.parent_id is None:
            roots.append(n.id)
        elif n.parent_id in node_map:
            node_map[n.parent_id]["children"].append(n.id)

    lines = ["Knowledge Tree:"]

    def _format_node(node_id: int, prefix: str) -> None:
        nd = node_map[node_id]["node"]
        lines.append(f"{prefix}{nd.name} (Level {nd.level})")
        if nd.description:
            lines.append(f"{prefix}  Description: {nd.description}")
        if nd.categories:
            lines.append(f"{prefix}  Categories: {nd.categories}")
        for child_id in node_map[node_id]["children"]:
            _format_node(child_id, prefix + "  ")

    for root_id in roots:
        _format_node(root_id, prefix="")

    # Add numbering for root-level nodes
    result_lines = ["Knowledge Tree:"]
    root_idx = 0
    for line in lines[1:]:
        if "Level 0)" in line:
            root_idx += 1
            result_lines.append(f"{root_idx}. {line}")
        else:
            result_lines.append(f"   {line}")

    return "\n".join(result_lines)
