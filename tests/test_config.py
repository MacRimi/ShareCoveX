import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sharecovex.config import DEFAULTS, admin_password, avahi_conf, avahi_service, ganesha_conf, load, nfs_export_id, samba_conf, samba_sections, save, unfs3_exports, validate_settings, validate_user


def share(id, name, smb=True, nfs=False, read_only=False, clients=None):
    return {"id": id, "name": name, "smb_enabled": smb, "nfs_enabled": nfs,
            "read_only": read_only, "nfs_clients": clients if clients is not None else []}


class ConfigTests(unittest.TestCase):
    def test_admin_password_file_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "admin.password"
            path.write_text("a-long-secret-password\n", encoding="utf-8")
            path.chmod(0o600)
            with patch.dict("os.environ", {"SHARECOVEX_ADMIN_PASSWORD_FILE": str(path)}, clear=True):
                self.assertEqual(admin_password(), "a-long-secret-password")
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    admin_password()

    def test_defaults_export_nothing(self):
        self.assertEqual(DEFAULTS, {"shares": [], "smb_min_protocol": "SMB2_10",
                                    "smb_max_protocol": "SMB3_11", "nfs_backend": "unfs3-v3",
                                    "nfs_protocols": ["3"],
                                    "smb_service_enabled": True, "nfs_service_enabled": True,
                                    "server_name": "ShareCoveX", "mdns_enabled": True,
                                    "collaborative_mode": True, "shared_uid": 1000,
                                    "shared_gid": 1000, "time_machine_max_size_gb": 0})
        self.assertNotIn("[Shared]", samba_conf(DEFAULTS))
        self.assertIn("  log level = 1 auth_audit:2\n", samba_conf(DEFAULTS))
        self.assertNotIn("log file", samba_conf(DEFAULTS))
        self.assertNotIn("/shares/", unfs3_exports(DEFAULTS))

    def test_nfs_protocol_selection_chooses_the_matching_engine(self):
        v3 = validate_settings({"shares": [], "nfs_protocols": ["3"]})
        self.assertEqual(v3["nfs_backend"], "unfs3-v3")
        v4 = validate_settings({"shares": [], "nfs_protocols": ["4"]})
        self.assertEqual(v4["nfs_backend"], "ganesha-vfs")
        both = validate_settings({"shares": [], "nfs_protocols": ["4", "3"]})
        self.assertEqual(both["nfs_protocols"], ["3", "4"])
        self.assertEqual(both["nfs_backend"], "ganesha-vfs")
        for invalid in ([], ["2"], ["3", "3"], "3"):
            with self.assertRaisesRegex(ValueError, "NFSv3"):
                validate_settings({"shares": [], "nfs_protocols": invalid})

    def test_ganesha_exports_v4_and_dual_protocol_resources(self):
        item = share("data", "Files", smb=False, nfs=True, clients=["192.168.0.0/24"])
        item.update(nfs_mapping="mapall", nfs_uid=2002, nfs_gid=2000)
        value = validate_settings({"shares": [item], "nfs_protocols": ["3", "4"]})
        config = ganesha_conf(value)
        self.assertIn("Protocols = 3,4;", config)
        self.assertIn("Enable_UDP = false;", config)
        self.assertIn("Active_krb5 = false;", config)
        self.assertIn("Path = \"/shares/data\";", config)
        self.assertIn("Pseudo = \"/shares/data\";", config)
        self.assertIn("Squash = All_Squash;", config)
        self.assertIn("Anonymous_Uid = 1000;", config)
        self.assertIn("Clients = 192.168.0.0/24;", config)
        self.assertIn("Name = VFS;", config)

    def test_server_name_configures_samba_and_bonjour(self):
        value = validate_settings({"shares": [], "server_name": "Cove NAS", "mdns_enabled": True})
        self.assertIn("server string = Cove NAS", samba_conf(value))
        self.assertIn("fruit:model = Xserve", samba_conf(value))
        self.assertIn("host-name=cove-nas", avahi_conf(value))
        self.assertIn("<name>Cove NAS</name>", avahi_service(value))
        self.assertIn("<type>_smb._tcp</type>", avahi_service(value))
        self.assertIn("model=Xserve", avahi_service(value))
        value["shares"] = [{"enabled": True, "smb_enabled": False, "time_machine": True}]
        self.assertIn("model=TimeCapsule8,119", avahi_service(value))
        self.assertIn("fruit:model = TimeCapsule8,119", samba_conf(value))
        value["shares"][0]["enabled"] = False
        self.assertIn("model=Xserve", avahi_service(value))
        self.assertIn("fruit:model = Xserve", samba_conf(value))
        for bad in ("", "bad\nname", " bad", "bad/host", "<script>", "x" * 49):
            with self.assertRaises(ValueError):
                validate_settings({"shares": [], "server_name": bad})
        with self.assertRaises(ValueError):
            validate_settings({"shares": [], "mdns_enabled": "yes"})

    def test_multiple_independent_shares(self):
        value = {"shares": [share("media", "Media", smb=True, nfs=True, clients=["192.168.1.7/24"]),
                            share("backups", "Backups", smb=False, nfs=True, read_only=True,
                                  clients=["10.0.0.0/8"])]}
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "settings.json")
            save(path, value)
            loaded = load(path)
        self.assertEqual(loaded["shares"][0]["nfs_clients"], ["192.168.1.0/24"])
        self.assertFalse(loaded["shares"][0]["nfs_read_only"])
        self.assertIn("[Media]", samba_conf(loaded))
        self.assertNotIn("[Backups]", samba_conf(loaded))
        self.assertIn("/shares/media 192.168.1.0/24(rw,all_squash,anonuid=1000,anongid=1000,secure)", unfs3_exports(loaded))
        self.assertIn("/shares/backups 10.0.0.0/8(ro,all_squash,anonuid=1000,anongid=1000,secure)", unfs3_exports(loaded))

    def test_global_service_switches_preserve_shares(self):
        item = share("media", "Media", nfs=True, clients=["192.168.1.0/24"])
        settings = validate_settings({"shares": [item], "smb_service_enabled": False,
                                      "nfs_service_enabled": False})
        self.assertEqual(len(settings["shares"]), 1)
        self.assertFalse(settings["smb_service_enabled"])
        self.assertFalse(settings["nfs_service_enabled"])
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "settings.json")
            save(path, settings)
            restored = load(path)
        self.assertFalse(restored["smb_service_enabled"])
        self.assertFalse(restored["nfs_service_enabled"])
        self.assertEqual(len(restored["shares"]), 1)
        for bad in (0, "false", None):
            with self.assertRaises(ValueError):
                validate_settings({"shares": [item], "smb_service_enabled": bad})

    def test_paused_resource_keeps_settings_without_exporting(self):
        item = share("media", "Media", nfs=True, clients=["192.168.1.0/24"])
        item.update({"enabled": False, "smb_users": ["alice"], "nfs_read_only": True})
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "settings.json")
            save(path, {"shares": [item]})
            settings = load(path)
        self.assertFalse(settings["shares"][0]["enabled"])
        self.assertEqual(settings["shares"][0]["smb_users"], ["alice"])
        self.assertNotIn("[Media]", samba_conf(settings))
        self.assertNotIn("/shares/media", unfs3_exports(settings))
        settings["shares"][0]["enabled"] = True
        self.assertIn("[Media]", samba_conf(settings))
        self.assertIn("/shares/media", unfs3_exports(settings))

    def test_inactive_resource_can_have_no_protocol(self):
        item = share("media", "Media", smb=False)
        item["enabled"] = False
        self.assertFalse(validate_settings({"shares": [item]})["shares"][0]["enabled"])
        with self.assertRaisesRegex(ValueError, "active resource"):
            validate_settings({"shares": [{**item, "enabled": True}]})
        with self.assertRaisesRegex(ValueError, "enabled must be"):
            validate_settings({"shares": [{**item, "enabled": "false"}]})
        legacy = validate_settings({"shares": [share("other", "Other")]})
        self.assertTrue(legacy["shares"][0]["enabled"])

    def test_paused_draft_can_wait_for_protocol_credentials(self):
        smb = share("backups", "Backups")
        smb.update({"enabled": False, "smb_users": []})
        self.assertNotIn("[Backups]", samba_conf(validate_settings({"shares": [smb]})))
        with self.assertRaisesRegex(ValueError, "SMB user"):
            validate_settings({"shares": [{**smb, "enabled": True}]})
        nfs = share("data", "Data", smb=False, nfs=True)
        nfs.update({"enabled": False, "nfs_mapping": "mapall", "nfs_uid": None, "nfs_gid": None})
        self.assertNotIn("/shares/data", unfs3_exports(validate_settings({"shares": [nfs]})))
        with self.assertRaisesRegex(ValueError, "Maproot/Mapall"):
            validate_settings({"shares": [{**nfs, "enabled": True}]})

    def test_rejects_wide_open_nfs(self):
        with self.assertRaises(ValueError):
            validate_settings({"shares": [share("media", "Media", nfs=True)]})
        with self.assertRaisesRegex(ValueError, "IPv4"):
            validate_settings({"shares": [share("media", "Media", nfs=True, clients=["2001:db8::/64"])]})

    def test_rejects_duplicate_names(self):
        with self.assertRaises(ValueError):
            validate_settings({"shares": [share("one", "Media"), share("two", "media")]})

    def test_rejects_injected_values(self):
        for name in ["bad name", "name\n[global]", "../share"]:
            with self.assertRaises(ValueError):
                validate_settings({"shares": [share("good", name)]})
        for id in ["../data", "Name", "foo;bar"]:
            with self.assertRaises(ValueError):
                validate_settings({"shares": [share(id, "Good")]})
        for name in ["root", "ROOT", "john;reboot", "user/name"]:
            with self.assertRaises(ValueError):
                validate_user(name)
        self.assertEqual(validate_user("Pedro"), "Pedro")

    def test_subfolders_and_independent_smb_users(self):
        first = share("media", "Movies")
        first.update(path="movies", smb_users=["alice"])
        second = share("media", "Shows", nfs=True, clients=["10.0.0.0/24"])
        second.update(path="shows", smb_users=["bob"])
        value = validate_settings({"shares": [first, second]})
        samba = samba_conf(value)
        self.assertIn("path = /shares/media/movies", samba)
        self.assertIn("valid users = alice", samba)
        self.assertIn("valid users = bob", samba)
        self.assertIn("/shares/media/shows 10.0.0.0/24(rw,all_squash,anonuid=1000,anongid=1000,secure)", unfs3_exports(value))
        self.assertEqual(value["shares"][1]["path"], "shows")

    def test_sibling_folders_can_choose_different_protocols(self):
        movies = share("media", "Movies", smb=True, nfs=False)
        movies.update(path="movies", smb_users=["alice"])
        backups = share("media", "Backups", smb=False, nfs=True,
                        clients=["192.168.1.0/24"])
        backups.update(path="backups")
        shared = share("media", "Shared", smb=True, nfs=True,
                       clients=["192.168.2.0/24"])
        shared.update(path="shared", smb_users=["bob"])
        value = validate_settings({"shares": [movies, backups, shared]})
        smb = samba_conf(value)
        nfs = unfs3_exports(value)
        self.assertIn("[Movies]", smb)
        self.assertNotIn("[Backups]", smb)
        self.assertIn("[Shared]", smb)
        self.assertNotIn("/shares/media/movies ", nfs)
        self.assertIn("/shares/media/backups ", nfs)
        self.assertIn("/shares/media/shared ", nfs)

    def test_rejects_parent_child_and_path_injection(self):
        parent = share("media", "Media")
        child = share("media", "Private")
        child["path"] = "private"
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            validate_settings({"shares": [parent, child]})
        for bad in ("../private", "movies/../private", "/etc", "foo;bar", "foo\nbar", "linked/{x}"):
            child["path"] = bad
            with self.assertRaises(ValueError):
                validate_settings({"shares": [child]})

    def test_time_machine_requires_explicit_writable_smb_share(self):
        tm = share("backups", "MacBackup")
        tm.update(smb_users=["alice"], time_machine=True)
        self.assertIn("fruit:time machine = yes", samba_conf(validate_settings({"shares": [tm]})))
        for change in ({"smb_users": None}, {"read_only": True}, {"smb_enabled": False}):
            bad = {**tm, **change}
            with self.assertRaises(ValueError):
                validate_settings({"shares": [bad]})

    def test_time_machine_limit_and_collaborative_permissions(self):
        tm = share("backups", "MacBackup")
        tm.update(smb_users=["alice"], time_machine=True)
        value = validate_settings({"shares": [tm], "time_machine_max_size_gb": 750,
                                   "shared_uid": 1000, "shared_gid": 1000})
        config = samba_conf(value)
        self.assertIn("fruit:time machine max size = 750G", config)
        self.assertIn("force create mode = 0660", config)
        self.assertIn("force directory mode = 2770", config)
        self.assertIn("force user = sharecovex-files", config)
        for bad in (-1, 1048577, "500"):
            with self.assertRaises(ValueError):
                validate_settings({"shares": [], "time_machine_max_size_gb": bad})

    def test_each_time_machine_destination_can_have_its_own_limit(self):
        first = share("backups", "MacBackup")
        first.update(path="pedro", smb_users=["pedro"], time_machine=True, time_machine_max_size_gb=300)
        second = share("backups", "MacAna")
        second.update(path="ana", smb_users=["ana"], time_machine=True)
        ordinary = share("media", "Media")
        ordinary["time_machine_max_size_gb"] = 50
        value = validate_settings({"shares": [first, second, ordinary], "time_machine_max_size_gb": 750})
        sections = samba_sections(samba_conf(value))
        self.assertIn("  fruit:time machine max size = 300G", sections["MacBackup"])
        self.assertIn("  fruit:time machine max size = 750G", sections["MacAna"])
        self.assertFalse(any("time machine" in line for line in sections["Media"]))
        self.assertEqual(value["shares"][2]["time_machine_max_size_gb"], 0)
        for bad in (-1, 1048577, "300", True):
            with self.assertRaisesRegex(ValueError, "Time Machine"):
                validate_settings({"shares": [{**first, "time_machine_max_size_gb": bad}]})

    def test_time_machine_destinations_are_announced_as_disks(self):
        ordinary = share("media", "Media")
        self.assertNotIn("_adisk._tcp", avahi_service(validate_settings({"shares": [ordinary]})))
        first = share("backups", "MacBackup")
        first.update(path="pedro", smb_users=["pedro"], time_machine=True)
        second = share("backups", "MacAna")
        second.update(path="ana", smb_users=["ana"], time_machine=True)
        paused = share("backups", "MacOld")
        paused.update(path="old", smb_users=["ana"], time_machine=True, enabled=False)
        service = avahi_service(validate_settings({"shares": [ordinary, first, second, paused]}))
        self.assertIn("<service><type>_adisk._tcp</type><port>9</port>"
                      "<txt-record>sys=waMa=0,adVF=0x100</txt-record>"
                      "<txt-record>dk0=adVN=MacBackup,adVF=0x82</txt-record>"
                      "<txt-record>dk1=adVN=MacAna,adVF=0x82</txt-record></service>", service)
        self.assertNotIn("MacOld", service)
        self.assertNotIn("adVN=Media", service)

    def test_discovery_follows_the_interface_with_the_default_route(self):
        value = validate_settings({"shares": [], "server_name": "Cove NAS"})
        self.assertIn("allow-interfaces=eth0\n", avahi_conf(value))
        self.assertIn("allow-interfaces=ens18\n", avahi_conf(value, "ens18"))
        self.assertNotIn("allow-interfaces", avahi_conf(value, None))

    def test_samba_does_not_publish_a_path_it_would_rewrite(self):
        item = share("media", "Sales")
        item["path"] = "reports/100%Units"
        with self.assertRaisesRegex(ValueError, "caracter %"):
            validate_settings({"shares": [dict(item)]})
        paused = validate_settings({"shares": [{**item, "enabled": False}]})
        self.assertNotIn("[Sales]", samba_conf(paused))
        nfs_only = share("media", "Sales", smb=False, nfs=True, clients=["10.0.0.0/24"])
        nfs_only["path"] = "reports/100%Units"
        self.assertIn("100%Units", unfs3_exports(validate_settings({"shares": [nfs_only]})))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"shares": [item, share("docs", "Docs")]}), encoding="utf-8")
            loaded = load(str(path))
        self.assertEqual([entry["enabled"] for entry in loaded["shares"]], [False, True])
        self.assertIn("[Docs]", samba_conf(loaded))
        self.assertNotIn("[Sales]", samba_conf(loaded))

    def test_share_sections_are_read_back_from_the_generated_file(self):
        first = share("media", "Media")
        second = share("docs", "Docs", read_only=True)
        sections = samba_sections(samba_conf(validate_settings({"shares": [first, second]})))
        self.assertEqual(sorted(sections), ["Docs", "Media"])
        self.assertIn("  path = /shares/docs", sections["Docs"])
        self.assertNotEqual(sections["Docs"], sections["Media"])

    def test_collaborative_identity_is_configurable(self):
        item = share("data", "Files", smb=False, nfs=True, clients=["10.0.0.0/24"])
        value = validate_settings({"shares": [item], "shared_uid": 1500, "shared_gid": 1600})
        self.assertIn("all_squash,anonuid=1500,anongid=1600", unfs3_exports(value))
        for key in ("shared_uid", "shared_gid"):
            with self.assertRaises(ValueError):
                validate_settings({"shares": [], key: 0})

    def test_legacy_share_keeps_global_smb_access(self):
        legacy = validate_settings({"shares": [share("media", "Media")]})
        self.assertIsNone(legacy["shares"][0]["smb_users"])
        self.assertIn("valid users = @sharecovex", samba_conf(legacy))

    def test_guest_smb_is_explicit_and_does_not_open_private_shares(self):
        guest = share("media", "Public", read_only=True)
        guest.update(smb_guest=True, smb_users=[])
        private = share("backups", "Private")
        private.update(smb_users=["alice"])
        value = validate_settings({"shares": [guest, private]})
        config = samba_conf(value)
        self.assertIn("map to guest = Bad User", config)
        self.assertIn("guest account = nobody", config)
        self.assertIn("[Public]\n  path = /shares/media\n  browseable = yes\n  guest ok = yes", config)
        self.assertIn("guest only = yes", config)
        self.assertIn("[Public]", config)
        self.assertIn("  read only = yes\n  force user = sharecovex-files\n  force group = sharecovex\n  guest only = yes", config)
        self.assertIn("[Private]\n  path = /shares/backups\n  browseable = yes\n  guest ok = no", config)
        self.assertIn("valid users = alice", config)
        self.assertNotIn("guest only = yes", config.split("[Private]", 1)[1])

    def test_guest_cannot_bypass_users_or_time_machine(self):
        item = share("media", "Public", read_only=True)
        for change in ({"smb_guest": True, "time_machine": True},
                       {"smb_guest": "yes"},
                       {"smb_guest": True, "smb_enabled": False},
                       {"smb_guest": True, "read_only": False},
                       {"smb_guest": True, "smb_users": None}):
            with self.assertRaises(ValueError):
                validate_settings({"shares": [{**item, **change}]})

    def test_guest_reads_while_named_user_can_write_on_same_share(self):
        item = share("backups", "backups")
        item.update(smb_guest=True, smb_users=["alice"])
        value = validate_settings({"shares": [item]})
        config = samba_conf(value)
        self.assertIn("  read only = yes\n  force user = sharecovex-files\n  force group = sharecovex\n"
                      "  valid users = nobody alice\n  write list = alice", config)
        self.assertNotIn("guest only = yes", config)
        item["read_only"] = True
        self.assertNotIn("write list", samba_conf(validate_settings({"shares": [item]})))

    def test_existing_writable_guest_becomes_read_only_on_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            item = share("backups", "backups")
            item.update(smb_guest=True, smb_users=[])
            path.write_text(json.dumps({"shares": [item]}), encoding="utf-8")
            restored = load(str(path))
        self.assertTrue(restored["shares"][0]["read_only"])
        self.assertIn("  force group = sharecovex", samba_conf(restored))

    def test_saved_legacy_settings_migrate_without_changing_access(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"shares":[{"id":"media","name":"Media",'
                            '"smb_enabled":true,"nfs_enabled":false,"read_only":false,'
                            '"nfs_clients":[]}]}', encoding="utf-8")
            migrated = load(str(path))
            self.assertEqual(migrated["shares"][0]["path"], "")
            self.assertIsNone(migrated["shares"][0]["smb_users"])
            save(str(path), migrated)
            self.assertIn("valid users = @sharecovex", samba_conf(load(str(path))))

    def test_nfs_export_ids_do_not_depend_on_share_order(self):
        first = share("data", "Photos", smb=False, nfs=True, clients=["192.168.1.0/24"])
        first["path"] = "photos"
        second = share("data", "Videos", smb=False, nfs=True, clients=["192.168.1.0/24"])
        second["path"] = "videos"
        before = validate_settings({"shares": [first, second]})
        after = validate_settings({"shares": [second, first]})
        self.assertEqual(nfs_export_id(before["shares"][0]), nfs_export_id(after["shares"][1]))

    def test_legacy_nfs_ids_survive_migration_and_reordering(self):
        first = share("media", "Media", nfs=True, clients=["10.0.0.0/24"])
        second = share("backups", "Backups", nfs=True, clients=["10.0.0.0/24"])
        migrated = validate_settings({"shares": [first, second]})
        self.assertEqual([item["export_id"] for item in migrated["shares"]], [1, 2])
        self.assertEqual([item["export_id"] for item in validate_settings(
            {"shares": list(reversed(migrated["shares"]))})["shares"]], [2, 1])

    def test_persisted_legacy_nfs_requires_explicit_backend_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"shares":[{"id":"data","name":"Files",'
                            '"smb_enabled":false,"nfs_enabled":true,"read_only":false,'
                            '"nfs_clients":["192.168.0.0/24"]}]}', encoding="utf-8")
            legacy = load(str(path))
            self.assertEqual(legacy["nfs_backend"], "legacy-v4")
            self.assertNotIn("/shares/data", unfs3_exports(legacy))
            legacy["nfs_backend"] = "unfs3-v3"
            save(str(path), legacy)
            self.assertIn("/shares/data", unfs3_exports(load(str(path))))

    def test_fruit_loaded_consistently_for_mac_clients(self):
        ordinary = share("media", "Media")
        backup = share("backups", "MacBackup")
        backup.update(smb_users=["alice"], time_machine=True)
        config = samba_conf(validate_settings({"shares": [ordinary, backup]}))
        self.assertEqual(config.count("vfs objects = catia fruit streams_xattr"), 1)
        self.assertEqual(config.count("fruit:time machine = yes"), 1)
        self.assertLess(config.index("vfs objects"), config.index("[Media]"))

    def test_smb_protocol_range_and_share_options(self):
        item = share("data", "Files")
        item.update(smb_users=["alice"], smb_browseable=False, smb_encryption="required",
                    smb_clients=["192.168.1.75/24"])
        value = validate_settings({"shares": [item], "smb_min_protocol": "SMB3_00",
                                   "smb_max_protocol": "SMB3_11"})
        config = samba_conf(value)
        self.assertIn("server min protocol = SMB3_00", config)
        self.assertIn("server max protocol = SMB3_11", config)
        self.assertIn("browseable = no", config)
        self.assertIn("server smb encrypt = required", config)
        self.assertIn("hosts allow = 192.168.1.0/255.255.255.0", config)
        with self.assertRaises(ValueError):
            validate_settings({"shares": [item], "smb_min_protocol": "SMB3_11",
                               "smb_max_protocol": "SMB2_10"})
        with self.assertRaises(ValueError):
            validate_settings({"shares": [item], "smb_max_protocol": "SMB2_10"})
        with self.assertRaises(ValueError):
            validate_settings({"shares": [], "smb_min_protocol": "NT1"})
        with self.assertRaises(ValueError):
            validate_settings({"shares": [{**item, "smb_clients": ["0.0.0.0/0", "not-an-ip"]}]})
        with self.assertRaises(ValueError):
            validate_settings({"shares": [{**item, "smb_clients": ["::1"]}]})

    def test_legacy_smb_settings_keep_existing_range_and_visibility(self):
        value = validate_settings({"shares": [share("data", "Files")]})
        self.assertEqual((value["smb_min_protocol"], value["smb_max_protocol"]),
                         ("SMB2_10", "SMB3_11"))
        self.assertTrue(value["shares"][0]["smb_browseable"])
        self.assertEqual(value["shares"][0]["smb_encryption"], "default")
        self.assertEqual(value["shares"][0]["smb_clients"], [])

    def test_smb_and_nfs_can_have_different_write_policies(self):
        item = share("data", "Files", nfs=True, clients=["192.168.0.0/24"])
        item.update(smb_users=["alice"], read_only=False, nfs_read_only=True)
        value = validate_settings({"shares": [item]})
        self.assertIn("read only = no", samba_conf(value))
        self.assertIn("/shares/data 192.168.0.0/24(ro,all_squash,anonuid=1000,anongid=1000,secure)", unfs3_exports(value))
        item["nfs_read_only"] = "yes"
        with self.assertRaises(ValueError):
            validate_settings({"shares": [item]})

    def test_maproot_and_mapall_are_scoped_to_each_export(self):
        personal = share("data", "Personal", smb=False, nfs=True, clients=["192.168.0.10/32"])
        personal.update(path="personal", nfs_mapping="maproot", nfs_uid=2001, nfs_gid=2000)
        shared = share("data", "Shared", smb=False, nfs=True, clients=["192.168.0.11/32"])
        shared.update(path="shared", nfs_mapping="mapall", nfs_uid=2002, nfs_gid=2000)
        value = validate_settings({"shares": [personal, shared], "collaborative_mode": False})
        exports = unfs3_exports(value)
        self.assertIn("/shares/data/personal 192.168.0.10/32(rw,root_squash,anonuid=2001,anongid=2000,secure)", exports)
        self.assertIn("/shares/data/shared 192.168.0.11/32(rw,all_squash,anonuid=2002,anongid=2000,secure)", exports)

    def test_non_reserved_nfs_ports_require_explicit_opt_in(self):
        item = share("data", "Files", smb=False, nfs=True, clients=["10.0.0.0/24"])
        self.assertIn("all_squash,anonuid=1000,anongid=1000,secure", unfs3_exports(validate_settings({"shares": [item]})))
        item["nfs_insecure"] = True
        self.assertIn("all_squash,anonuid=1000,anongid=1000,insecure", unfs3_exports(validate_settings({"shares": [item]})))
        item["nfs_insecure"] = "yes"
        with self.assertRaises(ValueError):
            validate_settings({"shares": [item]})

    def test_nfs_identity_requires_both_unprivileged_ids(self):
        item = share("data", "Shared", smb=False, nfs=True, clients=["10.0.0.0/24"])
        for change in ({"nfs_mapping": "mapall"}, {"nfs_mapping": "mapall", "nfs_uid": 0, "nfs_gid": 2000},
                       {"nfs_mapping": "maproot", "nfs_uid": "2001", "nfs_gid": 2000},
                       {"nfs_mapping": "root-squash", "nfs_uid": 2001, "nfs_gid": 2000}):
            with self.assertRaises(ValueError):
                validate_settings({"shares": [{**item, **change}]})

    def test_export_path_with_spaces_is_quoted(self):
        item = share("data", "Media", smb=False, nfs=True, clients=["10.0.0.0/24"])
        item["path"] = "My Movies"
        self.assertIn('"/shares/data/My Movies" 10.0.0.0/24(',
                      unfs3_exports(validate_settings({"shares": [item]})))


if __name__ == "__main__":
    unittest.main()
