import base64
import json
import threading
import time
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest
from PIL import Image

from quantaframe_warper import Engine, Placement, Project
from quantaframe_warper.ui.server import create_server


def test_authenticated_workbench_matches_api(media, tmp_path):
    video, asset = media
    project = Project(str(video), {"red": str(asset)}, (Placement("red", x=32, y=32),))
    server, workbench, url = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base, query = url.split("?")
    token = query.split("=")[1]

    def request(method, data):
        request = Request(
            base + "api/" + method,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json", "X-Session-Token": token},
        )
        return urlopen(request, timeout=10)

    def call(method, data):
        with request(method, data) as response:
            return json.load(response)

    try:
        with pytest.raises(HTTPError) as denied:
            urlopen(base, timeout=5)
        assert denied.value.code == 403
        assert b"app.js?token=" in urlopen(url, timeout=5).read()
        info = call("prepare", {"project": project.to_dict()})
        assert info["frames"] == 12
        result = call("render", {"project": project.to_dict(), "frame": 5})
        pixels = np.asarray(
            Image.open(BytesIO(base64.b64decode(result["url"].split(",")[1])))
        )
        with Engine(video) as engine:
            np.testing.assert_array_equal(pixels, engine.render(project, 5))
        with request("playback", {"project": project.to_dict(), "frame": 5}) as response:
            assert response.headers.get_content_type() == "image/jpeg"
            with Image.open(BytesIO(response.read())) as preview:
                assert preview.size == (96, 64)
                assert preview.getpixel((38, 32))[0] > 220
        job = call(
            "export_start",
            {"project": project.to_dict(), "path": str(tmp_path / "output.mp4")},
        )
        deadline = time.monotonic() + 10
        while True:
            status = call("export_status", job)
            if status["state"] != "running":
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert status["state"] == "done"
        assert status["completed"] == status["total"] == 12
        assert (tmp_path / "output.mp4").stat().st_size > 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        workbench.close()


def test_import_progress_is_pollable_and_failed_import_can_retry(media, monkeypatch):
    import time

    from quantaframe_warper.ui import server

    entered, release = threading.Event(), threading.Event()
    real_engine = server.Engine

    def gated_engine(video, progress=None, **kwargs):
        def callback(phase, index):
            if progress:
                progress(phase, index)
            if phase == "source_frames":
                entered.set()
                assert release.wait(5)

        return real_engine(video, progress=callback, **kwargs)

    monkeypatch.setattr(server, "Engine", gated_engine)
    workbench = server.Workbench()
    video, _ = media
    data = {"project": Project(str(video)).to_dict()}

    def await_job(job):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = workbench.call("prepare_status", job)
            if status["state"] != "running":
                return status
            time.sleep(0.01)
        pytest.fail("Import did not finish")

    try:
        job = workbench.call("prepare_start", data)
        assert entered.wait(5)
        status = workbench.call("prepare_status", job)
        assert status["state"] == "running" and status["total"] == 12
        with pytest.raises(ValueError, match="already running"):
            workbench.call("prepare_start", data)
        release.set()
        assert await_job(job)["result"]["frames"] == 12
        missing = {"project": Project(str(video.parent / "missing.mov")).to_dict()}
        failed = await_job(workbench.call("prepare_start", missing))
        assert failed["state"] == "error" and failed["error"]
        # A failed import preserves the previous usable session.
        assert workbench.call("render", {**data, "frame": 0})["url"].startswith(
            "data:image/png"
        )
        assert await_job(workbench.call("prepare_start", data))["state"] == "done"
    finally:
        release.set()
        workbench.close()
