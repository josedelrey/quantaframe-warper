"""Generate a reproducible two-region motion experiment in outputs/demo."""

from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from PIL import Image, ImageDraw


def main():
    root = Path("outputs/demo").resolve()
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    texture = rng.integers(30, 150, (320, 480, 3), dtype=np.uint8)
    texture = np.asarray(Image.fromarray(texture).resize((120, 80)).resize((480, 320)))
    video = root / "motion.mp4"
    with av.open(str(video), "w") as output:
        stream = output.add_stream("libx264", rate=24)
        stream.width, stream.height = 480, 320
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "16", "x264-params": "bframes=2:ref=3"}
        for index in range(72):
            pixels = texture.copy()
            pixels[:160] = np.roll(texture[:160], index * 2, axis=1)
            pixels[160:] = np.roll(texture[160:], -index * 2, axis=1)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 24)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    asset = root / "eye.png"
    image = Image.new("RGBA", (160, 160))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (4, 4, 156, 156),
        fill=(240, 240, 225, 220),
        outline=(180, 255, 100, 255),
        width=4,
    )
    draw.ellipse((44, 44, 116, 116), fill=(70, 160, 220, 255))
    draw.ellipse((63, 63, 97, 97), fill=(10, 15, 20, 255))
    draw.ellipse((66, 62, 78, 74), fill=(255, 255, 255, 255))
    image.save(asset)
    print(video)


if __name__ == "__main__":
    main()
