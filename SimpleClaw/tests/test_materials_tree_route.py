"""Tests for GET /materials/tree route."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _build_tree(rows: list[dict]) -> list[dict]:
    """Pure function under test — extract & expose for unit testing."""
    from Flowcut.api.routes.materials import _build_material_tree
    return _build_material_tree(rows)


def test_build_tree_groups_by_product_then_scene_role() -> None:
    rows = [
        {"product": "雪莲洗液", "scene_role": "医生"},
        {"product": "雪莲洗液", "scene_role": "医生"},
        {"product": "雪莲洗液", "scene_role": "药材"},
        {"product": "妆前乳", "scene_role": "产品展示"},
    ]
    tree = _build_tree(rows)
    assert tree == [
        {"product": "妆前乳", "total_count": 1, "children": [
            {"scene_role": "产品展示", "count": 1},
        ]},
        {"product": "雪莲洗液", "total_count": 3, "children": [
            {"scene_role": "医生", "count": 2},
            {"scene_role": "药材", "count": 1},
        ]},
    ]


def test_build_tree_null_product_becomes_通用() -> None:
    rows = [{"product": None, "scene_role": None}]
    tree = _build_tree(rows)
    assert tree[0]["product"] == "通用"
    assert tree[0]["children"] == [{"scene_role": "未分类", "count": 1}]


def test_build_tree_empty_rows_returns_empty_list() -> None:
    assert _build_tree([]) == []
