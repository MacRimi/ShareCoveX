import http.client
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

import sharecovex.auth as auth
import sharecovex.runtime

PASSWORD = "initial-password-123"


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.iterations = auth.ITERATIONS
        auth.ITERATIONS = 1_000
        cls.directory = tempfile.TemporaryDirectory()
        cls.runtime = Mock()
        cls.runtime.status.side_effect = lambda: {"settings": {"shares": []}, "users": []}
        environment = {"SHARECOVEX_ADMIN_PASSWORD": PASSWORD,
                       "SHARECOVEX_ADMINS_FILE": os.path.join(cls.directory.name, "admins.json")}
        sys.modules.pop("sharecovex.__main__", None)
        with (patch.dict("os.environ", environment),
              patch.object(sharecovex.runtime, "Runtime", return_value=cls.runtime)):
            cls.panel = importlib.import_module("sharecovex.__main__")
        cls.server = cls.panel.Server(("127.0.0.1", 0), cls.panel.Handler)
        cls.server.RequestHandlerClass.log_message = lambda *args: None
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.directory.cleanup()
        auth.ITERATIONS = cls.iterations
        sys.modules.pop("sharecovex.__main__", None)

    def setUp(self):
        self.panel.limiter.addresses.clear()
        self.panel.sessions.sessions.clear()
        self.panel.admins.change_password("admin", PASSWORD)

    def request(self, method, path, body=None, cookie=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        sent = dict(headers or {})
        if body is not None:
            sent.setdefault("Content-Type", "application/json")
        if cookie:
            sent["Cookie"] = cookie
        connection.request(method, path, json.dumps(body) if body is not None else None, sent)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        return response, value

    def sign_in(self, name="admin", password=PASSWORD, headers=None):
        response, value = self.request("POST", "/api/login", {"name": name, "password": password}, headers=headers)
        cookie = response.getheader("Set-Cookie")
        return response, value, cookie.split(";")[0] if cookie else None

    def test_the_page_opens_without_a_session_and_the_data_does_not(self):
        for path in ("/", "/app.js", "/style.css", "/locales/sv.json", "/healthz"):
            response, _ = self.request("GET", path)
            self.assertEqual(response.status, 200, path)
            self.assertIsNone(response.getheader("WWW-Authenticate"))
        for method, path in (("GET", "/api/state"), ("GET", "/api/tree?mount=media"), ("GET", "/anything"),
                             ("PUT", "/api/settings"), ("POST", "/api/users"), ("DELETE", "/api/users/alice")):
            response, value = self.request(method, path, {} if method in ("PUT", "POST") else None)
            self.assertEqual(response.status, 401, path)
            self.assertEqual(value, {"error": "Sesion no iniciada"})
            self.assertIsNone(response.getheader("WWW-Authenticate"))
        self.runtime.update_settings.assert_not_called()
        self.runtime.add_user.assert_not_called()

    def test_signing_in_gives_a_cookie_that_scripts_cannot_read(self):
        response, value, cookie = self.sign_in()
        self.assertEqual(response.status, 200)
        self.assertEqual(value["user"], "admin")
        flags = response.getheader("Set-Cookie")
        self.assertIn("HttpOnly", flags)
        self.assertIn("SameSite=Strict", flags)
        self.assertNotIn("Secure", flags)
        self.assertNotIn(PASSWORD, flags)
        response, value = self.request("GET", "/api/state", cookie=cookie)
        self.assertEqual((response.status, value["user"], value["admins"]), (200, "admin", ["admin"]))
        self.assertEqual(value["version"], "dev")
        response, _, _ = self.sign_in(headers={"X-Forwarded-Proto": "https"})
        self.assertIn("; Secure", response.getheader("Set-Cookie"))

    def test_a_wrong_name_and_a_wrong_password_look_the_same(self):
        first = self.sign_in(password="wrong-password-123")
        second = self.sign_in(name="nobody")
        self.assertEqual((first[0].status, first[1]), (401, {"error": "Usuario o contrasena incorrectos"}))
        self.assertEqual((second[0].status, second[1]), (first[0].status, first[1]))
        self.assertIsNone(first[2])

    def test_repeated_failures_make_the_address_wait(self):
        for _ in range(5):
            self.assertEqual(self.sign_in(password="wrong-password-123")[0].status, 401)
        response, value, cookie = self.sign_in()
        self.assertEqual(response.status, 429)
        self.assertEqual(response.getheader("Retry-After"), "60")
        self.assertEqual(value["retry_after"], 60)
        self.assertIn("Demasiados intentos", value["error"])
        self.assertIsNone(cookie)

    def test_signing_out_ends_the_session(self):
        _, _, cookie = self.sign_in()
        response, value = self.request("POST", "/api/logout", cookie=cookie)
        self.assertEqual((response.status, value), (200, {"signed_out": True}))
        self.assertIn("Max-Age=0", response.getheader("Set-Cookie"))
        self.assertEqual(self.request("GET", "/api/state", cookie=cookie)[0].status, 401)

    def test_a_new_password_signs_that_administrator_out_everywhere(self):
        _, _, here = self.sign_in()
        _, _, elsewhere = self.sign_in()
        response, value = self.request("POST", "/api/admins/admin/password",
                                       {"password": "replacement-password-123"}, cookie=here)
        self.assertEqual((response.status, value), (200, {"signed_out": True}))
        for cookie in (here, elsewhere):
            self.assertEqual(self.request("GET", "/api/state", cookie=cookie)[0].status, 401)
        self.assertEqual(self.sign_in()[0].status, 401)
        self.assertEqual(self.sign_in(password="replacement-password-123")[0].status, 200)

    def test_another_site_cannot_sign_in_or_change_anything(self):
        response, _, cookie = self.sign_in(headers={"Origin": "http://evil.example"})
        self.assertEqual(response.status, 403)
        self.assertIsNone(cookie)
        _, _, cookie = self.sign_in()
        response, _ = self.request("POST", "/api/users", {"name": "alice", "password": "eight888"},
                                   cookie=cookie, headers={"Origin": "http://evil.example"})
        self.assertEqual(response.status, 403)
        response, _ = self.request("POST", "/api/users", {"name": "alice", "password": "eight888"},
                                   cookie=cookie, headers={"Content-Type": "text/plain"})
        self.assertEqual(response.status, 415)
        self.runtime.add_user.assert_not_called()

    def form(self, fields, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        sent = {"Content-Type": "application/x-www-form-urlencoded",
                "Origin": f"http://127.0.0.1:{self.server.server_address[1]}"}
        sent.update(headers or {})
        connection.request("POST", "/login", urlencode(fields), sent)
        response = connection.getresponse()
        response.read()
        connection.close()
        cookie = response.getheader("Set-Cookie")
        return response.status, response.getheader("Location"), cookie.split(";")[0] if cookie else None

    def test_the_form_sent_by_the_browser_is_answered_with_a_redirect(self):
        status, location, cookie = self.form({"username": " admin ", "password": PASSWORD})
        self.assertEqual((status, location), (303, "/"))
        self.assertEqual(self.request("GET", "/api/state", cookie=cookie)[1]["user"], "admin")
        spaced = "pass word+with&odd=chars"
        self.panel.admins.change_password("admin", spaced)
        self.assertEqual(self.form({"username": "admin", "password": spaced})[:2], (303, "/"))
        for fields in ({"username": "admin", "password": "wrong-password-123"}, {"username": "nobody", "password": spaced},
                       {"username": "admin"}, {}):
            status, location, cookie = self.form(fields)
            self.assertEqual((status, location, cookie), (303, "/?login=failed", None))

    def test_the_form_is_refused_from_another_site_and_limited_like_the_rest(self):
        status, location, cookie = self.form({"username": "admin", "password": PASSWORD}, {"Origin": "http://evil.example"})
        self.assertEqual((status, cookie), (403, None))
        status, location, cookie = self.form({"username": "admin", "password": PASSWORD}, {"Origin": "null"})
        self.assertEqual((status, cookie), (403, None))
        for _ in range(4):
            self.form({"username": "admin", "password": "wrong-password-123"})
        self.assertEqual(self.form({"username": "admin", "password": "wrong-password-123"})[1], "/?login=failed")
        self.assertEqual(self.form({"username": "admin", "password": PASSWORD}), (303, "/?login=wait", None))
        response, _ = self.request("GET", "/?login=wait")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Referrer-Policy"), "same-origin")
        self.assertIn("form-action 'self'", response.getheader("Content-Security-Policy"))

    def test_only_the_icons_are_kept_by_the_browser(self):
        for path in ("/favicon.ico", "/sharecovex-icon.svg", "/sharecovex-icon.png"):
            response, body = self.request("GET", path)
            self.assertEqual((response.status, response.getheader("Cache-Control")), (200, "public, max-age=86400"), path)
        self.assertNotIn(b"<text", (self.panel.WEB / "sharecovex-icon.svg").read_bytes())
        for path in ("/", "/app.js", "/style.css", "/locales/en.json", "/healthz"):
            self.assertEqual(self.request("GET", path)[0].getheader("Cache-Control"), "no-store", path)

    def test_health_is_reported_without_a_session(self):
        self.runtime.healthy.return_value = True
        response, value = self.request("GET", "/healthz")
        self.assertEqual((response.status, value), (200, {"status": "ok"}))
        self.runtime.healthy.return_value = False
        response, value = self.request("GET", "/healthz")
        self.assertEqual((response.status, value), (503, {"status": "stopping"}))
        self.runtime.healthy.return_value = True

    def test_reading_the_state_does_not_restart_services(self):
        _, _, cookie = self.sign_in()
        self.runtime.sync.reset_mock()
        self.request("GET", "/api/state", cookie=cookie)
        self.runtime.sync.assert_not_called()


if __name__ == "__main__":
    unittest.main()
