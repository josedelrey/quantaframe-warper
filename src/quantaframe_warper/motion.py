"""Codec destination-to-reference fields and premultiplied RGBA resampling."""

import cv2
import numpy as np
from PIL import Image


def vector_field(vectors, width, height, pixel_scale=(1, 1), *, missing=0):
    """Rasterize quarter-pixel codec vectors at destination blocks.

    Missing/intra blocks use the specified fill, zero by default.
    The analysis stream guarantees every inter reference is the previous frame.
    """
    if not len(vectors):
        return np.full((height, width, 2), missing, np.float32)
    vectors = np.asarray(vectors)
    if np.any(vectors["source"] >= 0):
        raise ValueError("Analysis contains a future reference")
    scale = vectors["motion_scale"]
    if np.any(scale <= 0):
        raise ValueError("Invalid codec motion scale")
    sx, sy = pixel_scale
    x = vectors["dst_x"].astype(np.int32) - vectors["w"] // 2
    y = vectors["dst_y"].astype(np.int32) - vectors["h"] // 2
    # Project destination block boundaries and displacements into the preview grid.
    left = np.ceil(x * sx).clip(0, width).astype(int)
    right = np.ceil((x + vectors["w"]) * sx).clip(0, width).astype(int)
    top = np.ceil(y * sy).clip(0, height).astype(int)
    bottom = np.ceil((y + vectors["h"]) * sy).clip(0, height).astype(int)
    dx = vectors["motion_x"] / scale * sx
    dy = vectors["motion_y"] / scale * sy
    if pixel_scale != (1, 1):
        # A rectangle contributes its ID through four corner updates. The high
        # bits let us detect overlaps and fall back to painting in vector order.
        offset = 1 << 32
        ids = offset + np.arange(1, len(vectors) + 1, dtype=np.int64)
        blocks = np.zeros((height + 1, width + 1), dtype=np.int64)
        np.add.at(blocks, (top, left), ids)
        np.add.at(blocks, (bottom, right), ids)
        np.subtract.at(blocks, (top, right), ids)
        np.subtract.at(blocks, (bottom, left), ids)
        blocks = blocks[:height, :width]
        np.cumsum(blocks, axis=1, out=blocks)
        np.cumsum(blocks, axis=0, out=blocks)
        if blocks.max() < 2 * offset:
            blocks &= offset - 1
            displacements = np.empty((len(vectors) + 1, 2), dtype=np.float32)
            displacements[0] = missing
            displacements[1:, 0] = dx
            displacements[1:, 1] = dy
            return displacements[blocks]
    field = np.full((height, width, 2), missing, np.float32)
    for x0, x1, y0, y1, u, v in zip(left, right, top, bottom, dx, dy):
        field[y0:y1, x0:x1] = (u, v)
    return field


def warp(layer, field, strength=1):
    """Pull all four premultiplied channels from the same reference coordinates."""
    h, w = layer.shape[:2]
    xx = np.arange(w, dtype=np.float32)[None, :]
    yy = np.arange(h, dtype=np.float32)[:, None]
    return cv2.remap(
        layer,
        xx + field[..., 0] * strength,
        yy + field[..., 1] * strength,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def place(path, placement, width, height, pixel_scale=(1, 1)):
    with Image.open(path) as image:
        if image.format != "PNG":
            raise ValueError("Assets must be PNG images")
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255
    rgba[..., :3] *= rgba[..., 3:4]
    h, w = rgba.shape[:2]
    # Positive angles are clockwise in screen coordinates. Position is the center.
    angle = np.deg2rad(placement.rotation)
    a, b = np.cos(angle) * placement.scale, np.sin(angle) * placement.scale
    matrix = np.array(
        [
            [a, -b, placement.x - a * w / 2 + b * h / 2],
            [b, a, placement.y - b * w / 2 - a * h / 2],
        ],
        np.float32,
    )
    matrix *= np.asarray(pixel_scale, dtype=np.float32)[:, None]
    return cv2.warpAffine(
        rgba,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def composite(background, layers):
    result = background.astype(np.float32) / 255
    for layer in layers:
        result = layer[..., :3] + result * (1 - layer[..., 3:4])
    return np.clip(np.rint(result * 255), 0, 255).astype(np.uint8)
