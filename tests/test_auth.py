import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sharecovex.auth as auth


class AdminStoreTests(unittest.TestCase):
    def setUp(self):
        self.iterations = auth.ITERATIONS
        auth.ITERATIONS = 1_000

    def tearDown(self):
        auth.ITERATIONS = self.iterations

    def test_bootstrap_create_rotate_delete_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admins.json"
            store = auth.AdminStore(path, "initial-password-123")
            self.assertTrue(store.verify("admin", "initial-password-123"))
            self.assertFalse(store.verify("admin", "wrong-password-123"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("initial-password-123", path.read_text())

            store.add("pedro", "another-password-123")
            store.add("shortok", "eight888")
            self.assertTrue(store.verify("shortok", "eight888"))
            store.delete("shortok")
            store.change_password("admin", "replacement-password-123")
            self.assertFalse(store.verify("admin", "initial-password-123"))
            self.assertTrue(store.verify("admin", "replacement-password-123"))
            store.delete("pedro")
            with self.assertRaisesRegex(ValueError, "ultimo administrador"):
                store.delete("admin")

            loaded = auth.AdminStore(path)
            self.assertEqual(loaded.names(), ["admin"])
            self.assertTrue(loaded.verify("admin", "replacement-password-123"))

    def test_rejects_invalid_or_duplicate_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = auth.AdminStore(Path(directory) / "admins.json", "initial-password-123")
            with self.assertRaises(ValueError):
                store.add("Admin", "another-password-123")
            with self.assertRaisesRegex(ValueError, "entre 8 y 256"):
                store.add("pedro", "seven77")
            with self.assertRaisesRegex(ValueError, "ya existe"):
                store.add("admin", "another-password-123")

    def test_unknown_name_costs_the_same_as_a_wrong_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = auth.AdminStore(Path(directory) / "admins.json", "initial-password-123")
            calls = []
            original = auth.hashlib.pbkdf2_hmac

            def counted(*args, **kwargs):
                calls.append(args[0])
                return original(*args, **kwargs)

            with patch.object(auth.hashlib, "pbkdf2_hmac", counted):
                self.assertFalse(store.verify("admin", "wrong-password-123"))
                known = len(calls)
                self.assertFalse(store.verify("nobody", "wrong-password-123"))
            self.assertEqual(known, 1)
            self.assertEqual(len(calls), 2)

    def test_rejects_public_admin_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admins.json"
            auth.AdminStore(path, "initial-password-123")
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "grupo"):
                auth.AdminStore(path)


if __name__ == "__main__":
    unittest.main()
