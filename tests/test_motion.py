import numpy as np
import pytest
from PIL import Image

from quantaframe_warper import Placement
from quantaframe_warper.motion import composite, place, vector_field, warp

DTYPE = [
    ("source", "i4"),
    ("w", "u1"),
    ("h", "u1"),
    ("dst_x", "i2"),
    ("dst_y", "i2"),
    ("motion_x", "i4"),
    ("motion_y", "i4"),
    ("motion_scale", "u2"),
]


def test_codec_pull_direction_and_independent_regions():
    vectors = np.array(
        [(-1, 4, 4, 2, 2, -4, 0, 4), (-1, 4, 4, 6, 2, 4, 0, 4)], dtype=DTYPE
    )
    field = vector_field(vectors, 8, 4)
    layer = np.zeros((4, 8, 4), np.float32)
    layer[1, 1] = [1, 0, 0, 1]
    layer[1, 6] = [0, 1, 0, 1]
    result = warp(layer, field)
    assert result[1, 2].tolist() == [1, 0, 0, 1]
    assert result[1, 5].tolist() == [0, 1, 0, 1]
    assert result[..., 3].sum() == 2
    np.testing.assert_array_equal(warp(layer, field, 0), layer)


def test_fractional_vector_moves_alpha_with_color():
    vectors = np.array([(-1, 4, 4, 2, 2, -2, 0, 4)], dtype=DTYPE)
    layer = np.zeros((4, 4, 4), np.float32)
    layer[1, 1] = [0.5, 0, 0, 0.5]
    result = warp(layer, vector_field(vectors, 4, 4))
    assert result[1, 1].tolist() == [0.25, 0, 0, 0.25]
    assert result[1, 2].tolist() == [0.25, 0, 0, 0.25]
    assert result[1, 0].tolist() == [0, 0, 0, 0]


def test_missing_blocks_hold_and_future_references_rejected():
    np.testing.assert_array_equal(vector_field([], 4, 4), np.zeros((4, 4, 2)))
    with pytest.raises(ValueError, match="future"):
        vector_field(np.array([(1, 4, 4, 2, 2, 0, 0, 4)], dtype=DTYPE), 4, 4)


def test_preview_scales_block_bounds_and_preserves_overwrite_order():
    first = (-1, 4, 4, 2, 2, -4, 0, 4)
    second = (-1, 4, 4, 4, 2, 4, 0, 4)
    scale = (0.5, 0.5)
    single = vector_field(np.array([first], dtype=DTYPE), 4, 2, scale)
    np.testing.assert_array_equal(single[..., 0], [[-0.5, -0.5, 0, 0]] * 2)
    overlap = vector_field(np.array([first, second], dtype=DTYPE), 4, 2, scale)
    np.testing.assert_array_equal(overlap[..., 0], [[-0.5, 0.5, 0.5, 0]] * 2)


def test_premultiplication_prevents_transparent_color_bleed(tmp_path):
    asset = np.zeros((4, 4, 4), np.uint8)
    asset[:] = [0, 255, 0, 0]
    asset[1:3, 1:3] = [255, 0, 0, 128]
    path = tmp_path / "asset.png"
    Image.fromarray(asset).save(path)
    layer = place(path, Placement("a", x=4, y=4, rotation=30), 8, 8)
    assert np.all(layer[..., 1] == 0)
    np.testing.assert_allclose(layer[..., 0], layer[..., 3])
    background = np.full((8, 8, 3), 255, np.uint8)
    output = composite(background, [layer])
    assert output[..., 0].min() == 255
    assert output[..., 1].min() < 255
    assert np.all(background == 255)
