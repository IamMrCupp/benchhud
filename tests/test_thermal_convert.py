"""Tests for the Sipeed T256s Y16 → °C conversion."""

from __future__ import annotations

import numpy as np
import pytest

from benchhud.thermal.convert import celsius_to_y16, y16_to_celsius


def test_known_value_300k() -> None:
    # 300 K = 19200 raw (300 * 64) -> 26.85 °C
    raw = np.array([[19200]], dtype=np.uint16)
    celsius = y16_to_celsius(raw)
    assert celsius[0, 0] == pytest.approx(26.85, abs=1e-3)


def test_absolute_zero_raw() -> None:
    raw = np.array([0], dtype=np.uint16)
    assert y16_to_celsius(raw)[0] == pytest.approx(-273.15, abs=1e-3)


def test_output_is_float32_same_shape() -> None:
    raw = np.zeros((256, 192), dtype=np.uint16)
    out = y16_to_celsius(raw)
    assert out.dtype == np.float32
    assert out.shape == (256, 192)


def test_elementwise() -> None:
    raw = np.array([19200, 19264], dtype=np.uint16)  # 300 K, 301 K
    out = y16_to_celsius(raw)
    assert out[0] == pytest.approx(26.85, abs=1e-3)
    assert out[1] == pytest.approx(27.85, abs=1e-3)


def test_round_trip() -> None:
    celsius = np.array([[0.0, 25.0, 100.0]], dtype=np.float32)
    back = y16_to_celsius(celsius_to_y16(celsius))
    # Round-trips within the raw quantization step (1/64 °C).
    assert np.allclose(back, celsius, atol=1.0 / 64)
