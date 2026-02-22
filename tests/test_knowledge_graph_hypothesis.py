# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Property-based tests for the KnowledgeGraph (in-memory backend) using Hypothesis.

Tests invariants for add_fact() and find-related operations that must hold for
all valid inputs, complementing fixed-input unit tests.
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from self_fixing_engineer.arbiter.knowledge_graph import KnowledgeGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine in a fresh event loop (pytest-asyncio not required)."""
    return asyncio.get_event_loop().run_until_complete(coro)


_safe_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        whitelist_characters="-_.",
    ),
    min_size=1,
    max_size=40,
)


# ---------------------------------------------------------------------------
# KnowledgeGraph.add_fact
# ---------------------------------------------------------------------------


class TestKnowledgeGraphAddFactProperties:
    """Property-based invariants for KnowledgeGraph.add_fact."""

    @given(domain=_safe_text, key=_safe_text, data=st.fixed_dictionaries({}))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    def test_add_fact_returns_success(self, domain: str, key: str, data: dict) -> None:
        """add_fact must always return a dict with status=='success'."""
        kg = KnowledgeGraph()
        _run(kg.connect())
        result = _run(kg.add_fact(domain=domain, key=key, data=data))
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result.get("status") == "success", f"Unexpected result: {result}"

    @given(domain=_safe_text, key=_safe_text)
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    def test_add_fact_fact_id_contains_domain_and_key(
        self, domain: str, key: str
    ) -> None:
        """The fact_id returned by add_fact must incorporate both domain and key."""
        kg = KnowledgeGraph()
        _run(kg.connect())
        result = _run(kg.add_fact(domain=domain, key=key, data={}))
        fact_id: str = result["fact_id"]
        # Colons and special chars are sanitised — use sanitised variants
        safe_domain = domain.replace(":", "_")
        safe_key = key.replace(":", "_")
        assert safe_domain in fact_id
        assert safe_key in fact_id

    @given(
        facts=st.lists(
            st.tuples(_safe_text, _safe_text, st.just({})),
            min_size=1,
            max_size=10,
            unique_by=lambda t: (t[0], t[1]),
        )
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    def test_node_count_matches_inserted_facts(self, facts: list) -> None:
        """After inserting N distinct facts the graph must have exactly N nodes."""
        kg = KnowledgeGraph()
        _run(kg.connect())
        for domain, key, data in facts:
            _run(kg.add_fact(domain=domain, key=key, data=data))
        stats = _run(kg.get_stats())
        assert stats["node_count"] == len(facts)

    @given(domain=_safe_text, key=_safe_text)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    def test_added_fact_is_retrievable_via_get_node(
        self, domain: str, key: str
    ) -> None:
        """A fact added with add_fact must be retrievable via get_node."""
        kg = KnowledgeGraph()
        _run(kg.connect())
        result = _run(kg.add_fact(domain=domain, key=key, data={"v": 1}))
        node = _run(kg.get_node(result["fact_id"]))
        assert node is not None

    @given(domain=_safe_text, key=_safe_text)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    def test_clear_resets_graph(self, domain: str, key: str) -> None:
        """After clear(), node_count must be 0."""
        kg = KnowledgeGraph()
        _run(kg.connect())
        _run(kg.add_fact(domain=domain, key=key, data={}))
        _run(kg.clear())
        stats = _run(kg.get_stats())
        assert stats["node_count"] == 0


# ---------------------------------------------------------------------------
# get_neighbors (related facts)
# ---------------------------------------------------------------------------


class TestKnowledgeGraphRelatedFactsProperties:
    """Property-based invariants for neighbour lookups after add_edge."""

    @given(
        node_a=_safe_text,
        node_b=_safe_text,
        rel=_safe_text,
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    def test_get_neighbors_returns_list(
        self, node_a: str, node_b: str, rel: str
    ) -> None:
        """get_neighbors must always return a list."""
        kg = KnowledgeGraph()
        _run(kg.connect())
        _run(kg.add_node(node_a, {}))
        _run(kg.add_node(node_b, {}))
        _run(kg.add_edge(node_a, node_b, rel))
        result = _run(kg.get_neighbors(node_a))
        assert isinstance(result, list)

    @given(
        node_a=_safe_text,
        node_b=_safe_text,
        rel=_safe_text,
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    def test_added_edge_appears_in_neighbors(
        self, node_a: str, node_b: str, rel: str
    ) -> None:
        """After add_edge(a, b, rel), get_neighbors(a) must include b."""
        if node_a == node_b:
            return
        kg = KnowledgeGraph()
        _run(kg.connect())
        _run(kg.add_node(node_a, {}))
        _run(kg.add_node(node_b, {}))
        _run(kg.add_edge(node_a, node_b, rel))
        neighbors = _run(kg.get_neighbors(node_a))
        found = any(n == node_b for n, _ in neighbors)
        assert found, f"{node_b} not found in neighbors of {node_a}: {neighbors}"

    @given(node_id=_safe_text)
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    def test_isolated_node_has_no_neighbors(self, node_id: str) -> None:
        """A node with no outgoing edges must have an empty neighbor list."""
        kg = KnowledgeGraph()
        _run(kg.connect())
        _run(kg.add_node(node_id, {}))
        result = _run(kg.get_neighbors(node_id))
        assert result == []
