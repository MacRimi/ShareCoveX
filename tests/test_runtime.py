import signal
import subprocess
import unittest
import tempfile
import threading
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

from sharecovex.runtime import (Runtime, container_environment, default_interface, mount_sources, mountpoints,
                                vfs_handle_error, xattr_supported)


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

    def test_mount_sources_name_the_folder_and_its_filesystem(self):
        lines = [
            "46 22 8:1 /mnt/data/media /shares/media rw,relatime shared:3 master:1 - ext4 /dev/mapper/pve-root rw\n",
            "47 22 0:52 / /shares/films rw,relatime - zfs tank/films rw,xattr\n",
            "48 22 8:17 /my\\040photos /shares/photos rw,relatime - xfs /dev/sdb1 rw\n",
        ]
        self.assertEqual(mount_sources(lines), {
            "/shares/media": {"path": "/mnt/data/media", "fstype": "ext4", "device": "/dev/mapper/pve-root"},
            "/shares/films": {"path": "/", "fstype": "zfs", "device": "tank/films"},
            "/shares/photos": {"path": "/my photos", "fstype": "xfs", "device": "/dev/sdb1"},
        })

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

    def test_a_folder_the_container_cannot_enter_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "media" / "private").mkdir(parents=True)
            runtime = object.__new__(Runtime)
            runtime.lock = threading.RLock()
            denied = PermissionError(13, "Permission denied")
            with (patch("sharecovex.runtime.SHARES", root),
                  patch.object(Runtime, "mounted_folders", return_value=["media"]),
                  patch("sharecovex.runtime.open", mock_open(read_data=""), create=True)):
                with patch.object(Path, "iterdir", side_effect=denied):
                    with self.assertRaisesRegex(ValueError, "cannot be read by this container"):
                        runtime.tree("media")
                with patch.object(Path, "is_dir", side_effect=denied):
                    with self.assertRaisesRegex(ValueError, "cannot be read by this container"):
                        runtime.directory("media", "private")
        with (patch("sharecovex.runtime.shutil.which", return_value="/usr/bin/ganesha.nfsd"),
              patch("sharecovex.runtime.ctypes.CDLL") as library,
              patch("sharecovex.runtime.os.open", side_effect=denied)):
            library.return_value.name_to_handle_at.return_value = 0
            self.assertEqual(vfs_handle_error("/shares/media"), "/shares/media: the folder cannot be opened")

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
        self.assertEqual(start.call_args.args[1][:2], ["avahi-daemon", "--no-chroot"])

    def test_smb_password_minimum_is_eight_for_creation_and_rotation(self):
        runtime = object.__new__(Runtime)
        runtime.lock = threading.RLock()
        runtime.users = {}
        with (patch("sharecovex.runtime.command") as command,
              patch.object(Runtime, "_save_users")):
            with self.assertRaisesRegex(ValueError, "8-256"):
                runtime.add_user("alice", "seven77")
            runtime.add_user("alice", "eight888")
            self.assertEqual(runtime.users["alice"], 2001)
            self.assertEqual(command.call_count, 2)
            with self.assertRaisesRegex(ValueError, "8-256"):
                runtime.password("alice", "seven77")
            runtime.password("alice", "neweight")
            self.assertEqual(command.call_count, 3)

    def test_a_removed_user_does_not_lend_its_uid_to_the_next_one(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory)
            runtime = object.__new__(Runtime)
            runtime.lock = threading.RLock()
            runtime.users_file = config / "users.json"
            runtime.settings = {"shares": []}
            with (patch("sharecovex.runtime.CONFIG", config),
                  patch("sharecovex.runtime.command")):
                runtime.users = runtime._load_users()
                runtime.add_user("alice", "eight888")
                runtime.add_user("bob", "eight888")
                runtime.delete_user("bob")
                runtime.add_user("carol", "eight888")
                self.assertEqual(runtime.users, {"alice": 2001, "carol": 2003})
                self.assertEqual((config / "last-uid").read_text(), "2003\n")
                reloaded = object.__new__(Runtime)
                reloaded.users_file = runtime.users_file
                reloaded._load_users()
                self.assertEqual(reloaded.last_uid, 2003)

    def test_only_mounts_that_can_name_a_resource_are_offered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("media", "Movies", "my files", "config"):
                (root / name).mkdir()
            (root / "plain").mkdir()
            lines = [f"46 22 8:1 / {root}/{name} rw - ext4 /dev/sda1 rw\n".replace("my files", "my\\040files")
                     for name in ("media", "Movies", "my files", "config")]
            runtime = object.__new__(Runtime)
            with (patch("sharecovex.runtime.SHARES", root),
                  patch("sharecovex.runtime.open", mock_open(read_data="".join(lines)), create=True)):
                self.assertEqual(runtime.mounted_folders(), ["media"])
                self.assertEqual(runtime.ignored_mounts(), ["Movies", "my files"])


class EnvironmentTests(unittest.TestCase):
    def test_the_interface_of_the_default_route_is_found(self):
        with tempfile.TemporaryDirectory() as directory:
            routes = Path(directory) / "route"
            header = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
            routes.write_text(header + "ens18\t0000A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
                              "ens18\t00000000\t0100A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0\n")
            self.assertEqual(default_interface(str(routes)), "ens18")
            routes.write_text(header + "eth0\t0000A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n")
            self.assertIsNone(default_interface(str(routes)))
            self.assertIsNone(default_interface(str(routes) + ".missing"))

    def test_extended_attributes_are_probed_without_writing(self):
        import errno
        missing = OSError(errno.ENODATA, "No data available")
        unsupported = OSError(errno.ENOTSUP, "Operation not supported")
        with patch("sharecovex.runtime.os.getxattr", side_effect=missing, create=True):
            self.assertTrue(xattr_supported("/shares/media"))
        with patch("sharecovex.runtime.os.getxattr", side_effect=unsupported, create=True):
            self.assertFalse(xattr_supported("/shares/media"))


def service(code=None):
    return Mock(poll=Mock(return_value=code), returncode=code)


class LifecycleTests(unittest.TestCase):
    def test_nfs_registers_with_rpcbind_and_keeps_discovery_running(self):
        runtime = self.runtime()
        runtime.settings["shares"][0]["nfs_enabled"] = True
        runtime.processes["smb"] = service()
        def start_service(name, args):
            runtime.processes[name] = service()
        with (patch.object(Runtime, "mounted_folders", return_value=["data"]),
              patch.object(Runtime, "directory", return_value=Path("/shares/data")),
              patch.object(Runtime, "_start", side_effect=start_service) as start,
              patch("sharecovex.runtime.time.sleep")):
            runtime.sync()
            self.assertEqual([c.args[0] for c in start.call_args_list], ["rpcbind", "nfs"])
            self.assertNotIn("-p", start.call_args_list[-1].args[1])
            runtime.sync()
            self.assertEqual(start.call_count, 2)
            runtime.processes["rpcbind"] = service(code=1)
            runtime.sync()
            self.assertEqual([c.args[0] for c in start.call_args_list[-2:]], ["rpcbind", "nfs"])

    def runtime(self):
        runtime = object.__new__(Runtime)
        runtime.lock = threading.RLock()
        runtime.stopping = threading.Event()
        runtime.processes = {}
        runtime.errors = {}
        runtime.last_start = {}
        runtime.users = {}
        runtime.settings = {"shares": [{"id": "data", "path": "", "smb_enabled": True, "nfs_enabled": False}],
                            "nfs_backend": "unfs3-v3", "smb_service_enabled": True,
                            "nfs_service_enabled": True, "mdns_enabled": False}
        return runtime

    def test_a_service_that_ended_is_started_again_without_the_panel(self):
        runtime = self.runtime()
        runtime.processes["smb"] = service(code=1)
        runtime.last_start["smb"] = -1000.0
        with (patch.object(Runtime, "mounted_folders", return_value=["data"]),
              patch.object(Runtime, "directory", return_value=Path("/shares/data")),
              patch.object(Runtime, "_start") as start):
            runtime.sync()
        self.assertEqual(start.call_args.args[0], "smb")
        self.assertEqual(runtime.errors["smb"], "Exited with code 1")
        # Samba writes to the container log, where a rejected sign-in can be read.
        self.assertIn("--debug-stdout", start.call_args.args[1])

    def test_a_service_that_keeps_ending_is_not_started_in_a_loop(self):
        runtime = self.runtime()
        runtime.processes["smb"] = service(code=1)
        with (patch.object(Runtime, "mounted_folders", return_value=["data"]),
              patch.object(Runtime, "directory", return_value=Path("/shares/data")),
              patch("sharecovex.runtime.time.monotonic", return_value=100.0),
              patch.object(Runtime, "_start") as start):
            runtime.last_start["smb"] = 90.0
            runtime.sync()
            start.assert_not_called()
            runtime.last_start["smb"] = 60.0
            runtime.sync()
            start.assert_called_once()

    def test_the_supervisor_looks_until_the_container_stops(self):
        runtime = self.runtime()
        looked = threading.Event()
        with patch.object(Runtime, "sync", side_effect=lambda: looked.set()) as sync:
            runtime.start_supervisor(interval=0.01)
            self.assertTrue(looked.wait(2))
            runtime.stopping.set()
            runtime.supervisor.join(2)
            self.assertFalse(runtime.supervisor.is_alive())
            calls = sync.call_count
        self.assertGreaterEqual(calls, 1)

    def test_health_follows_the_supervisor(self):
        runtime = self.runtime()
        runtime.supervisor = None
        self.assertFalse(runtime.healthy())
        with patch.object(Runtime, "sync"):
            runtime.start_supervisor(interval=0.01)
            self.assertTrue(runtime.healthy())
            runtime.shutdown(timeout=0.1)
        self.assertFalse(runtime.healthy())

    def test_a_failed_look_does_not_end_the_supervisor(self):
        runtime = self.runtime()
        looks = []

        def sync():
            looks.append(1)
            if len(looks) == 1:
                raise RuntimeError("mountinfo unreadable")
            runtime.stopping.set()

        with (patch.object(Runtime, "sync", side_effect=sync), patch("builtins.print")):
            runtime.start_supervisor(interval=0.01)
            runtime.supervisor.join(2)
        self.assertEqual(len(looks), 2)

    def test_stopping_ends_every_service_and_starts_none_again(self):
        runtime = self.runtime()
        smb, nfs, mdns = service(), service(), service()
        stubborn = service()
        stubborn.wait.side_effect = [subprocess.TimeoutExpired("rpcbind", 0.1), None]
        runtime.processes = {"smb": smb, "nfs": nfs, "mdns": mdns, "rpcbind": stubborn}
        runtime.shutdown(timeout=0.2)
        for process in (smb, nfs, mdns, stubborn):
            process.terminate.assert_called_once()
        smb.kill.assert_not_called()
        stubborn.kill.assert_called_once()
        self.assertEqual(runtime.processes, {})
        with (patch.object(Runtime, "mounted_folders", return_value=["data"]),
              patch.object(Runtime, "_start") as start):
            runtime.sync()
        start.assert_not_called()


CONF = "[global]\n  workgroup = X\n[Media]\n  path = /shares/media\n  read only = {media}\n[Docs]\n  path = /shares/docs\n"


class ReloadTests(unittest.TestCase):
    def runtime(self):
        runtime = object.__new__(Runtime)
        runtime.lock = threading.RLock()
        runtime.errors = {}
        runtime.last_start = {}
        runtime.processes = {"smb": service(), "nfs": service(), "mdns": service()}
        return runtime

    def rendered(self, **changes):
        value = {"smb": CONF.format(media="no"), "nfs": ("unfs3-v3", "/shares/media 10.0.0.0/24(rw)\n"),
                 "avahi": "[server]\nhost-name=cove\n", "service": "<service-group/>"}
        value.update(changes)
        return value

    def test_nothing_is_touched_when_nothing_changed(self):
        runtime = self.runtime()
        with (patch("sharecovex.runtime.command") as command, patch.object(Runtime, "_stop") as stop):
            runtime._apply_changes(self.rendered(), self.rendered())
        command.assert_not_called()
        stop.assert_not_called()
        for process in runtime.processes.values():
            process.send_signal.assert_not_called()

    def test_samba_reloads_and_only_the_changed_share_reconnects(self):
        runtime = self.runtime()
        after = CONF.format(media="yes").replace("[Docs]\n  path = /shares/docs\n", "[Docs]\n  path = /shares/docs\n[New]\n  path = /shares/new\n")
        with (patch("sharecovex.runtime.CONFIG", Path("/config")),
              patch("sharecovex.runtime.command") as command, patch.object(Runtime, "_stop") as stop):
            runtime._apply_changes(self.rendered(), self.rendered(smb=after))
        self.assertEqual([call.args[0] for call in command.call_args_list],
                         [["smbcontrol", "--configfile=/config/smb.conf", "all", "reload-config"],
                          ["smbcontrol", "--configfile=/config/smb.conf", "all", "close-share", "Media"]])
        stop.assert_not_called()
        runtime.processes["nfs"].send_signal.assert_not_called()

    def test_a_removed_share_is_closed_for_its_clients(self):
        runtime = self.runtime()
        after = CONF.format(media="no").replace("[Docs]\n  path = /shares/docs\n", "")
        with (patch("sharecovex.runtime.CONFIG", Path("/config")),
              patch("sharecovex.runtime.command") as command, patch.object(Runtime, "_stop")):
            runtime._apply_changes(self.rendered(), self.rendered(smb=after))
        self.assertEqual(command.call_args_list[-1].args[0][-2:], ["close-share", "Docs"])

    def test_samba_restarts_when_it_cannot_be_told_or_the_identity_changed(self):
        runtime = self.runtime()
        after = self.rendered(smb=CONF.format(media="yes"))
        with (patch("sharecovex.runtime.command", side_effect=RuntimeError("smbcontrol failed")),
              patch.object(Runtime, "_stop") as stop):
            runtime._apply_changes(self.rendered(), after)
        stop.assert_called_once_with("smb")
        with (patch("sharecovex.runtime.command") as command, patch.object(Runtime, "_stop") as stop):
            runtime._apply_changes(self.rendered(), after, identity_changed=True)
        command.assert_not_called()
        stop.assert_called_once_with("smb")

    def test_the_nfs_server_restarts_only_when_an_export_changed(self):
        runtime = self.runtime()
        nfs = runtime.processes["nfs"]
        with (patch("sharecovex.runtime.command") as command, patch.object(Runtime, "_stop") as stop):
            runtime._apply_changes(self.rendered(), self.rendered(smb=CONF.format(media="yes")))
            stop.assert_not_called()
            runtime._apply_changes(self.rendered(), self.rendered(nfs=("unfs3-v3", "/shares/docs 10.0.0.0/24(ro)\n")))
        self.assertEqual([call.args[0] for call in stop.call_args_list], ["nfs", "rpcbind"])
        nfs.send_signal.assert_not_called()
        self.assertEqual(command.call_count, 2)

    def test_avahi_reads_its_services_again_and_restarts_for_a_new_name(self):
        runtime = self.runtime()
        mdns = runtime.processes["mdns"]
        with patch.object(Runtime, "_stop") as stop:
            runtime._apply_changes(self.rendered(), self.rendered(service="<service-group><adisk/></service-group>"))
            mdns.send_signal.assert_called_once_with(signal.SIGHUP)
            stop.assert_not_called()
            runtime._apply_changes(self.rendered(), self.rendered(avahi="[server]\nhost-name=nas\n"))
        stop.assert_called_once_with("mdns")

    def test_a_stale_avahi_pid_file_is_removed_before_starting(self):
        runtime = self.runtime()
        with (patch("sharecovex.runtime.os.unlink") as unlink,
              patch("sharecovex.runtime.subprocess.Popen", return_value=Mock())):
            runtime._start("mdns", ["avahi-daemon"])
            runtime._start("smb", ["smbd"])
        unlink.assert_called_once_with("/run/avahi-daemon/pid")

if __name__ == "__main__":
    unittest.main()
