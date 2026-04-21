"""Tests for src/tree.py — load_tree_yaml, import_tree_from_yaml, format_tree_for_prompt."""

from __future__ import annotations

import pytest
import yaml

from src.tree import load_tree_yaml, import_tree_from_yaml, format_tree_for_prompt
from src.storage import init_db, count_tree_nodes, get_tree_node_by_name, get_all_tree_nodes


# ── load_tree_yaml ────────────────────────────────────────────────────


class TestLoadTreeYaml:
    def test_basic(self, tmp_path):
        data = {
            "tree": [
                {"name": "Root1", "description": "A root", "categories": ["cat.A"]},
            ]
        }
        f = tmp_path / "tree.yaml"
        f.write_text(yaml.dump(data))
        nodes = load_tree_yaml(f)
        assert len(nodes) == 1
        assert nodes[0]["name"] == "Root1"

    def test_nested_children(self, tmp_path):
        data = {
            "tree": [
                {
                    "name": "Root",
                    "children": [
                        {"name": "Child1", "children": [
                            {"name": "Grandchild"}
                        ]},
                        {"name": "Child2"},
                    ]
                }
            ]
        }
        f = tmp_path / "tree.yaml"
        f.write_text(yaml.dump(data))
        nodes = load_tree_yaml(f)
        assert len(nodes) == 1
        assert len(nodes[0]["children"]) == 2
        assert nodes[0]["children"][0]["children"][0]["name"] == "Grandchild"

    def test_empty_tree(self, tmp_path):
        f = tmp_path / "tree.yaml"
        f.write_text(yaml.dump({"tree": []}))
        assert load_tree_yaml(f) == []

    def test_missing_tree_key(self, tmp_path):
        f = tmp_path / "tree.yaml"
        f.write_text(yaml.dump({"other": []}))
        assert load_tree_yaml(f) == []


# ── import_tree_from_yaml ─────────────────────────────────────────────


class TestImportTreeFromYaml:
    def test_imports_flat_tree(self, tmp_path):
        conn = init_db(":memory:")
        data = {
            "tree": [
                {"name": "RootA", "description": "desc", "categories": ["cat.A"]},
                {"name": "RootB", "categories": ["cat.B"]},
            ]
        }
        f = tmp_path / "tree.yaml"
        f.write_text(yaml.dump(data))

        count = import_tree_from_yaml(conn, f)
        assert count == 2
        assert count_tree_nodes(conn) == 2

        ra = get_tree_node_by_name(conn, "RootA")
        assert ra is not None
        assert ra.parent_id is None
        assert ra.level == 0
        assert "cat.A" in ra.categories

        conn.close()

    def test_imports_nested_tree(self, tmp_path):
        conn = init_db(":memory:")
        data = {
            "tree": [
                {
                    "name": "Root",
                    "description": "Root desc",
                    "categories": ["cat.A"],
                    "children": [
                        {
                            "name": "Child",
                            "description": "Child desc",
                            "categories": ["cat.B"],
                        }
                    ]
                }
            ]
        }
        f = tmp_path / "tree.yaml"
        f.write_text(yaml.dump(data))

        count = import_tree_from_yaml(conn, f)
        assert count == 2

        child = get_tree_node_by_name(conn, "Child")
        assert child is not None
        assert child.parent_id is not None
        assert child.level == 1
        # Child should inherit parent categories
        assert "cat.A" in child.categories
        assert "cat.B" in child.categories

        conn.close()

    def test_skips_if_tree_exists(self, tmp_path, db_conn_with_tree):
        data = {"tree": [{"name": "NewRoot"}]}
        f = tmp_path / "tree.yaml"
        f.write_text(yaml.dump(data))

        count = import_tree_from_yaml(db_conn_with_tree, f)
        assert count == 0

    def test_returns_zero_for_empty_yaml(self, tmp_path):
        conn = init_db(":memory:")
        f = tmp_path / "tree.yaml"
        f.write_text(yaml.dump({"tree": []}))
        count = import_tree_from_yaml(conn, f)
        assert count == 0
        conn.close()


# ── format_tree_for_prompt ────────────────────────────────────────────


class TestFormatTreeForPrompt:
    def test_empty_tree(self):
        conn = init_db(":memory:")
        result = format_tree_for_prompt(conn)
        assert "No knowledge tree nodes defined" in result
        conn.close()

    def test_flat_tree(self):
        conn = init_db(":memory:")
        from src.storage import insert_tree_node
        insert_tree_node(conn, "Node A", "Description A", categories="cat.A")
        insert_tree_node(conn, "Node B", "Description B", categories="cat.B")

        result = format_tree_for_prompt(conn)
        assert "Node A" in result
        assert "Node B" in result
        assert "Description A" in result
        assert "cat.A" in result
        assert "Knowledge Tree:" in result
        conn.close()

    def test_nested_tree(self, db_conn_with_tree):
        result = format_tree_for_prompt(db_conn_with_tree)
        assert "Galactic Dynamics" in result
        assert "Bar Formation" in result
        assert "Gamma-Ray Bursts" in result

    def test_root_nodes_numbered(self):
        conn = init_db(":memory:")
        from src.storage import insert_tree_node
        insert_tree_node(conn, "Root A", "desc A")
        insert_tree_node(conn, "Root B", "desc B")

        result = format_tree_for_prompt(conn)
        assert "1. Root A" in result
        assert "2. Root B" in result
        conn.close()
