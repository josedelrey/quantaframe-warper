"""Compare cumulative PNG warping with optical flow."""

import argparse
from pathlib import Path

import cv2
from PIL import Image

from quantaframe_warper import Engine, Placement, Project
from quantaframe_warper.flow import FlowGuide


class ComparisonEngine(Engine):
    def enable_flow(self, blend):
        self._flow = FlowGuide(self.root, self.width, self.height, blend)
        self._replay.project = None
        self._playback_replay.project = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("png", type=Path)
    duration = parser.add_mutually_exclusive_group()
    duration.add_argument("--frames", type=int, help="First N source frames")
    duration.add_argument("--seconds", type=float, default=None, help="First N seconds")
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
    if args.frames is not None and args.frames < 2:
        parser.error("--frames must be at least 2")
    if args.seconds is not None and not 0 < args.seconds < float("inf"):
        parser.error("--seconds must be positive and finite")
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
    seconds = (
        args.seconds
        if args.seconds is not None
        else (6 if args.frames is None else None)
    )
    print(f"Preparing {args.frames or str(seconds) + ' seconds'}", flush=True)
    with ComparisonEngine(
        args.video,
        frame_limit=args.frames,
        seconds=seconds,
        progress=progress,
        optical_flow=False,
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
