"""Dense optical flow guidance for cumulative codec warping."""

import cv2
import numpy as np
from PIL import Image

from .motion import vector_field


def consistent_pull(estimator, previous, current):
    """Compute current-to-previous flow and check its round trip."""
    backward = estimator.calc(current, previous, None)
    forward = estimator.calc(previous, current, None)
    height, width = current.shape
    y, x = np.mgrid[:height, :width].astype(np.float32)
    source_x = x + backward[..., 0]
    source_y = y + backward[..., 1]
    round_trip = backward + cv2.remap(forward, source_x, source_y, cv2.INTER_LINEAR)
    error = np.hypot(round_trip[..., 0], round_trip[..., 1])
    valid = (
        (error <= 1.0)
        & (source_x >= 0)
        & (source_x <= width - 1)
        & (source_y >= 0)
        & (source_y <= height - 1)
    )
    backward[~valid] = 0
    return backward, valid


def merge_fields(codec, flow, valid, blend):
    """Fill missing vectors first, then optionally blend covered pixels."""
    missing = np.isnan(codec[..., 0])
    codec[missing] = 0
    codec[missing & valid] = flow[missing & valid]
    if blend:
        covered = ~missing & valid
        codec[covered] = (1 - blend) * codec[covered] + blend * flow[covered]
    return codec


class FlowGuide:
    """Session-local DIS estimates shared by preview and full-resolution rendering."""

    def __init__(self, root, width, height, blend=0):
        self.root = root
        self.width, self.height = width, height
        scale = min(1, 960 / max(self.width, self.height))
        self.flow_size = (
            max(16, round(self.width * scale)),
            max(16, round(self.height * scale)),
        )
        self.flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
        self.blend = blend
        self.last_gray = None

    def _gray(self, index):
        with Image.open(self.root / f"{index}.png") as image:
            gray = np.asarray(image.convert("L"))
        if gray.shape[::-1] != self.flow_size:
            gray = cv2.resize(gray, self.flow_size, interpolation=cv2.INTER_AREA)
        return gray

    def _estimate(self, index):
        if self.last_gray is not None and self.last_gray[0] == index - 1:
            previous = self.last_gray[1]
        else:
            previous = self._gray(index - 1)
        current = self._gray(index)
        self.last_gray = index, current
        return consistent_pull(self.flow, previous, current)

    def _cached_flow(self, index):
        path = self.root / f"flow-{index}.npz"
        if path.exists():
            with np.load(path, allow_pickle=False) as cached:
                flow, valid = cached["flow"], cached["valid"]
        else:
            flow, valid = self._estimate(index)
            temporary = path.with_suffix(".tmp")
            try:
                with temporary.open("wb") as output:
                    np.savez(output, flow=flow, valid=valid)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return flow, valid

    def motion_field(self, index, size):
        flow, valid = self._cached_flow(index)
        width, height = size
        if size != self.flow_size:
            flow = cv2.resize(flow, size, interpolation=cv2.INTER_LINEAR)
            flow[..., 0] *= width / self.flow_size[0]
            flow[..., 1] *= height / self.flow_size[1]
            # Accept interpolated flow only where the contributing samples agree.
            valid = (
                cv2.resize(
                    valid.astype(np.float32), size, interpolation=cv2.INTER_LINEAR
                )
                >= 1.0 - 1e-6
            )
        vectors = np.load(self.root / f"{index}.npy", allow_pickle=False)
        scale = (width / self.width, height / self.height)
        codec = vector_field(vectors, width, height, scale, missing=np.nan)
        return merge_fields(codec, flow, valid, self.blend)
