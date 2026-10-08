import json
import os
import re
import signal
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .auth import MIN_PASSWORD, AdminStore
from .config import admin_password
from .runtime import Runtime
from .session import COOKIE, LIFETIME, LoginLimiter, SessionStore

os.umask(0o077)
PASSWORD = admin_password()
ADMINS_FILE = os.environ.get("SHARECOVEX_ADMINS_FILE", "/config/admins.json")
# Set when the image is built; shown in the panel footer.
VERSION = os.environ.get("SHARECOVEX_VERSION", "dev")
if not os.path.isfile(ADMINS_FILE) and (len(PASSWORD) < MIN_PASSWORD or PASSWORD == "replace-with-a-long-random-password"):
    raise SystemExit("Set SHARECOVEX_ADMIN_PASSWORD or /config/admin.password to a unique password "
                     f"of at least {MIN_PASSWORD} characters")
admins = AdminStore(ADMINS_FILE, PASSWORD)
sessions = SessionStore()
limiter = LoginLimiter()

runtime = Runtime()
WEB = Path(__file__).resolve().parent.parent / "web"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/favicon.ico": ("favicon.ico", "image/x-icon"),
          "/sharecovex-icon.svg": ("sharecovex-icon.svg", "image/svg+xml"),
          "/sharecovex-icon.png": ("sharecovex-icon.png", "image/png")}


class Handler(BaseHTTPRequestHandler):
    # A connection that sends nothing is closed instead of holding a thread.
    timeout = 30

    def log_request(self, code="-", size="-"):
        # The health check asks every few seconds; it would bury the rest of the log.
        if urlsplit(self.path).path != "/healthz":
            super().log_request(code, size)

    def _headers(self, status, content_type="application/json", length=0, extra=(), cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        # Nothing is told to other sites, and a form sent from this page
        # still names its own origin, which `_same_origin` checks.
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()

    def _json(self, status, value, extra=()):
        body = json.dumps(value).encode("utf-8")
        self._headers(status, length=len(body), extra=extra)
        self.wfile.write(body)

    def _file(self, name, content_type):
        data = (WEB / name).read_bytes()
        # The icons are the only files a browser may keep between visits.
        cache = "public, max-age=86400" if content_type.startswith("image/") else "no-store"
        self._headers(200, content_type, len(data), cache=cache)
        self.wfile.write(data)

    def _token(self):
        try:
            morsel = SimpleCookie(self.headers.get("Cookie", "")).get(COOKIE)
        except Exception:  # a malformed cookie header is no session
            return None
        return morsel.value if morsel else None

    def _cookie(self, token, max_age):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "").lower() == "https" else ""
        return ("Set-Cookie", f"{COOKIE}={token}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}")

    def _same_origin(self, require_json):
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            self._json(403, {"error": "Cross-origin changes are not allowed"})
            return False
        if require_json and self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._json(415, {"error": "JSON required"})
            return False
        return True

    def _guard(self, write=False, require_json=False):
        user = sessions.user(self._token())
        if not user:
            self._json(401, {"error": "Sesion no iniciada"})
            return None
        if write and not self._same_origin(require_json):
            return None
        return user

    def _state(self):
        value = runtime.status()
        value["admins"] = admins.names()
        value["version"] = VERSION
        return value

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 2 or length > 16384:
            raise ValueError("Invalid request size")
        return json.loads(self.rfile.read(length))

    def _answered(self, handler, *arguments):
        """A request is always answered: an error nobody expected is logged
        and returned, instead of closing the connection on the browser."""
        try:
            handler(*arguments)
        except Exception as exc:
            print(f"ShareCoveX panel: {type(exc).__name__}: {exc}", flush=True)
            try:
                self._json(500, {"error": str(exc) or type(exc).__name__})
            except OSError:
                pass

    def do_GET(self):
        self._answered(self._get)

    def _get(self):
        url = urlsplit(self.path)
        path = url.path
        # The page, its scripts and its texts hold nothing private: they are
        # what shows the sign-in form. Everything under /api needs a session.
        if path in ASSETS:
            self._file(*ASSETS[path])
            return
        locale_match = re.fullmatch(r"/locales/(en|de|es|fr|it|pt|sk|sv)\.json", path)
        if locale_match:
            self._file(f"locales/{locale_match.group(1)}.json", "application/json; charset=utf-8")
            return
        if path == "/healthz":
            healthy = runtime.healthy()
            self._json(200 if healthy else 503, {"status": "ok" if healthy else "stopping"})
            return
        user = self._guard()
        if not user:
            return
        if path == "/api/state":
            state = self._state()
            state["user"] = user
            self._json(200, state)
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
        self._json(404, {"error": "Not found"})

    def _login(self):
        if not self._same_origin(require_json=True):
            return
        address = self.client_address[0]
        wait = limiter.retry_after(address)
        if wait:
            self._json(429, {"error": "Demasiados intentos. Espera unos minutos antes de volver a probar.",
                             "retry_after": wait}, extra=[("Retry-After", str(wait))])
            return
        try:
            body = self._body()
            name, password = body["name"], body["password"]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            self._json(400, {"error": "Invalid request"})
            return
        if not admins.verify(name, password):
            limiter.failed(address)
            self._json(401, {"error": "Usuario o contrasena incorrectos"})
            return
        limiter.succeeded(address)
        state = self._state()
        state["user"] = name
        self._json(200, state, extra=[self._cookie(sessions.create(name), LIFETIME)])

    def _redirect(self, location, extra=()):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()

    def _login_form(self):
        """The sign-in form is sent by the browser itself and answered with a
        redirect, which is what lets it offer to remember the password."""
        if not self._same_origin(require_json=False):
            return
        address = self.client_address[0]
        if limiter.retry_after(address):
            self._redirect("/?login=wait")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (not 0 < length <= 4096
                    or self.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded"):
                raise ValueError("Invalid request")
            fields = parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)
            name, password = fields["username"][0].strip(), fields["password"][0]
        except (ValueError, KeyError):
            self._redirect("/?login=failed")
            return
        if not admins.verify(name, password):
            limiter.failed(address)
            self._redirect("/?login=failed")
            return
        limiter.succeeded(address)
        self._redirect("/", extra=[self._cookie(sessions.create(name), LIFETIME)])

    def _logout(self):
        if not self._same_origin(require_json=False):
            return
        sessions.end(self._token())
        self._json(200, {"signed_out": True}, extra=[self._cookie("", 0)])

    def _write(self, method):
        path = unquote(urlsplit(self.path).path)
        if method == "POST" and path == "/api/login":
            self._login()
            return
        if method == "POST" and path == "/login":
            self._login_form()
            return
        if method == "POST" and path == "/api/logout":
            self._logout()
            return
        current_admin = self._guard(write=True, require_json=method != "DELETE")
        if not current_admin:
            return
        try:
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
                name = path.split("/")[3]
                admins.change_password(name, body["password"])
                # Whoever was signed in with the old password signs in again.
                sessions.end_user(name)
                if name == current_admin:
                    self._json(200, {"signed_out": True}, extra=[self._cookie("", 0)])
                    return
            elif method == "DELETE" and re.fullmatch(r"/api/admins/[a-z][a-z0-9_-]{0,31}", path):
                name = path.split("/")[3]
                if name == current_admin:
                    raise ValueError("No puedes eliminar la cuenta con la que has iniciado sesion")
                admins.delete(name)
                sessions.end_user(name)
            else:
                self._json(404, {"error": "Not found"})
                return
            state = self._state()
            state["user"] = current_admin
            self._json(200, state)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            self._json(500, {"error": str(exc)})

    def do_PUT(self):
        self._answered(self._write, "PUT")

    def do_POST(self):
        self._answered(self._write, "POST")

    def do_DELETE(self):
        self._answered(self._write, "DELETE")


class Server(ThreadingHTTPServer):
    daemon_threads = True


def main():
    server = Server(("0.0.0.0", 8080), Handler)

    def stop(_signal, _frame):
        # The services are ended first, in order; `shutdown` must not be
        # called from the thread that runs `serve_forever`.
        def end():
            runtime.shutdown()
            server.shutdown()
        threading.Thread(target=end, name="shutdown").start()

    # LXC asks a container to halt with SIGPWR unless told otherwise.
    for name in ("SIGTERM", "SIGINT", "SIGPWR"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    runtime.start_supervisor()
    print("ShareCoveX panel listening on 0.0.0.0:8080", flush=True)
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
