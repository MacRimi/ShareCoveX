import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from .config import avahi_conf, avahi_service, ganesha_conf, load, samba_conf, save, unfs3_exports, validate_relative_path, validate_settings, validate_user

CONFIG = Path(os.environ.get("SHARECOVEX_CONFIG", "/config"))
SHARES = Path(os.environ.get("SHARECOVEX_SHARES", "/shares"))


def container_environment():
    """Classify LXC separately because its user namespace limits NFSv4."""
    markers = {os.environ.get("container", "").lower()}
    for path in (Path("/run/systemd/container"), Path("/proc/1/environ")):
        try:
            raw = path.read_bytes().replace(b"\0", b"\n").decode("utf-8", "ignore").lower()
            markers.add(raw)
        except OSError:
            pass
    is_lxc = any("lxc" in marker for marker in markers)
    unprivileged = False
    if is_lxc:
        try:
            first = Path("/proc/self/uid_map").read_text(encoding="utf-8").splitlines()[0].split()
            unprivileged = len(first) >= 3 and (first[0] != "0" or first[1] != "0")
        except (OSError, IndexError):
            unprivileged = True
    return {"type": "lxc" if is_lxc else "docker", "unprivileged": unprivileged}


def mountpoints(lines):
    points = set()
    for line in lines:
        fields = line.split(" - ", 1)[0].split()
        if len(fields) >= 5:
            points.add(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4]))
    return points


def command(args, password=None):
    result = subprocess.run(args, input=password, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip()[:300]}")


def listening(port):
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            pass
    return False


def vfs_handle_error(path):
    if not shutil.which("ganesha.nfsd"):
        return "NFS-Ganesha VFS is not installed in this image"
    class FileHandle(ctypes.Structure):
        _fields_ = [("handle_bytes", ctypes.c_uint), ("handle_type", ctypes.c_int),
                    ("handle", ctypes.c_ubyte * 128)]
    libc = ctypes.CDLL(None, use_errno=True)
    handle = FileHandle()
    handle.handle_bytes = 128
    mount_id = ctypes.c_int()
    if libc.name_to_handle_at(-100, os.fsencode(path), ctypes.byref(handle),
                              ctypes.byref(mount_id), 0) != 0:
        return f"{path}: name_to_handle_at failed ({os.strerror(ctypes.get_errno())})"
    # Some kernels reject O_PATH descriptors as mount_fd with EBADF even though
    # name_to_handle_at succeeds. Ganesha opens a regular filesystem descriptor.
    mount_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        opened = libc.open_by_handle_at(mount_fd, ctypes.byref(handle), os.O_RDONLY | os.O_DIRECTORY)
        if opened < 0:
            return (f"{path}: open_by_handle_at failed ({os.strerror(ctypes.get_errno())}); "
                    "NFSv4 requires CAP_DAC_READ_SEARCH outside an unprivileged user namespace")
        os.close(opened)
    finally:
        os.close(mount_fd)
    return None


class Runtime:
    def __init__(self):
        self.lock = threading.RLock()
        self.config_file = CONFIG / "settings.json"
        self.users_file = CONFIG / "users.json"
        self.settings = load(str(self.config_file))
        self.environment = container_environment()
        if self.config_file.exists():
            original = json.loads(self.config_file.read_text(encoding="utf-8"))
            if any(share.get("smb_guest") is True and not share.get("smb_users")
                   and share.get("read_only") is False
                   for share in original.get("shares", []) if isinstance(share, dict)):
                save(str(self.config_file), self.settings)
        if self.environment["type"] == "lxc" and self.environment["unprivileged"]:
            if self.settings.get("nfs_protocols") != ["3"]:
                self.settings["nfs_protocols"] = ["3"]
                validate_settings(self.settings)
                save(str(self.config_file), self.settings)
        self.processes = {}
        self.errors = {}
        self.last_start = {}
        CONFIG.mkdir(parents=True, exist_ok=True)
        for path in ("samba-private", "samba-state"):
            (CONFIG / path).mkdir(mode=0o700, exist_ok=True)
        self.users = self._load_users()
        self._restore_accounts()
        self._write_service_configs()
        self.sync()

    def _load_users(self):
        if not self.users_file.exists():
            return {}
        with self.users_file.open(encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict) or any(
            not isinstance(uid, int) or not 2001 <= uid <= 59999 or validate_user(name) != name
            for name, uid in value.items()
        ) or len({name.lower() for name in value}) != len(value):
            raise ValueError("Invalid persistent user registry")
        return value

    def _save_users(self):
        temporary = self.users_file.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(self.users, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.users_file)

    def _restore_accounts(self):
        import grp
        import pwd
        desired_uid = self.settings.get("shared_uid", 1000)
        desired_gid = self.settings.get("shared_gid", 1000)
        try:
            group = grp.getgrnam("sharecovex")
            if group.gr_gid != desired_gid:
                try:
                    conflict = grp.getgrgid(desired_gid)
                except KeyError:
                    conflict = None
                if conflict and conflict.gr_name != "sharecovex":
                    raise RuntimeError(f"GID {desired_gid} already belongs to {conflict.gr_name}")
                command(["groupmod", "-g", str(desired_gid), "sharecovex"])
        except KeyError:
            command(["groupadd", "-g", str(desired_gid), "sharecovex"])
        try:
            account = pwd.getpwnam("sharecovex-files")
            if account.pw_uid != desired_uid:
                try:
                    conflict = pwd.getpwuid(desired_uid)
                except KeyError:
                    conflict = None
                if conflict and conflict.pw_name != "sharecovex-files":
                    raise RuntimeError(f"UID {desired_uid} already belongs to {conflict.pw_name}")
                command(["usermod", "-u", str(desired_uid), "-g", "sharecovex", "sharecovex-files"])
            elif account.pw_gid != desired_gid:
                command(["usermod", "-g", "sharecovex", "sharecovex-files"])
        except KeyError:
            command(["useradd", "-M", "-u", str(desired_uid), "-g", "sharecovex",
                     "-s", "/usr/sbin/nologin", "sharecovex-files"])
        for name, uid in self.users.items():
            try:
                account = pwd.getpwnam(name)
                if account.pw_uid != uid:
                    raise RuntimeError(f"UID mismatch for {name}")
            except KeyError:
                command(["useradd", "--badname", "-M", "-u", str(uid), "-g", "sharecovex",
                         "-s", "/usr/sbin/nologin", name])

    def _write_service_configs(self):
        (CONFIG / "smb.conf").write_text(samba_conf(self.settings), encoding="utf-8")
        (CONFIG / "exports").write_text(unfs3_exports(self.settings), encoding="utf-8")
        (CONFIG / "ganesha.conf").write_text(ganesha_conf(self.settings), encoding="utf-8")
        (CONFIG / "avahi-daemon.conf").write_text(avahi_conf(self.settings), encoding="utf-8")
        services = Path("/etc/avahi/services")
        services.mkdir(parents=True, exist_ok=True)
        service_file = services / "sharecovex.service"
        service_file.write_text(avahi_service(self.settings), encoding="utf-8")
        # avahi-daemon drops to its own user before loading service definitions.
        service_file.chmod(0o644)
        command(["testparm", "-s", str(CONFIG / "smb.conf")])
        active_nfs = any(share["enabled"] and share["nfs_enabled"] for share in self.settings["shares"])
        if active_nfs and self.settings["nfs_backend"] == "ganesha-vfs":
            if not shutil.which("ganesha.nfsd"):
                raise RuntimeError("NFSv4 requires an image containing NFS-Ganesha VFS")
        else:
            command(["unfsd", "-T", "-e", str(CONFIG / "exports")])

    def mounted_folders(self):
        if not SHARES.is_dir():
            return []
        with open("/proc/self/mountinfo", encoding="utf-8") as stream:
            mounted = mountpoints(stream)
        return sorted(entry.name for entry in SHARES.iterdir()
                      if entry.is_dir() and not entry.is_symlink() and str(entry) in mounted
                      and entry.name != "config")

    def directory(self, mount_id, relative=""):
        if mount_id not in self.mounted_folders():
            raise ValueError("Folder is not mounted")
        relative = validate_relative_path(relative)
        root = SHARES / mount_id
        current = root
        for part in relative.split("/") if relative else []:
            current = current / part
            if current.is_symlink() or not current.is_dir():
                raise ValueError("Folder is missing or uses a symbolic link")
        if current.resolve() != root.resolve() and root.resolve() not in current.resolve().parents:
            raise ValueError("Folder escapes its mount")
        with open("/proc/self/mountinfo", encoding="utf-8") as stream:
            mounted = mountpoints(stream)
        if any(point.startswith(str(current) + "/") or
               (point == str(current) and current != root) for point in mounted):
            raise ValueError("Folder contains a separate nested mount")
        return current

    def tree(self, mount_id, relative=""):
        with self.lock:
            directory = self.directory(mount_id, relative)
            children = sorted((entry for entry in directory.iterdir()
                               if entry.is_dir() and not entry.is_symlink()), key=lambda entry: entry.name)
            visible = children[:300]
            expandable = {}
            for child in visible:
                try:
                    with os.scandir(child) as entries:
                        # Bound the probe for folders containing many files; uncertain folders stay expandable.
                        expandable[child.name] = any(
                            entry.is_dir(follow_symlinks=False) or index >= 199
                            for index, entry in enumerate(entries)
                        )
                except OSError:
                    expandable[child.name] = True
            return {"mount": mount_id, "path": validate_relative_path(relative),
                    "children": [entry.name for entry in visible], "expandable": expandable,
                    "truncated": len(children) > 300}

    @staticmethod
    def _mapped_id(identifier, map_file):
        try:
            with open(map_file, encoding="utf-8") as stream:
                for line in stream:
                    inside, outside, length = (int(item) for item in line.split()[:3])
                    if inside <= identifier < inside + length:
                        return outside + identifier - inside
        except (OSError, ValueError):
            pass
        return identifier

    def _permission_info(self, directory):
        uid = self.settings.get("shared_uid", 1000)
        gid = self.settings.get("shared_gid", 1000)
        stat = directory.stat()
        mode = stat.st_mode & 0o7777
        if stat.st_uid == uid:
            bits = (mode >> 6) & 7
        elif stat.st_gid == gid:
            bits = (mode >> 3) & 7
        else:
            bits = mode & 7
        acl_readable = False
        acl_writable = False
        try:
            acl = subprocess.run(["getfacl", "-cpn", str(directory)], text=True,
                                 capture_output=True, check=False)
            lines = set(acl.stdout.splitlines()) if acl.returncode == 0 else set()
            user_entry = next((line for line in lines if line.startswith(f"user:{uid}:")), "")
            group_entry = next((line for line in lines if line.startswith(f"group:{gid}:")), "")
            acl_readable = "r" in user_entry.rsplit(":", 1)[-1] or "r" in group_entry.rsplit(":", 1)[-1]
            acl_writable = "w" in user_entry.rsplit(":", 1)[-1] or "w" in group_entry.rsplit(":", 1)[-1]
            user_ready = f"user:{uid}:rwx" in lines and f"default:user:{uid}:rwx" in lines
            group_ready = (f"group:{gid}:rwx" in lines and f"default:group:{gid}:rwx" in lines
                           and stat.st_gid == gid and bool(mode & 0o2000))
            acl_ready = user_ready or group_ready
        except OSError:
            acl_ready = False
        return {
            "uid": stat.st_uid, "gid": stat.st_gid, "mode": f"{mode:04o}",
            "readable": acl_readable or bits & 5 == 5,
            "writable": acl_writable or bits & 3 == 3,
            "acl_ready": acl_ready,
            "shared_uid": uid, "shared_gid": gid,
            "host_uid": self._mapped_id(uid, "/proc/self/uid_map"),
            "host_gid": self._mapped_id(gid, "/proc/self/gid_map"),
        }

    def mount_permissions(self, mounted):
        result = {}
        for mount_id in mounted:
            try:
                result[mount_id] = self._permission_info(SHARES / mount_id)
            except OSError:
                continue
        return result

    def share_permissions(self):
        result = {}
        for share in self.settings["shares"]:
            if not share["path"]:
                continue
            key = f'{share["id"]}/{share["path"]}'
            try:
                result[key] = self._permission_info(self.directory(share["id"], share["path"]))
            except OSError:
                continue
        return result

    def _stop(self, name):
        process = self.processes.pop(name, None)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def _start(self, name, args):
        try:
            self.processes[name] = subprocess.Popen(args, start_new_session=True)
            self.last_start[name] = time.monotonic()
            self.errors.pop(name, None)
        except OSError as exc:
            self.errors[name] = str(exc)

    def sync(self):
        with self.lock:
            mounted = set(self.mounted_folders())
            for name in ("smb", "nfs"):
                selected = [share for share in self.settings["shares"]
                            if share.get("enabled", True) and share["smb_enabled" if name == "smb" else "nfs_enabled"]]
                legacy_nfs = name == "nfs" and self.settings["nfs_backend"] == "legacy-v4"
                missing = []
                for share in selected:
                    if share["id"] not in mounted:
                        missing.append(share["id"])
                        continue
                    try:
                        self.directory(share["id"], share["path"])
                    except ValueError:
                        missing.append(share["id"] + "/" + share["path"])
                enabled = (self.settings.get(f"{name}_service_enabled", True)
                           and bool(selected) and not missing and not legacy_nfs)
                v4_issue = None
                if name == "nfs" and enabled and self.settings["nfs_backend"] == "ganesha-vfs":
                    for share in selected:
                        v4_issue = vfs_handle_error(str(self.directory(share["id"], share["path"])))
                        if v4_issue:
                            break
                    enabled = not v4_issue
                process = self.processes.get(name)
                if missing:
                    self.errors[name] = "Missing mounts: " + ", ".join(missing)
                elif legacy_nfs and selected:
                    self.errors[name] = "NFSv4 legacy settings require explicit migration to NFSv3"
                elif v4_issue:
                    self.errors[name] = v4_issue
                if not enabled:
                    self._stop(name)
                    if name == "nfs":
                        self._stop("rpcbind")
                elif not process or process.poll() is not None:
                    if process and process.poll() is not None:
                        self.errors[name] = f"Exited with code {process.returncode}"
                        if time.monotonic() - self.last_start.get(name, 0) < 30:
                            continue
                    if name == "smb":
                        args = ["smbd", "--foreground", "--no-process-group", "-s", str(CONFIG / "smb.conf")]
                    elif self.settings["nfs_backend"] == "ganesha-vfs":
                        rpcbind = self.processes.get("rpcbind")
                        if not rpcbind or rpcbind.poll() is not None:
                            self._start("rpcbind", ["rpcbind", "-f", "-w"])
                            time.sleep(0.2)
                        args = ["ganesha.nfsd", "-F", "-L", "STDOUT", "-f", str(CONFIG / "ganesha.conf")]
                    else:
                        self._stop("rpcbind")
                        args = ["unfsd", "-d", "-t", "-p", "-n", "2049", "-m", "2049", "-e", str(CONFIG / "exports")]
                    self._start(name, args)
                    if name == "nfs" and self.settings["nfs_backend"] == "ganesha-vfs":
                        # Debian's Ganesha registers through rpcbind at startup,
                        # but NFSv4 clients connect directly to TCP/2049.
                        time.sleep(1)
                        self._stop("rpcbind")
            smb = self.processes.get("smb")
            announce = (self.settings["mdns_enabled"] and smb is not None
                        and smb.poll() is None)
            if not announce:
                self._stop("mdns")
            else:
                mdns = self.processes.get("mdns")
                if not mdns or mdns.poll() is not None:
                    if mdns and time.monotonic() - self.last_start.get("mdns", 0) < 30:
                        self.errors["mdns"] = f"Exited with code {mdns.returncode}"
                    else:
                        # Avahi's internal chroot helper can exit as a zombie in LXC/OCI.
                        # It still drops to the avahi user when only chrooting is disabled.
                        self._start("mdns", ["avahi-daemon", "--no-chroot", "-f",
                                             str(CONFIG / "avahi-daemon.conf")])

    def status(self):
        with self.lock:
            mounted = self.mounted_folders()
            services = {}
            for name, port in (("smb", 445), ("nfs", 2049)):
                configured = any(share.get("enabled", True) and share["smb_enabled" if name == "smb" else "nfs_enabled"]
                                 for share in self.settings["shares"])
                enabled = self.settings.get(f"{name}_service_enabled", True) and configured
                alive = bool(self.processes.get(name) and self.processes[name].poll() is None)
                ready = alive and listening(port)
                services[name] = {"enabled": enabled, "configured": configured, "running": ready,
                                  "error": self.errors.get(name) or
                                  ("Process running but not listening" if enabled and alive and not ready else None)}
            mdns = self.processes.get("mdns")
            configured = any(share.get("enabled", True) and share["smb_enabled"] for share in self.settings["shares"])
            enabled = self.settings["mdns_enabled"] and self.settings["smb_service_enabled"] and configured
            services["mdns"] = {"enabled": enabled, "configured": configured,
                                "running": bool(mdns and mdns.poll() is None),
                                "error": self.errors.get("mdns") if enabled else None}
            return {
                "settings": self.settings,
                "users": sorted(self.users),
                "mounted_folders": mounted,
                "mount_permissions": self.mount_permissions(mounted),
                "share_permissions": self.share_permissions(),
                "services": services,
                "nfs_v4_available": not bool(vfs_handle_error(str(SHARES / mounted[0]))) if mounted else False,
                "environment": getattr(self, "environment", {"type": "docker", "unprivileged": False}),
            }

    def update_settings(self, value):
        with self.lock:
            validate_settings(value)
            environment = getattr(self, "environment", {"type": "docker", "unprivileged": False})
            if (environment["type"] == "lxc" and environment["unprivileged"]
                    and value["nfs_protocols"] != ["3"]):
                raise ValueError("Unprivileged LXC supports NFSv3 only")
            mounted = set(self.mounted_folders())
            missing = [share["id"] for share in value["shares"] if share["id"] not in mounted]
            if missing:
                raise ValueError("Folders are not mounted by Docker/OCI: " + ", ".join(missing))
            for share in value["shares"]:
                self.directory(share["id"], share["path"])
                if share["smb_users"] is not None:
                    unknown = set(share["smb_users"]) - set(self.users)
                    if unknown:
                        raise ValueError("Unknown SMB users: " + ", ".join(sorted(unknown)))
            if (value.get("shared_uid", 1000) != self.settings.get("shared_uid", 1000)
                    or value.get("shared_gid", 1000) != self.settings.get("shared_gid", 1000)):
                previous_settings = self.settings
                self.settings = value
                try:
                    self._restore_accounts()
                finally:
                    self.settings = previous_settings
            if value["nfs_backend"] == "ganesha-vfs":
                for share in value["shares"]:
                    if not share["enabled"] or not share["nfs_enabled"]:
                        continue
                    issue = vfs_handle_error(str(self.directory(share["id"], share["path"])))
                    if issue:
                        raise ValueError(issue)
            previous = self.settings
            save(str(self.config_file), value)
            self.settings = load(str(self.config_file))
            try:
                self._write_service_configs()
            except Exception:
                save(str(self.config_file), previous)
                self.settings = previous
                self._write_service_configs()
                raise
            self._stop("smb")
            self._stop("nfs")
            self._stop("mdns")
            self.sync()

    def add_user(self, name, password):
        name = validate_user(name)
        if not isinstance(password, str) or not 6 <= len(password) <= 256 or "\n" in password:
            raise ValueError("Password must contain 6-256 characters without newlines")
        with self.lock:
            if name.lower() in {existing.lower() for existing in self.users}:
                raise ValueError("User already exists")
            uid = max([2000, *self.users.values()]) + 1
            command(["useradd", "--badname", "-M", "-u", str(uid), "-g", "sharecovex",
                     "-s", "/usr/sbin/nologin", name])
            try:
                command(["smbpasswd", "-c", str(CONFIG / "smb.conf"), "-s", "-a", name],
                        password + "\n" + password + "\n")
                self.users[name] = uid
                self._save_users()
            except Exception:
                command(["userdel", name])
                raise

    def password(self, name, password):
        if not isinstance(password, str) or not 6 <= len(password) <= 256 or "\n" in password:
            raise ValueError("Password must contain 6-256 characters without newlines")
        with self.lock:
            if name not in self.users:
                raise ValueError("Unknown user")
            command(["smbpasswd", "-c", str(CONFIG / "smb.conf"), "-s", name],
                    password + "\n" + password + "\n")

    def delete_user(self, name):
        with self.lock:
            if name not in self.users:
                raise ValueError("Unknown user")
            if any(share["smb_enabled"] and share["smb_users"] is not None
                   and name in share["smb_users"] for share in self.settings["shares"]):
                raise ValueError("Remove this user from SMB shares first")
            command(["smbpasswd", "-c", str(CONFIG / "smb.conf"), "-x", name])
            command(["userdel", name])
            del self.users[name]
            self._save_users()
