"""Codec destination-to-reference fields and premultiplied RGBA resampling."""

import cv2
import numpy as np
from PIL import Image


def vector_field(vectors, width, height):
    """Rasterize quarter-pixel codec vectors at destination blocks.

    Missing/intra blocks have zero displacement, preserving the layer there.
    The analysis stream guarantees every inter reference is the previous frame.
    """
    field = np.zeros((height, width, 2), np.float32)
    for mv in vectors:
        if mv["source"] >= 0:
            raise ValueError("Analysis contains a future reference")
        scale = int(mv["motion_scale"])
        if scale <= 0:
            raise ValueError("Invalid codec motion scale")
        x = int(mv["dst_x"]) - int(mv["w"]) // 2
        y = int(mv["dst_y"]) - int(mv["h"]) // 2
        field[
            max(y, 0) : min(y + int(mv["h"]), height),
            max(x, 0) : min(x + int(mv["w"]), width),
        ] = (int(mv["motion_x"]) / scale, int(mv["motion_y"]) / scale)
    return field


def warp(layer, field, strength=1):
    """Pull all four premultiplied channels from the same reference coordinates."""
    h, w = layer.shape[:2]
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    return cv2.remap(
        layer,
        xx + field[..., 0] * strength,
        yy + field[..., 1] * strength,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def place(path, placement, width, height):
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
