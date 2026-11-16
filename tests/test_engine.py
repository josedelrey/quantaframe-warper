from dataclasses import replace

import av
import numpy as np
import pytest
from PIL import Image

from quantaframe_warper import Engine, Placement, Project


def test_real_b_frame_input_vectors_replay_and_export(media, tmp_path):
    video, asset = media
    with av.open(str(video)) as source:
        assert any(
            f.pict_type == av.video.frame.PictureType.B for f in source.decode(video=0)
        )
    project = Project(
        str(video),
        {"red": str(asset)},
        (Placement("red", start=1, duration=8, x=30, y=30),),
    )
    with Engine(video) as engine:
        root = engine.root
        assert engine.count == 12
        assert engine.vector_count > 0
        field = np.load(root / "3.npy")
        assert np.median(field["motion_x"] / field["motion_scale"]) == pytest.approx(
            -2, abs=0.5
        )
        initial = engine.render(project, 1)
        later = engine.render(project, 5)
        # Layer actually moves in the expected direction on a translated texture.
        assert later[30, 40, 0] > 245 and later[30, 40, 1] < 10
        assert not np.array_equal(initial, later)
        np.testing.assert_array_equal(engine.render(project, 5), later)
        engine.render(project, 2)
        np.testing.assert_array_equal(engine.render(project, 5), later)
        with Image.open(root / "9.png") as image:
            np.testing.assert_array_equal(engine.render(project, 9), np.asarray(image))
        static = replace(
            project, placements=(replace(project.placements[0], strength=0),)
        )
        assert engine.render(static, 5)[30, 25].tolist() == [255, 0, 0]
        output = tmp_path / "export.mp4"
        engine.export(project, output)
        with av.open(str(output)) as source:
            frames = list(source.decode(video=0))
        assert len(frames) == engine.count
        assert frames[5].time == pytest.approx(5 / 24)
        reference = engine.render(project, 5).astype(float)
        assert (
            np.abs(
                frames[5].to_ndarray(format="rgb24").astype(float) - reference
            ).mean()
            < 15
        )
        with pytest.raises(ValueError, match="overwrite"):
            engine.export(project, video)
        with pytest.raises(ValueError, match="outside"):
            engine.render(project, 12)
    assert not root.exists()


def test_config_roundtrip_and_relative_paths(media, tmp_path):
    video, asset = media
    project = Project(str(video), {"red": str(asset)}, (Placement("red", x=20, y=30),))
    path = tmp_path / "project.yaml"
    project.save(path)
    assert Project.load(path) == project
    path.write_text("video: input.mp4\nassets:\n  red: red.png\nplacements: []\n")
    assert Project.load(path).video == str(video)
    path.write_text("video: a\nvideo: b\n")
    with pytest.raises(ValueError, match="duplicate"):
        Project.load(path)
    with pytest.raises(ValueError):
        Project.from_dict({"video": "a", "unknown": 2})
    with pytest.raises(ValueError):
        Placement("red", strength=float("nan"))
    with pytest.raises(ValueError):
        Placement("red", duration=0)


def test_opposing_codec_regions_deform_one_layer(tmp_path):
    video = tmp_path / "split.mp4"
    rng = np.random.default_rng(42)
    texture = rng.integers(0, 256, (96, 128, 3), dtype=np.uint8)
    with av.open(str(video), "w") as output:
        stream = output.add_stream("libx264", rate=24)
        stream.width, stream.height = 128, 96
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "12"}
        for index in range(8):
            pixels = texture.copy()
            pixels[:48] = np.roll(texture[:48], index * 2, axis=1)
            pixels[48:] = np.roll(texture[48:], -index * 2, axis=1)
            for packet in stream.encode(
                av.VideoFrame.from_ndarray(pixels, format="rgb24")
            ):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    asset = tmp_path / "bar.png"
    Image.new("RGBA", (12, 64), (255, 0, 0, 255)).save(asset)
    project = Project(
        str(video), {"bar": str(asset)}, (Placement("bar", duration=8, x=64, y=48),)
    )
    with Engine(video) as engine:
        pixels = engine.render(project, 5)
        # Opaque red identifies the rendered layer against the textured background.
        alpha = (
            (pixels[..., 0] > 250) & (pixels[..., 1] < 5) & (pixels[..., 2] < 5)
        ).astype(float)
        xx = np.arange(128)
        upper_center = (alpha[24:40].sum(axis=0) * xx).sum() / alpha[24:40].sum()
        lower_center = (alpha[56:72].sum(axis=0) * xx).sum() / alpha[56:72].sum()
        assert upper_center > 70
        assert lower_center < 58
        assert upper_center - lower_center > 15


def test_layer_order_start_duration_and_safe_save(media, tmp_path):
    video, red = media
    blue = tmp_path / "blue.png"
    Image.new("RGBA", (16, 16), (0, 0, 255, 255)).save(blue)
    project = Project(
        str(video),
        {"red": str(red), "blue": str(blue)},
        (
            Placement("red", start=0, duration=8, x=32, y=32, strength=0),
            Placement("blue", start=2, duration=2, x=32, y=32, strength=0),
        ),
    )
    with Engine(video) as engine:
        assert engine.render(project, 0)[32, 32].tolist() == [255, 0, 0]
        assert engine.render(project, 2)[32, 32].tolist() == [0, 0, 255]
        assert engine.render(project, 4)[32, 32].tolist() == [255, 0, 0]
    before = video.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        project.save(video)
    assert video.read_bytes() == before
