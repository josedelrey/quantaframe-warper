"""Authenticated loopback transport for the shared Python engine."""

import base64
import hmac
import json
import logging
import mimetypes
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from .. import Engine, Project

STATIC = Path(__file__).parent
LOG = logging.getLogger(__name__)


class Workbench:
    def __init__(self):
        self.engine = None
        self.lock = threading.Lock()
        self.job_lock = threading.Lock()
        self.job = None
        self.worker = None
        self.closed = threading.Event()

    def start_prepare(self, data):
        # Validation is immediate. Expensive decoding runs outside the HTTP request.
        project = Project.from_dict(data["project"])
        with self.job_lock:
            if self.job and self.job["state"] == "running":
                raise ValueError("A video import is already running")
            job_id = secrets.token_hex(8)
            self.job = dict(
                id=job_id, state="running", phase="opening", completed=0, total=0
            )
        LOG.info("Import %s started: %s", job_id, project.video)
        last_progress = {"phase": None, "time": 0}

        def progress(phase, index):
            if self.closed.is_set():
                raise RuntimeError("Workbench closed")
            with self.job_lock:
                if phase == "source_frames":
                    self.job["total"] = index
                else:
                    self.job.update(phase=phase, completed=index)
                    if phase == "vectors":
                        self.job["total"] = max(self.job["total"], index)
                total = self.job["total"]
            now = time.monotonic()
            if phase != last_progress["phase"] or now - last_progress["time"] >= 2:
                LOG.info(
                    "Import %s: %s %d / %s", job_id, phase, index, total or "unknown"
                )
                last_progress.update(phase=phase, time=now)

        def run():
            try:
                with self.lock:
                    self.ensure_engine(project, progress)
                    result = self.engine.info
                with self.job_lock:
                    self.job.update(state="done", result=result)
                LOG.info(
                    "Import %s ready: %d frames, %d x %d",
                    job_id,
                    result["frames"],
                    result["width"],
                    result["height"],
                )
            except Exception as error:
                LOG.exception("Import %s failed", job_id)
                with self.job_lock:
                    self.job.update(state="error", error=str(error))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        return {"id": job_id}

    def start_export(self, data):
        project = Project.from_dict(data["project"])
        path = data["path"]
        with self.job_lock:
            if self.job and self.job["state"] == "running":
                raise ValueError("Another task is already running")
            job_id = secrets.token_hex(8)
            self.job = dict(id=job_id, state="running", phase="export", completed=0, total=0)

        def run():
            try:
                with self.lock:
                    self.ensure_engine(project)
                    with self.job_lock:
                        self.job["total"] = self.engine.count

                    def progress(phase, index):
                        with self.job_lock:
                            self.job.update(phase=phase, completed=index)

                    self.engine.export(project, path, progress=progress)
                with self.job_lock:
                    self.job.update(state="done", result={"saved": path})
                LOG.info("Export complete: %s", path)
            except Exception as error:
                LOG.exception("Export %s failed", job_id)
                with self.job_lock:
                    self.job.update(state="error", error=str(error))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        return {"id": job_id}

    def ensure_engine(self, project, progress=None):
        if self.engine is None or self.engine.video != Path(project.video).resolve():
            LOG.info("Opening video: %s", project.video)
            engine = Engine(project.video, progress=progress, playback=True)
            if self.engine:
                self.engine.close()
            self.engine = engine

    def call(self, method, data):
        if method == "prepare_start":
            return self.start_prepare(data)
        if method == "export_start":
            return self.start_export(data)
        if method in ("prepare_status", "export_status"):
            with self.job_lock:
                if self.job is None or self.job["id"] != data["id"]:
                    raise ValueError("Unknown import job")
                return dict(self.job)
        with self.lock:
            if method == "asset":
                with Image.open(data["path"]) as image:
                    if image.format != "PNG":
                        raise ValueError("Asset must be PNG")
                    width, height = image.size
                    image.thumbnail((128, 128))
                    buffer = BytesIO()
                    image.save(buffer, format="PNG")
                return {
                    "width": width, "height": height,
                    "url": "data:image/png;base64,"
                    + base64.b64encode(buffer.getvalue()).decode()
                }
            project = Project.from_dict(data["project"])
            self.ensure_engine(project)
            if method == "prepare":
                return self.engine.info
            if method == "render":
                image = Image.fromarray(self.engine.render(project, data["frame"]))
                buffer = BytesIO()
                image.save(buffer, format="PNG", compress_level=1)
                return {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(buffer.getvalue()).decode()
                }
            if method == "playback":
                return self.engine.playback_frame(project, data["frame"])
            raise ValueError("Unknown method")

    def close(self):
        self.closed.set()
        if self.worker:
            self.worker.join()
        with self.lock:
            if self.engine:
                self.engine.close()


def create_server():
    token = secrets.token_urlsafe(32)
    workbench = Workbench()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # The default request line includes the authentication query string.
            # Log only method, path and response status from reply().
            pass

        def reply(self, status, content, kind="application/json"):
            LOG.debug("%s %s -> %d", self.command, urlsplit(self.path).path, status)
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(content)

        def authorized(self):
            query = parse_qs(urlsplit(self.path).query)
            supplied = self.headers.get("X-Session-Token", query.get("token", [""])[0])
            return hmac.compare_digest(supplied, token)

        def do_GET(self):
            if not self.authorized():
                self.reply(403, b"{}")
                return
            name = urlsplit(self.path).path
            files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
            if name not in files:
                self.reply(404, b"{}")
                return
            path = STATIC / files[name]
            content = path.read_bytes()
            if name == "/":
                content = content.replace(b"/app.js", f"/app.js?token={token}".encode())
                content = content.replace(
                    b"</head>",
                    f'<link rel="stylesheet" href="/style.css?token={token}"></head>'.encode(),
                )
            self.reply(200, content, mimetypes.guess_type(path)[0] or "text/plain")

        def do_POST(self):
            if not self.authorized() or self.headers.get("Origin") not in (
                None,
                f"http://127.0.0.1:{self.server.server_port}",
            ):
                self.reply(403, b"{}")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 2_000_000:
                    raise ValueError("Invalid request length")
                data = json.loads(self.rfile.read(length))
                method = urlsplit(self.path).path.removeprefix("/api/")
                result = workbench.call(method, data)
                if method == "playback":
                    self.reply(200, result, "image/jpeg")
                else:
                    self.reply(200, json.dumps(result).encode())
            except Exception as error:
                LOG.exception("%s %s failed", self.command, urlsplit(self.path).path)
                self.reply(400, json.dumps({"error": str(error)}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    return server, workbench, f"http://127.0.0.1:{server.server_port}/?token={token}"


def main():
    default_level = "INFO" if os.environ.get("QUANTAFRAME_DEV") == "1" else "WARNING"
    logging.basicConfig(
        level=os.environ.get("QUANTAFRAME_LOG_LEVEL", default_level).upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server, workbench, url = create_server()
    # Stdout's first line is a private readiness message consumed by Electron.
    print(json.dumps({"url": url}), flush=True)
    LOG.info("Backend listening on %s", urlsplit(url).netloc)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        LOG.info("Backend shutting down")
        server.server_close()
        workbench.close()


if __name__ == "__main__":
    main()
