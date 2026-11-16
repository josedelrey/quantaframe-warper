"""Compare codec-only and hybrid cumulative PNG warping on a short clip."""

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from quantaframe_warper import Engine, Placement, Project
from quantaframe_warper.motion import vector_field


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


class ComparisonEngine(Engine):
    flow = None

    def enable_flow(self, blend):
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

    def _motion_field(self, index, size):
        if self.flow is None:
            return super()._motion_field(index, size)

        if self.last_gray is not None and self.last_gray[0] == index - 1:
            previous = self.last_gray[1]
        else:
            previous = self._gray(index - 1)
        current = self._gray(index)
        self.last_gray = index, current
        flow, valid = consistent_pull(self.flow, previous, current)

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("png", type=Path)
    parser.add_argument("--frames", type=int, default=72, help="First N source frames")
    parser.add_argument("--x", type=float, help="PNG center X in upright video pixels")
    parser.add_argument("--y", type=float, help="PNG center Y in upright video pixels")
    parser.add_argument("--scale", type=float, default=1, help="PNG scale")
    parser.add_argument("--rotation", type=float, default=0, help="Clockwise degrees")
    parser.add_argument(
        "--blend",
        type=float,
        default=0,
        help="Flow weight in covered blocks (0 to 1). Missing blocks use valid flow.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/optical-flow"))
    args = parser.parse_args()
    if args.frames < 2:
        parser.error("--frames must be at least 2")
    if not 0 <= args.blend <= 1:
        parser.error("--blend must be between 0 and 1")
    for path in (args.video, args.png):
        if not path.is_file():
            parser.error(f"File not found: {path}")
    with Image.open(args.png) as image:
        if image.format != "PNG":
            parser.error("The overlay must be a PNG file")

    def progress(phase, count):
        if phase != "source_frames" and (count == 1 or count % 12 == 0):
            print(f"  {phase}: {count} frames", flush=True)

    cv2.setNumThreads(min(cv2.getNumThreads(), 4))
    print(f"Preparing up to {args.frames} source frames", flush=True)
    with ComparisonEngine(
        args.video, frame_limit=args.frames, progress=progress
    ) as engine:
        if engine.count < 2:
            parser.error("The source must contain at least two frames")
        placement = Placement(
            "overlay",
            duration=engine.count,
            x=engine.width / 2 if args.x is None else args.x,
            y=engine.height / 2 if args.y is None else args.y,
            scale=args.scale,
            rotation=args.rotation,
        )
        project = Project(str(args.video), {"overlay": str(args.png)}, (placement,))
        print(
            f"Comparing {engine.count} frames at {engine.width} x {engine.height} "
            f"with PNG center ({placement.x:g}, {placement.y:g})",
            flush=True,
        )
        baseline = args.output_dir / "codec.mp4"
        print(f"Exporting {baseline}", flush=True)
        engine.export(project, baseline, progress=progress)

        engine.enable_flow(args.blend)
        hybrid = args.output_dir / "codec-flow.mp4"
        print(f"Exporting {hybrid} with blend {args.blend:g}", flush=True)
        engine.export(project, hybrid, progress=progress)
    print(f"Done. Compare the two MP4s in {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
