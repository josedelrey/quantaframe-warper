from dataclasses import replace
from io import BytesIO

import av
import numpy as np
import pytest
from PIL import Image

from quantaframe_warper import Engine, Placement, Project, engine


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


def test_import_discards_session_when_frame_cache_write_fails(media, monkeypatch):
    video, _ = media
    cache_roots = set()

    def fail_save(_image, path, **kwargs):
        cache_roots.add(path.parent)
        raise OSError("Disk full")

    monkeypatch.setattr(Image.Image, "save", fail_save)
    with pytest.raises(OSError, match="Disk full"):
        Engine(video)
    assert cache_roots and all(not path.exists() for path in cache_roots)


def test_scaled_playback_replays_skipped_frames_independently(media, monkeypatch):
    video, asset = media
    monkeypatch.setattr(engine, "_PLAYBACK_MAX_EDGE", 48)
    project = Project(
        str(video), {"red": str(asset)}, (Placement("red", x=32, y=32, duration=8),)
    )
    with Engine(video, playback=True) as session:
        exact = session.render(project, 5)
        first = session.playback_frame(project, 0)
        skipped = session.playback_frame(project, 5)
        assert session.playback_frame(project, 0) == first
        for index in range(1, 5):
            session.playback_frame(project, index)
        assert session.playback_frame(project, 5) == skipped
        with Image.open(BytesIO(skipped)) as preview:
            assert preview.size == (48, 32)
            r, g, b = preview.getpixel((21, 16))
            assert r > 225 and g < 30 and b < 30
        assert session.playback_frame(project, 8) == (session.root / "8.jpg").read_bytes()
        np.testing.assert_array_equal(session.render(project, 5), exact)


def test_settings_validation():
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



def test_rotated_variable_rate_phone_video_preserves_pixels_and_timing(tmp_path):
    from fractions import Fraction

    video = tmp_path / "phone.mov"
    pts = [100, 120, 145, 165, 200]
    rng = np.random.default_rng(16)
    texture = rng.integers(0, 256, (64, 96, 3), dtype=np.uint8)
    with av.open(str(video), "w") as output:
        stream = output.add_stream("libx264", rate=30)
        stream.width, stream.height = 96, 64
        stream.pix_fmt = "yuv420p"
        stream.time_base = stream.codec_context.time_base = Fraction(1, 600)
        stream.set_display_rotation(-90)
        for index, timestamp in enumerate(pts):
            frame = av.VideoFrame.from_ndarray(
                np.roll(texture, index * 2, axis=1), format="rgb24"
            )
            frame.pts, frame.time_base = timestamp, Fraction(1, 600)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    with av.open(str(video)) as source:
        frames = list(source.decode(video=0))
    assert frames[0].rotation == -90
    project = Project(str(video))
    with Engine(video) as engine:
        assert (engine.width, engine.height, engine.count) == (64, 96, 5)
        for index, frame in enumerate(frames):
            np.testing.assert_array_equal(
                engine.render(project, index),
                np.rot90(frame.to_ndarray(format="rgb24"), -1),
            )
        expected = [(p - pts[0]) / 600 for p in pts]
        assert engine.info["timestamps"] == pytest.approx(expected)
        path = tmp_path / "export.mp4"
        engine.export(project, path)
        with av.open(str(path)) as output:
            exported = list(output.decode(video=0))
        assert [f.time for f in exported] == pytest.approx(expected, abs=1e-5)
        assert (exported[0].width, exported[0].height, exported[0].rotation) == (
            64,
            96,
            0,
        )
