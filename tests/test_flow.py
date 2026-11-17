import cv2
import numpy as np
import pytest

from quantaframe_warper import Engine, Placement, Project
from quantaframe_warper.flow import consistent_pull, merge_fields


def test_pull_direction_and_missing_coverage():
    rng = np.random.default_rng(2026)
    previous = cv2.GaussianBlur(
        rng.integers(20, 235, (128, 160), dtype=np.uint8), (5, 5), 0
    )
    current = np.roll(previous, 8, axis=1)
    estimator = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
    flow, valid = consistent_pull(estimator, previous, current)
    assert valid[64, 80]
    np.testing.assert_allclose(flow[64, 80], [-8, 0], atol=0.25)
    codec = np.full_like(flow, np.nan)
    codec[64, 81] = 0
    valid[64, 82] = False
    result = merge_fields(codec, flow, valid, 0)
    np.testing.assert_allclose(result[64, 80], [-8, 0], atol=0.25)
    np.testing.assert_array_equal(result[64, 81:83], 0)


def test_default_flow_replay_and_preview_share_cached_estimates(media, monkeypatch):
    video, asset = media
    project = Project(str(video), {"red": str(asset)}, (Placement("red", x=32, y=32),))
    with Engine(video, playback=True) as session:
        # Remove codec coverage to expose the default flow path through rendering.
        for index in range(1, 6):
            np.save(session.root / f"{index}.npy", [])
        initial = session.render(project, 0)
        moved = session.render(project, 5)

        def red_center(pixels):
            mask = (pixels[..., 0] > 245) & (pixels[..., 1] < 10)
            return np.where(mask)[1].mean()

        assert red_center(moved) - red_center(initial) == pytest.approx(10, abs=1)

        def unexpected_recompute(index):
            pytest.fail(f"Recomputed cached flow for frame {index}")

        monkeypatch.setattr(session._flow, "_estimate", unexpected_recompute)
        preview = session.playback_frame(project, 5)
        session.playback_frame(project, 0)
        assert session.playback_frame(project, 5) == preview
        session.render(project, 0)
        np.testing.assert_array_equal(session.render(project, 5), moved)

    with Engine(video, optical_flow=False) as baseline:
        for index in range(1, 6):
            np.save(baseline.root / f"{index}.npy", [])
        assert red_center(baseline.render(project, 5)) == pytest.approx(
            red_center(initial)
        )
