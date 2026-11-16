"""One disk-backed analysis session and deterministic preview/export engine."""

from __future__ import annotations

import tempfile
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image

from .configuration import Project
from .motion import composite, place, vector_field, warp


class Engine:
    """Decode once, analyze a single-reference P stream, replay independent layers.

    Use as a context manager to remove the temporary decoded frames and vectors.
    Sessions are synchronous and must not be accessed concurrently.
    """

    def __init__(self, video, *, progress=None):
        self.video = Path(video).resolve()
        self._temp = tempfile.TemporaryDirectory(prefix="quantaframe-")
        self.root = Path(self._temp.name)
        self._last_project = None
        self._last_frame = -1
        self._layers = {}
        try:
            self._prepare(progress or (lambda phase, index: None))
        except BaseException:
            self.close()
            raise

    def _prepare(self, progress):
        proxy = self.root / "analysis.mp4"
        self.count = 0
        with av.open(str(self.video)) as source, av.open(str(proxy), "w") as output:
            if not source.streams.video:
                raise ValueError("Input contains no video stream")
            stream = source.streams.video[0]
            self.fps = Fraction(stream.average_rate or 30)
            if self.fps <= 0:
                raise ValueError("Invalid source frame rate")
            self.width, self.height = stream.width, stream.height
            if self.width % 2 or self.height % 2:
                raise ValueError("MVP requires even video width and height")
            if stream.sample_aspect_ratio not in (None, Fraction(1)):
                raise ValueError("MVP requires square pixels")
            encoder = output.add_stream("libx264", rate=self.fps)
            encoder.width, encoder.height = self.width, self.height
            encoder.pix_fmt = "yuv420p"
            encoder.options = {
                "preset": "medium",
                "crf": "18",
                "x264-params": "bframes=0:ref=1:scenecut=0:keyint=1000000:weightp=0",
            }
            first_time = None
            for index, frame in enumerate(source.decode(stream)):
                if (frame.width, frame.height) != (self.width, self.height):
                    raise ValueError("Variable frame dimensions are unsupported")
                if frame.rotation or frame.interlaced_frame:
                    raise ValueError(
                        "Rotate/deinterlace input to progressive upright video first"
                    )
                if frame.time is not None:
                    if first_time is None:
                        first_time = frame.time
                    if abs(frame.time - first_time - float(index / self.fps)) > float(
                        0.2 / self.fps
                    ):
                        raise ValueError(
                            "Variable frame rate input is unsupported. Convert to CFR first"
                        )
                rgb = frame.to_ndarray(format="rgb24")
                Image.fromarray(rgb).save(self.root / f"{index}.png")
                analysis = av.VideoFrame.from_ndarray(rgb, format="rgb24")
                analysis.pts = index
                analysis.time_base = 1 / self.fps
                for packet in encoder.encode(analysis):
                    output.mux(packet)
                self.count += 1
                progress("decode", self.count)
            for packet in encoder.encode():
                output.mux(packet)
        if not self.count:
            raise ValueError("Video has no decoded frames")
        self.vector_count = 0
        decoded = 0
        with av.open(str(proxy)) as source:
            stream = source.streams.video[0]
            stream.codec_context.options = {"flags2": "+export_mvs"}
            for index, frame in enumerate(source.decode(stream)):
                if frame.pict_type not in (
                    av.video.frame.PictureType.I,
                    av.video.frame.PictureType.P,
                ):
                    raise ValueError("Analysis encoder violated the P-only contract")
                side = frame.side_data.get("MOTION_VECTORS")
                vectors = side.to_ndarray() if side is not None else []
                self.vector_count += len(vectors)
                # Persist sparse codec blocks rather than dense full-frame fields.
                np.save(self.root / f"{index}.npy", vectors, allow_pickle=False)
                decoded += 1
                progress("vectors", decoded)
        if decoded != self.count:
            raise ValueError("Analysis frame count differs from source")
        if self.count > 1 and not self.vector_count:
            raise ValueError("Decoder exported no motion vectors")

    @property
    def info(self):
        return dict(
            width=self.width,
            height=self.height,
            frames=self.count,
            fps=float(self.fps),
            vectors=self.vector_count,
            analysis="H.264 P-only, one reference, CRF 18",
        )

    def render(self, project: Project, frame: int):
        """Return RGB uint8 pixels. Frame indexes are zero based and in display order."""
        if Path(project.video).resolve() != self.video:
            raise ValueError("Project video differs from this engine session")
        if type(frame) is not int or not 0 <= frame < self.count:
            raise ValueError("Frame outside video")
        if any(p.start >= self.count for p in project.placements):
            raise ValueError("Placement starts outside video")
        signature = project.to_dict()
        if signature != self._last_project or frame < self._last_frame:
            for p in project.placements:
                if p.start <= frame:
                    with Image.open(project.assets[p.asset]) as asset:
                        if asset.format != "PNG":
                            raise ValueError("Assets must be PNG images")
            self._layers = {}
            self._last_frame = (
                min((p.start for p in project.placements), default=frame) - 1
            )
            self._last_project = signature
        for index in range(self._last_frame + 1, frame + 1):
            field = None
            for key, p in enumerate(project.placements):
                if index == p.start:
                    self._layers[key] = place(
                        project.assets[p.asset], p, self.width, self.height
                    )
                elif p.start < index < p.start + p.duration:
                    if field is None:
                        vectors = np.load(
                            self.root / f"{index}.npy", allow_pickle=False
                        )
                        field = vector_field(vectors, self.width, self.height)
                    self._layers[key] = warp(self._layers[key], field, p.strength)
                elif index >= p.start + p.duration:
                    self._layers.pop(key, None)
        self._last_frame = frame
        with Image.open(self.root / f"{frame}.png") as image:
            background = np.asarray(image)
        return composite(background, [self._layers[k] for k in sorted(self._layers)])

    def export(self, project, output, *, progress=None):
        """Export silent H.264 MP4 atomically. The source is never overwritten."""
        output = Path(output).resolve()
        if output.suffix.lower() != ".mp4":
            raise ValueError("Export path must end in .mp4")
        inputs = [self.video, *(Path(p).resolve() for p in project.assets.values())]
        if output in inputs:
            raise ValueError("Export cannot overwrite project inputs")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=output.parent, suffix=".mp4", delete=False
        ) as tmp:
            temporary = Path(tmp.name)
        try:
            with av.open(
                str(temporary), "w", options={"movflags": "+faststart"}
            ) as container:
                stream = container.add_stream("libx264", rate=self.fps)
                stream.width, stream.height = self.width, self.height
                stream.pix_fmt = "yuv420p"
                stream.options = {"crf": "18", "preset": "medium"}
                for index in range(self.count):
                    frame = av.VideoFrame.from_ndarray(
                        self.render(project, index), format="rgb24"
                    )
                    frame.pts = index
                    frame.time_base = 1 / self.fps
                    for packet in stream.encode(frame):
                        container.mux(packet)
                    if progress:
                        progress("export", index + 1)
                for packet in stream.encode():
                    container.mux(packet)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)

    def close(self):
        self._temp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
