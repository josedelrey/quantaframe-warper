"""Authenticated loopback transport for the shared Python engine."""

import base64
import hmac
import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from .. import Engine, Project

STATIC = Path(__file__).parent


class Workbench:
    def __init__(self):
        self.engine = None
        self.lock = threading.Lock()

    def call(self, method, data):
        with self.lock:
            if method == "load":
                return Project.load(data["path"]).to_dict()
            if method == "save":
                project = Project.from_dict(data["project"])
                path = Path(data["path"]).resolve()
                if path in [
                    Path(project.video).resolve(),
                    *(Path(p).resolve() for p in project.assets.values()),
                ]:
                    raise ValueError("Project file cannot overwrite an input")
                project.save(path)
                return {"saved": str(path)}
            if method == "asset":
                with Image.open(data["path"]) as image:
                    if image.format != "PNG":
                        raise ValueError("Asset must be PNG")
                    image.thumbnail((128, 128))
                    buffer = BytesIO()
                    image.save(buffer, format="PNG")
                return {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(buffer.getvalue()).decode()
                }
            project = Project.from_dict(data["project"])
            if (
                self.engine is None
                or self.engine.video != Path(project.video).resolve()
            ):
                engine = Engine(project.video)
                if self.engine:
                    self.engine.close()
                self.engine = engine
            if method == "prepare":
                return self.engine.info
            if method == "render":
                image = Image.fromarray(self.engine.render(project, data["frame"]))
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                return {
                    "url": "data:image/png;base64,"
                    + base64.b64encode(buffer.getvalue()).decode()
                }
            if method == "export":
                self.engine.export(project, data["path"])
                return {"saved": data["path"]}
            raise ValueError("Unknown method")

    def close(self):
        if self.engine:
            self.engine.close()


def create_server():
    token = secrets.token_urlsafe(32)
    workbench = Workbench()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, content, kind="application/json"):
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
                result = workbench.call(
                    urlsplit(self.path).path.removeprefix("/api/"), data
                )
                self.reply(200, json.dumps(result).encode())
            except Exception as error:
                self.reply(400, json.dumps({"error": str(error)}).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    return server, workbench, f"http://127.0.0.1:{server.server_port}/?token={token}"


def main():
    server, workbench, url = create_server()
    print(json.dumps({"url": url}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        workbench.close()


if __name__ == "__main__":
    main()
