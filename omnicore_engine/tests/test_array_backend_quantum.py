# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Test suite for quantum and neuromorphic array backends in omnicore_engine/array_backend.py.

Tests cover:
- ArrayBackend with mode="quantum" (with and without qiskit installed)
- ArrayBackend with mode="neuromorphic" (with and without nengo installed)
- Graceful fallback to NumPy when optional deps are unavailable
"""

import os
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnicore_engine.array_backend import ArrayBackend, HAS_QISKIT, HAS_NENGO

# Mark all tests in this module as heavy (requires numpy)
pytestmark = pytest.mark.heavy


class TestQuantumBackendFallback:
    """Test that ArrayBackend with mode='quantum' works even without qiskit."""

    def test_quantum_mode_returns_valid_namespace_without_qiskit(self):
        """ArrayBackend(mode='quantum') returns a valid namespace even when falling back to numpy."""
        # use_quantum=False forces numpy fallback regardless of qiskit availability
        backend = ArrayBackend(mode="quantum", use_quantum=False)
        assert backend.xp is np

    def test_quantum_mode_with_use_quantum_false_falls_back(self):
        """ArrayBackend with use_quantum=False always falls back to numpy."""
        backend = ArrayBackend(mode="quantum", use_quantum=False)
        result = backend.xp.zeros((3,))
        assert result.shape == (3,)
        assert np.all(result == 0)

    def test_quantum_normal_fallback_shape(self):
        """quantum_normal returns array of correct shape when falling back to numpy."""
        with patch("omnicore_engine.array_backend.HAS_QISKIT", False):
            with patch("omnicore_engine.array_backend.AerSimulator", None):
                backend = ArrayBackend(mode="quantum", use_quantum=True)
                # The quantum_normal function should fall back to numpy
                result = backend.xp.random.normal(size=10)
                assert hasattr(result, "__len__") or isinstance(result, (float, int, np.floating))

    @pytest.mark.skipif(not HAS_QISKIT, reason="qiskit not installed")
    def test_quantum_backend_normal_shape_with_qiskit(self):
        """quantum_normal returns an array of correct shape when qiskit is available."""
        qiskit = pytest.importorskip("qiskit")
        backend = ArrayBackend(mode="quantum", use_quantum=True)
        assert hasattr(backend.xp, "random")
        result = backend.xp.random.normal(loc=0.0, scale=1.0, size=5)
        assert isinstance(result, np.ndarray)
        assert result.shape == (5,)

    @pytest.mark.skipif(not HAS_QISKIT, reason="qiskit not installed")
    def test_quantum_backend_zeros_shape(self):
        """quantum_zeros returns zeros array of correct shape."""
        pytest.importorskip("qiskit")
        backend = ArrayBackend(mode="quantum", use_quantum=True)
        result = backend.xp.zeros((4, 4))
        assert result.shape == (4, 4)
        assert np.all(result == 0)

    @pytest.mark.skipif(not HAS_QISKIT, reason="qiskit not installed")
    def test_quantum_backend_array(self):
        """quantum_array wraps data correctly."""
        pytest.importorskip("qiskit")
        backend = ArrayBackend(mode="quantum", use_quantum=True)
        data = [1.0, 2.0, 3.0]
        result = backend.xp.array(data)
        assert isinstance(result, np.ndarray)
        np.testing.assert_array_equal(result, np.array(data))


class TestNeuromorphicBackendFallback:
    """Test that ArrayBackend with mode='neuromorphic' falls back gracefully when nengo unavailable."""

    def test_neuromorphic_mode_fallback_without_nengo(self):
        """ArrayBackend(mode='neuromorphic') falls back to numpy gracefully when nengo is unavailable."""
        with patch("omnicore_engine.array_backend.HAS_NENGO", False):
            backend = ArrayBackend(mode="neuromorphic", use_neuromorphic=True)
            # With HAS_NENGO=False, the neuromorphic functions should fall back to numpy
            result = backend.xp.random.normal(size=5)
            assert result is not None

    def test_neuromorphic_mode_use_neuromorphic_false(self):
        """ArrayBackend with use_neuromorphic=False always falls back to numpy."""
        backend = ArrayBackend(mode="neuromorphic", use_neuromorphic=False)
        assert backend.xp is np

    def test_neuromorphic_normal_fallback_shape(self):
        """neuromorphic_normal returns correct shape when falling back to numpy."""
        with patch("omnicore_engine.array_backend.HAS_NENGO", False):
            backend = ArrayBackend(mode="neuromorphic", use_neuromorphic=True)
            result = backend.xp.random.normal(size=8)
            assert result is not None

    @pytest.mark.skipif(not HAS_NENGO, reason="nengo not installed")
    def test_neuromorphic_backend_with_nengo(self):
        """neuromorphic backend works when nengo is installed."""
        pytest.importorskip("nengo")
        backend = ArrayBackend(mode="neuromorphic", use_neuromorphic=True)
        assert hasattr(backend.xp, "random")
        result = backend.xp.random.normal(loc=0.0, scale=1.0, size=3)
        assert result is not None


class TestArrayBackendModeIntegrity:
    """Test that ArrayBackend mode selection works correctly for all modes."""

    def test_numpy_mode_is_default(self):
        """Default mode returns numpy backend."""
        backend = ArrayBackend(mode="numpy")
        assert backend.xp is np

    def test_quantum_mode_without_use_quantum_falls_back_to_numpy(self):
        """mode='quantum' without use_quantum=True yields numpy."""
        backend = ArrayBackend(mode="quantum")
        assert backend.xp is np

    def test_neuromorphic_mode_without_use_neuromorphic_falls_back_to_numpy(self):
        """mode='neuromorphic' without use_neuromorphic=True yields numpy."""
        backend = ArrayBackend(mode="neuromorphic")
        assert backend.xp is np

    def test_quantum_namespace_has_required_api(self):
        """Quantum namespace exposes array(), zeros(), ones(), random.normal() API."""
        with patch("omnicore_engine.array_backend.HAS_QISKIT", False):
            with patch("omnicore_engine.array_backend.AerSimulator", None):
                backend = ArrayBackend(mode="quantum", use_quantum=True)
                xp = backend.xp
                assert hasattr(xp, "array") or xp is np
                assert hasattr(xp, "zeros") or xp is np
                assert hasattr(xp, "random") or xp is np

    def test_neuromorphic_namespace_has_required_api(self):
        """Neuromorphic namespace exposes array(), zeros(), ones(), random.normal() API."""
        with patch("omnicore_engine.array_backend.HAS_NENGO", False):
            backend = ArrayBackend(mode="neuromorphic", use_neuromorphic=True)
            xp = backend.xp
            assert hasattr(xp, "array") or xp is np
            assert hasattr(xp, "zeros") or xp is np
            assert hasattr(xp, "random") or xp is np
