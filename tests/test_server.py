import base64
import json
import threading
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

    def call(method, data):
        request = Request(
            base + "api/" + method,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json", "X-Session-Token": token},
        )
        return json.load(urlopen(request, timeout=10))

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
        path = str(tmp_path / "saved.yaml")
        call("save", {"project": project.to_dict(), "path": path})
        assert call("load", {"path": path}) == project.to_dict()
        call(
            "export",
            {"project": project.to_dict(), "path": str(tmp_path / "output.mp4")},
        )
        assert (tmp_path / "output.mp4").stat().st_size > 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        workbench.close()
