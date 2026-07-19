"""Tests for the registration transform (hardware-free)."""

from __future__ import annotations

import numpy as np

from benchhud.overlay.transform import RegistrationTransform


def _ramp(width: int, height: int) -> np.ndarray:
    """A distinguishable gradient image (values vary across x and y)."""
    xs = np.arange(width, dtype=np.float32)[None, :]
    ys = np.arange(height, dtype=np.float32)[:, None]
    return (xs + ys).astype(np.float32)


def test_identity_reproduces_source() -> None:
    src = _ramp(20, 16)
    out = RegistrationTransform().warp(src, (20, 16))
    assert out.shape == (16, 20)
    assert np.allclose(out, src)


def test_output_size_matches_dst() -> None:
    src = _ramp(20, 16)
    out = RegistrationTransform(scale=1.5).warp(src, (40, 30))
    assert out.shape == (30, 40)


def test_pure_offset_shifts_image() -> None:
    src = _ramp(20, 16)
    out = RegistrationTransform(offset_x=3, offset_y=0).warp(src, (20, 16))
    # Everything shifts right by 3; columns 3.. equal the original 0..-3.
    assert np.allclose(out[:, 3:], src[:, :-3])
    # The exposed left border is filled with zeros.
    assert np.all(out[:, :3] == 0)


def test_uniform_image_preserved_under_scale() -> None:
    src = np.full((16, 20), 42.0, dtype=np.float32)
    out = RegistrationTransform(scale=2.0).warp(src, (20, 16))
    # Center region stays the constant value regardless of scaling.
    assert out[8, 10] == 42.0


def test_affine_matrix_shape() -> None:
    matrix = RegistrationTransform().affine_matrix((20, 16))
    assert matrix.shape == (2, 3)
