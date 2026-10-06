import base64
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .config import admin_password
from .auth import AdminStore
from .runtime import Runtime

os.umask(0o077)
PASSWORD = admin_password()
ADMINS_FILE = os.environ.get("SHARECOVEX_ADMINS_FILE", "/config/admins.json")
if not os.path.isfile(ADMINS_FILE) and (len(PASSWORD) < 6 or PASSWORD == "replace-with-a-long-random-password"):
    raise SystemExit("Set SHARECOVEX_ADMIN_PASSWORD or /config/admin.password to a unique password of at least 6 characters")
admins = AdminStore(ADMINS_FILE, PASSWORD)

runtime = Runtime()
WEB = Path(__file__).resolve().parent.parent / "web"


class Handler(BaseHTTPRequestHandler):
    def _headers(self, status, content_type="application/json", length=0):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()

    def _json(self, status, value):
        body = json.dumps(value).encode("utf-8")
        self._headers(status, length=len(body))
        self.wfile.write(body)

    def _authorized(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[6:], validate=True).decode("utf-8")
        except (ValueError, UnicodeError):
            return False
        user, _, password = raw.partition(":")
        return user if admins.verify(user, password) else None

    def _guard(self, write=False, require_json=False):
        user = self._authorized()
        if not user:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="ShareCoveX"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if write:
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self._json(403, {"error": "Cross-origin changes are not allowed"})
                return None
            if require_json and self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self._json(415, {"error": "JSON required"})
                return None
        return user

    def _state(self):
        value = runtime.status()
        value["admins"] = admins.names()
        return value

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 2 or length > 16384:
            raise ValueError("Invalid request size")
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/favicon.ico": ("favicon.ico", "image/x-icon"),
                  "/sharecovex-icon.svg": ("sharecovex-icon.svg", "image/svg+xml"),
                  "/sharecovex-icon.png": ("sharecovex-icon.png", "image/png")}
        # Browsers request the favicon before the authenticated panel itself.
        if path in ("/favicon.ico", "/sharecovex-icon.svg", "/sharecovex-icon.png"):
            file, content_type = assets[path]
            data = (WEB / file).read_bytes()
            self._headers(200, content_type, len(data))
            self.wfile.write(data)
            return
        if not self._guard():
            return
        locale_match = re.fullmatch(r"/locales/(en|de|es|fr|it|pt|sk|sv)\.json", path)
        if locale_match:
            file = WEB / "locales" / f"{locale_match.group(1)}.json"
            data = file.read_bytes()
            self._headers(200, "application/json; charset=utf-8", len(data))
            self.wfile.write(data)
            return
        if path == "/api/state":
            runtime.sync()
            self._json(200, self._state())
            return
        if path == "/api/tree":
            try:
                query = parse_qs(url.query, keep_blank_values=True)
                mount = query.get("mount", [""])[0]
                relative = query.get("path", [""])[0]
                self._json(200, runtime.tree(mount, relative))
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
            return
        if path not in assets:
            self._json(404, {"error": "Not found"})
            return
        file, content_type = assets[path]
        data = (WEB / file).read_bytes()
        self._headers(200, content_type, len(data))
        self.wfile.write(data)

    def _write(self, method):
        current_admin = self._guard(write=True, require_json=method != "DELETE")
        if not current_admin:
            return
        try:
            path = unquote(urlsplit(self.path).path)
            body = self._body() if method != "DELETE" else None
            if method == "PUT" and path == "/api/settings":
                runtime.update_settings(body)
            elif method == "POST" and path == "/api/users":
                runtime.add_user(body["name"], body["password"])
            elif method == "POST" and re.fullmatch(r"/api/users/[A-Za-z][A-Za-z0-9_-]{0,31}/password", path):
                runtime.password(path.split("/")[3], body["password"])
            elif method == "DELETE" and re.fullmatch(r"/api/users/[A-Za-z][A-Za-z0-9_-]{0,31}", path):
                runtime.delete_user(path.split("/")[3])
            elif method == "POST" and path == "/api/admins":
                admins.add(body["name"], body["password"])
            elif method == "POST" and re.fullmatch(r"/api/admins/[a-z][a-z0-9_-]{0,31}/password", path):
                admins.change_password(path.split("/")[3], body["password"])
            elif method == "DELETE" and re.fullmatch(r"/api/admins/[a-z][a-z0-9_-]{0,31}", path):
                name = path.split("/")[3]
                if name == current_admin:
                    raise ValueError("No puedes eliminar la cuenta con la que has iniciado sesion")
                admins.delete(name)
            else:
                self._json(404, {"error": "Not found"})
                return
            self._json(200, self._state())
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(500, {"error": str(exc)})

    def do_PUT(self):
        self._write("PUT")

    def do_POST(self):
        self._write("POST")

    def do_DELETE(self):
        self._write("DELETE")


if __name__ == "__main__":
    print("ShareCoveX panel listening on 0.0.0.0:8080", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
