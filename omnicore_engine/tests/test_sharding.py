# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Unit tests for omnicore_engine/sharding.py.

Covers:
- Uniform key distribution across shards.
- Consistent-hashing property: minimal key remapping on shard add/remove.
- Thread-safety under concurrent access.
- build_ring_from_env() helper.
"""

from __future__ import annotations

import os
import threading
from collections import Counter
from typing import List

import pytest

from omnicore_engine.sharding import ConsistentHashRing, build_ring_from_env


# ---------------------------------------------------------------------------
# Basic correctness
# ---------------------------------------------------------------------------


class TestConsistentHashRingBasic:
    """Basic correctness tests for ConsistentHashRing."""

    def test_empty_ring_raises_on_get_shard(self) -> None:
        ring = ConsistentHashRing()
        with pytest.raises(ValueError):
            ring.get_shard("any-key")

    def test_single_shard_routes_all_keys(self) -> None:
        ring = ConsistentHashRing()
        ring.add_shard("only")
        for i in range(100):
            assert ring.get_shard(f"key-{i}") == "only"

    def test_get_shard_returns_registered_shard(self) -> None:
        ring = ConsistentHashRing()
        ring.add_shard("alpha")
        ring.add_shard("beta")
        result = ring.get_shard("some-topic")
        assert result in {"alpha", "beta"}

    def test_duplicate_add_is_ignored(self) -> None:
        ring = ConsistentHashRing()
        ring.add_shard("x")
        ring.add_shard("x")  # duplicate — should not raise
        assert ring.shard_count == 1

    def test_remove_unknown_shard_is_ignored(self) -> None:
        ring = ConsistentHashRing()
        ring.add_shard("a")
        ring.remove_shard("nonexistent")  # should not raise
        assert ring.shard_count == 1

    def test_remove_shard_decrements_count(self) -> None:
        ring = ConsistentHashRing()
        ring.add_shard("a")
        ring.add_shard("b")
        ring.remove_shard("a")
        assert ring.shard_count == 1
        assert "b" in ring.shard_ids

    def test_routing_is_deterministic(self) -> None:
        ring = ConsistentHashRing()
        for i in range(3):
            ring.add_shard(f"s{i}")
        results = [ring.get_shard("fixed-key") for _ in range(50)]
        assert len(set(results)) == 1  # all identical

    def test_shard_ids_property(self) -> None:
        ring = ConsistentHashRing()
        ring.add_shard("z")
        ring.add_shard("a")
        assert ring.shard_ids == ["a", "z"]


# ---------------------------------------------------------------------------
# Uniform key distribution
# ---------------------------------------------------------------------------


class TestUniformDistribution:
    """Verify that keys distribute approximately uniformly across shards."""

    _NUM_KEYS = 10_000
    _NUM_SHARDS = 5
    # Allow each shard at most 2× the average load.
    _MAX_RATIO = 2.0

    def test_no_shard_has_more_than_2x_average_load(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=150)
        shards = [f"shard-{i}" for i in range(self._NUM_SHARDS)]
        for s in shards:
            ring.add_shard(s)

        counts: Counter = Counter(
            ring.get_shard(f"key:{i}") for i in range(self._NUM_KEYS)
        )
        average = self._NUM_KEYS / self._NUM_SHARDS
        for shard, count in counts.items():
            ratio = count / average
            assert ratio <= self._MAX_RATIO, (
                f"Shard {shard!r} has {count} keys ({ratio:.2f}× average {average:.0f}); "
                f"expected ≤ {self._MAX_RATIO}×"
            )

    def test_all_shards_receive_at_least_one_key(self) -> None:
        ring = ConsistentHashRing(virtual_nodes=150)
        shards = [f"shard-{i}" for i in range(self._NUM_SHARDS)]
        for s in shards:
            ring.add_shard(s)

        counts: Counter = Counter(
            ring.get_shard(f"k:{i}") for i in range(self._NUM_KEYS)
        )
        for s in shards:
            assert counts[s] > 0, f"Shard {s!r} received zero keys"


# ---------------------------------------------------------------------------
# Minimal key remapping (consistent-hashing property)
# ---------------------------------------------------------------------------


class TestMinimalRemapping:
    """Verify that adding/removing a shard remaps only a minimal fraction of keys."""

    _NUM_KEYS = 5_000
    _BASE_SHARDS = 4

    def _route_all(self, ring: ConsistentHashRing) -> List[str]:
        return [ring.get_shard(f"t:{i}") for i in range(self._NUM_KEYS)]

    def test_adding_shard_remaps_at_most_expected_fraction(self) -> None:
        ring = ConsistentHashRing()
        for i in range(self._BASE_SHARDS):
            ring.add_shard(f"s{i}")

        before = self._route_all(ring)
        ring.add_shard(f"s{self._BASE_SHARDS}")
        after = self._route_all(ring)

        changed = sum(1 for a, b in zip(before, after) if a != b)
        # Ideal remapping: 1 / (N+1) ≈ 20 % for N=4 → allow up to 40 %
        allowed = self._NUM_KEYS / (self._BASE_SHARDS + 1) * 2
        assert changed <= allowed, (
            f"{changed} keys remapped after adding a shard; expected ≤ {allowed:.0f}"
        )

    def test_removing_shard_remaps_at_most_expected_fraction(self) -> None:
        ring = ConsistentHashRing()
        for i in range(self._BASE_SHARDS):
            ring.add_shard(f"r{i}")

        before = self._route_all(ring)
        ring.remove_shard("r0")
        after = self._route_all(ring)

        changed = sum(1 for a, b in zip(before, after) if a != b)
        # Only keys previously assigned to r0 should move (≈ 1/N)
        # Allow up to 2× that fraction.
        allowed = self._NUM_KEYS / self._BASE_SHARDS * 2
        assert changed <= allowed, (
            f"{changed} keys remapped after removing a shard; expected ≤ {allowed:.0f}"
        )


# ---------------------------------------------------------------------------
# Thread-safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Verify that concurrent reads and writes do not corrupt the ring."""

    _NUM_THREADS = 20
    _KEYS_PER_THREAD = 200

    def test_concurrent_get_shard_does_not_raise(self) -> None:
        ring = ConsistentHashRing()
        for i in range(4):
            ring.add_shard(f"ts{i}")

        errors: List[Exception] = []

        def worker(thread_id: int) -> None:
            try:
                for k in range(self._KEYS_PER_THREAD):
                    result = ring.get_shard(f"t{thread_id}:k{k}")
                    assert result.startswith("ts")
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(self._NUM_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_concurrent_add_and_get_does_not_raise(self) -> None:
        ring = ConsistentHashRing()
        ring.add_shard("initial-shard")
        errors: List[Exception] = []

        def reader(tid: int) -> None:
            try:
                for k in range(50):
                    ring.get_shard(f"r{tid}:{k}")
            except ValueError:
                # Ring may temporarily be empty between remove/add — acceptable
                pass
            except Exception as exc:
                errors.append(exc)

        def writer(tid: int) -> None:
            try:
                shard = f"dynamic-{tid}"
                ring.add_shard(shard)
                ring.get_shard(f"probe-{tid}")
                ring.remove_shard(shard)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=(reader if i % 2 == 0 else writer), args=(i,))
            for i in range(self._NUM_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"


# ---------------------------------------------------------------------------
# build_ring_from_env helper
# ---------------------------------------------------------------------------


class TestBuildRingFromEnv:
    """Tests for the build_ring_from_env() factory function."""

    def test_default_creates_three_shards(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MESSAGE_BUS_SHARDS", raising=False)
        ring = build_ring_from_env()
        assert ring.shard_count == 3

    def test_env_var_controls_shard_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MESSAGE_BUS_SHARDS", "6")
        ring = build_ring_from_env()
        assert ring.shard_count == 6

    def test_shards_are_named_shard_n(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MESSAGE_BUS_SHARDS", "2")
        ring = build_ring_from_env()
        assert ring.shard_ids == ["shard-0", "shard-1"]

    def test_ring_routes_keys_after_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MESSAGE_BUS_SHARDS", "3")
        ring = build_ring_from_env()
        result = ring.get_shard("some.topic.name")
        assert result in {"shard-0", "shard-1", "shard-2"}
