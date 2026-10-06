import unittest
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

from sharecovex.runtime import Runtime, container_environment, mountpoints


class MountpointTests(unittest.TestCase):
    def test_unprivileged_lxc_is_detected_from_uid_mapping(self):
        def read_bytes(path):
            return b"container=lxc\0" if str(path) == "/proc/1/environ" else b""

        def read_text(path, **_kwargs):
            return "         0     100000      65536\n"

        with (patch.object(Path, "read_bytes", read_bytes),
              patch.object(Path, "read_text", read_text),
              patch.dict("os.environ", {}, clear=True)):
            self.assertEqual(container_environment(), {"type": "lxc", "unprivileged": True})

    def test_bind_mount_on_same_filesystem_is_detected(self):
        lines = [
            "46 22 8:1 /media /shares/media rw,relatime - ext4 /dev/sda1 rw\n",
            "47 22 8:1 /downloads /shares/my\\040downloads rw,relatime - ext4 /dev/sda1 rw\n",
        ]
        self.assertEqual(mountpoints(lines), {"/shares/media", "/shares/my downloads"})

    def test_tree_stays_within_mounted_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "media" / "movies").mkdir(parents=True)
            (root / "media" / "outside").symlink_to(root)
            runtime = object.__new__(Runtime)
            import threading
            runtime.lock = threading.RLock()
            with (patch("sharecovex.runtime.SHARES", root),
                  patch.object(Runtime, "mounted_folders", return_value=["media"]),
                  patch("sharecovex.runtime.open", mock_open(read_data=""), create=True)):
                self.assertEqual(runtime.tree("media")["children"], ["movies"])
                self.assertEqual(runtime.directory("media", "movies"), root / "media" / "movies")
                with self.assertRaises(ValueError):
                    runtime.directory("media", "outside")
                with self.assertRaises(ValueError):
                    runtime.directory("media", "../private")

    def test_tree_marks_leaf_folders_without_hiding_nested_ones(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "media" / "movies" / "season1").mkdir(parents=True)
            (root / "media" / "private").mkdir()
            runtime = object.__new__(Runtime)
            runtime.lock = threading.RLock()
            with (patch("sharecovex.runtime.SHARES", root),
                  patch.object(Runtime, "mounted_folders", return_value=["media"]),
                  patch("sharecovex.runtime.open", mock_open(read_data=""), create=True)):
                tree = runtime.tree("media")
                self.assertEqual(tree["children"], ["movies", "private"])
                self.assertEqual(tree["expandable"], {"movies": True, "private": False})

    def test_service_processes_do_not_share_panel_process_group(self):
        runtime = object.__new__(Runtime)
        runtime.processes = {}
        runtime.last_start = {}
        runtime.errors = {}
        with patch("sharecovex.runtime.subprocess.Popen", return_value=Mock()) as popen:
            runtime._start("smb", ["smbd"])
        popen.assert_called_once_with(["smbd"], start_new_session=True)

    def test_legacy_nfs_shares_do_not_start_unfs3_implicitly(self):
        import threading
        runtime = object.__new__(Runtime)
        runtime.lock = threading.RLock()
        runtime.settings = {"shares": [{"id": "data", "path": "", "nfs_enabled": True,
                                         "smb_enabled": False}], "nfs_backend": "legacy-v4",
                            "mdns_enabled": True}
        runtime.processes = {}
        runtime.errors = {}
        runtime.last_start = {}
        with (patch.object(Runtime, "mounted_folders", return_value=["data"]),
              patch.object(Runtime, "directory", return_value=Path("/shares/data")),
              patch.object(Runtime, "_start") as start):
            runtime.sync()
        start.assert_not_called()
        self.assertIn("explicit migration", runtime.errors["nfs"])

    def test_global_switch_stops_service_without_removing_shares(self):
        import threading
        runtime = object.__new__(Runtime)
        runtime.lock = threading.RLock()
        runtime.settings = {"shares": [{"id": "data", "path": "", "smb_enabled": True,
                                         "nfs_enabled": False}], "nfs_backend": "unfs3-v3",
                            "smb_service_enabled": False, "nfs_service_enabled": True,
                            "mdns_enabled": True}
        runtime.processes = {}
        runtime.errors = {}
        runtime.last_start = {}
        with (patch.object(Runtime, "mounted_folders", return_value=["data"]),
              patch.object(Runtime, "directory", return_value=Path("/shares/data")),
              patch.object(Runtime, "_start") as start,
              patch.object(Runtime, "_stop") as stop):
            runtime.sync()
        start.assert_not_called()
        stop.assert_any_call("smb")
        self.assertEqual(len(runtime.settings["shares"]), 1)

    def test_paused_resource_does_not_start_network_services(self):
        runtime = object.__new__(Runtime)
        runtime.lock = threading.RLock()
        runtime.settings = {"shares": [{"id": "data", "path": "", "enabled": False,
                                         "smb_enabled": True, "nfs_enabled": True}],
                            "nfs_backend": "unfs3-v3", "smb_service_enabled": True,
                            "nfs_service_enabled": True, "mdns_enabled": True}
        runtime.processes = {}
        runtime.errors = {}
        runtime.last_start = {}
        runtime.users = {}
        with (patch.object(Runtime, "mounted_folders", return_value=["data"]),
              patch.object(Runtime, "directory", return_value=Path("/shares/data")),
              patch.object(Runtime, "_start") as start,
              patch.object(Runtime, "_stop") as stop):
            runtime.sync()
        start.assert_not_called()
        self.assertEqual(stop.call_count, 4)
        self.assertEqual(len(runtime.settings["shares"]), 1)
        with (patch.object(Runtime, "mounted_folders", return_value=["data"]),
              patch("sharecovex.runtime.listening", return_value=False)):
            status = runtime.status()
        self.assertFalse(status["services"]["smb"]["configured"])
        self.assertFalse(status["services"]["nfs"]["configured"])
        self.assertFalse(status["services"]["mdns"]["configured"])

    def test_bonjour_starts_only_with_running_smb(self):
        runtime = object.__new__(Runtime)
        runtime.lock = threading.RLock()
        runtime.settings = {"shares": [{"id": "data", "path": "", "smb_enabled": True,
                                         "nfs_enabled": False}], "nfs_backend": "unfs3-v3",
                            "smb_service_enabled": True, "nfs_service_enabled": False,
                            "mdns_enabled": True}
        runtime.processes = {"smb": Mock(poll=Mock(return_value=None))}
        runtime.errors = {}
        runtime.last_start = {}
        with (patch.object(Runtime, "mounted_folders", return_value=["data"]),
              patch.object(Runtime, "directory", return_value=Path("/shares/data")),
              patch.object(Runtime, "_start") as start,
              patch.object(Runtime, "_stop")):
            runtime.sync()
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0], "mdns")
        self.assertEqual(start.call_args.args[1][0], "avahi-daemon")

    def test_smb_password_minimum_is_six_for_creation_and_rotation(self):
        runtime = object.__new__(Runtime)
        runtime.lock = threading.RLock()
        runtime.users = {}
        with (patch("sharecovex.runtime.command") as command,
              patch.object(Runtime, "_save_users")):
            with self.assertRaisesRegex(ValueError, "6-256"):
                runtime.add_user("alice", "short")
            runtime.add_user("alice", "sixsix")
            self.assertEqual(runtime.users["alice"], 2001)
            self.assertEqual(command.call_count, 2)
            with self.assertRaisesRegex(ValueError, "6-256"):
                runtime.password("alice", "short")
            runtime.password("alice", "newsix")
            self.assertEqual(command.call_count, 3)


if __name__ == "__main__":
    unittest.main()
