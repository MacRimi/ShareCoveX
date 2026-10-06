import json
import tempfile
import unittest
from pathlib import Path

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
            store.add("shortok", "sixsix")
            self.assertTrue(store.verify("shortok", "sixsix"))
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
            with self.assertRaises(ValueError):
                store.add("pedro", "short")
            with self.assertRaisesRegex(ValueError, "ya existe"):
                store.add("admin", "another-password-123")

    def test_rejects_public_admin_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admins.json"
            auth.AdminStore(path, "initial-password-123")
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "grupo"):
                auth.AdminStore(path)


if __name__ == "__main__":
    unittest.main()
