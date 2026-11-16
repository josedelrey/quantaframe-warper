from fractions import Fraction

import av
import numpy as np
import pytest
from PIL import Image


@pytest.fixture
def media(tmp_path):
    video = tmp_path / "input.mp4"
    rng = np.random.default_rng(4)
    texture = rng.integers(0, 256, (64, 96, 3), dtype=np.uint8)
    with av.open(str(video), "w") as output:
        stream = output.add_stream("libx264", rate=24)
        stream.width, stream.height = 96, 64
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "12", "x264-params": "bframes=2:ref=3:keyint=24"}
        for index in range(12):
            frame = av.VideoFrame.from_ndarray(
                np.roll(texture, index * 2, axis=1), format="rgb24"
            )
            frame.pts = index
            frame.time_base = Fraction(1, 24)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    asset = tmp_path / "red.png"
    Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(asset)
    return video, asset
