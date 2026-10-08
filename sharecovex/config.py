import ipaddress
import json
import os
import re
import tempfile
import zlib
from xml.sax.saxutils import escape

DEFAULTS = {"shares": [], "smb_min_protocol": "SMB2_10", "smb_max_protocol": "SMB3_11",
            "nfs_backend": "unfs3-v3", "nfs_protocols": ["3"], "smb_service_enabled": True,
            "nfs_service_enabled": True, "server_name": "ShareCoveX", "mdns_enabled": True,
            "collaborative_mode": True, "shared_uid": 1000, "shared_gid": 1000,
            "time_machine_max_size_gb": 0}
SMB_PROTOCOLS = ("SMB2_02", "SMB2_10", "SMB3_00", "SMB3_02", "SMB3_11")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
USER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SERVER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,47}$")
SHARE_FIELDS = {"id", "name", "smb_enabled", "nfs_enabled", "read_only", "nfs_clients"}
OPTIONAL_FIELDS = {"path", "smb_users", "smb_guest", "time_machine", "export_id", "nfs_read_only",
                   "smb_browseable", "smb_encryption", "smb_clients", "nfs_mapping",
                   "nfs_uid", "nfs_gid", "nfs_insecure", "enabled", "time_machine_max_size_gb"}


def admin_password():
    password = os.environ.get("SHARECOVEX_ADMIN_PASSWORD")
    if password is not None:
        return password
    path = os.environ.get("SHARECOVEX_ADMIN_PASSWORD_FILE", "/config/admin.password")
    if not os.path.isfile(path):
        return ""
    if os.stat(path).st_mode & 0o077:
        raise ValueError("Admin password file must not be readable by group or others")
    with open(path, encoding="utf-8") as stream:
        return stream.read().rstrip("\r\n")


def validate_relative_path(path):
    if not isinstance(path, str) or len(path) > 1024 or path.startswith("/") or "\\" in path:
        raise ValueError("Invalid relative folder path")
    if path in ("", "."):
        return ""
    parts = path.split("/")
    if any(part in ("", ".", "..") or any(ord(char) < 32 or char in ';{}#"' for char in part)
           for part in parts):
        raise ValueError("Invalid relative folder path")
    return path


def validate_settings(value):
    if (not isinstance(value, dict) or "shares" not in value
            or set(value) - {"shares", "smb_min_protocol", "smb_max_protocol", "nfs_backend", "nfs_protocols",
                             "smb_service_enabled", "nfs_service_enabled", "server_name", "mdns_enabled",
                             "collaborative_mode", "shared_uid", "shared_gid", "time_machine_max_size_gb"}
            or not isinstance(value["shares"], list)):
        raise ValueError("Expected a list of shares")
    name = value.get("server_name", DEFAULTS["server_name"])
    if not isinstance(name, str) or not SERVER_NAME.fullmatch(name) or name != name.strip():
        raise ValueError("El nombre del servidor debe tener 1-48 caracteres: letras, numeros, espacios, _ o -")
    value["server_name"] = name
    if type(value.get("mdns_enabled", True)) is not bool:
        raise ValueError("El anuncio Bonjour debe estar activado o desactivado")
    value["mdns_enabled"] = value.get("mdns_enabled", True)
    collaborative = value.get("collaborative_mode", DEFAULTS["collaborative_mode"])
    shared_uid = value.get("shared_uid", DEFAULTS["shared_uid"])
    shared_gid = value.get("shared_gid", DEFAULTS["shared_gid"])
    if type(collaborative) is not bool:
        raise ValueError("El modo colaborativo debe estar activado o desactivado")
    if type(shared_uid) is not int or type(shared_gid) is not int or not 1 <= shared_uid <= 65534 or not 1 <= shared_gid <= 65534:
        raise ValueError("UID y GID compartidos deben estar entre 1 y 65534")
    tm_size = value.get("time_machine_max_size_gb", DEFAULTS["time_machine_max_size_gb"])
    if type(tm_size) is not int or not 0 <= tm_size <= 1048576:
        raise ValueError("El limite de Time Machine debe estar entre 0 y 1048576 GB")
    value["collaborative_mode"] = collaborative
    value["shared_uid"] = shared_uid
    value["shared_gid"] = shared_gid
    value["time_machine_max_size_gb"] = tm_size
    minimum = value.get("smb_min_protocol", DEFAULTS["smb_min_protocol"])
    maximum = value.get("smb_max_protocol", DEFAULTS["smb_max_protocol"])
    if minimum not in SMB_PROTOCOLS or maximum not in SMB_PROTOCOLS or SMB_PROTOCOLS.index(minimum) > SMB_PROTOCOLS.index(maximum):
        raise ValueError("Choose a valid SMB2/SMB3 protocol range")
    value["smb_min_protocol"] = minimum
    value["smb_max_protocol"] = maximum
    backend = value.get("nfs_backend", DEFAULTS["nfs_backend"])
    if backend not in ("unfs3-v3", "ganesha-vfs", "legacy-v4"):
        raise ValueError("Unsupported NFS backend")
    protocols = value.get("nfs_protocols", ["4"] if backend == "legacy-v4" else ["3"])
    if (not isinstance(protocols, list) or not protocols or len(protocols) != len(set(protocols))
            or any(protocol not in ("3", "4") for protocol in protocols)):
        raise ValueError("Select NFSv3, NFSv4 or both")
    protocols = [protocol for protocol in ("3", "4") if protocol in protocols]
    if backend != "legacy-v4":
        backend = "ganesha-vfs" if "4" in protocols else "unfs3-v3"
    value["nfs_backend"] = backend
    value["nfs_protocols"] = protocols
    for service in ("smb", "nfs"):
        key = f"{service}_service_enabled"
        enabled = value.get(key, True)
        if type(enabled) is not bool:
            raise ValueError(f"{key} must be true or false")
        value[key] = enabled
    paths, names, export_ids = set(), set(), set()
    legacy_nfs_id = 0
    for share in value["shares"]:
        if not isinstance(share, dict) or not SHARE_FIELDS <= set(share) or set(share) - SHARE_FIELDS - OPTIONAL_FIELDS:
            raise ValueError("Unexpected or missing share settings")
        legacy = not (set(share) & OPTIONAL_FIELDS)
        if not isinstance(share["id"], str) or not SLUG.fullmatch(share["id"]):
            raise ValueError("Folder ID must use lowercase letters, digits, _ or -")
        if not isinstance(share["name"], str) or not NAME.fullmatch(share["name"]):
            raise ValueError("Share name must start with a letter and use letters, digits, _ or -")
        share["path"] = validate_relative_path(share.get("path", ""))
        if "%" in share["path"] and share["smb_enabled"] and share.get("enabled", True) is not False:
            # Samba replaces %U, %H and the like in a path: the share would
            # point somewhere else than the folder chosen here.
            raise ValueError("Samba no puede publicar una carpeta cuyo nombre contiene el caracter %")
        key = (share["id"], share["path"])
        if key in paths or share["name"].lower() in names:
            raise ValueError("Share paths and names must be unique")
        paths.add(key)
        names.add(share["name"].lower())
        for key in ("smb_enabled", "nfs_enabled", "read_only"):
            if type(share[key]) is not bool:
                raise ValueError(f"{key} must be true or false")
        enabled = share.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("enabled must be true or false")
        if enabled and not (share["smb_enabled"] or share["nfs_enabled"]):
            raise ValueError("An active resource requires SMB or NFS")
        share["enabled"] = enabled
        nfs_read_only = share.get("nfs_read_only", share["read_only"])
        if type(nfs_read_only) is not bool:
            raise ValueError("nfs_read_only must be true or false")
        share["nfs_read_only"] = nfs_read_only
        mapping = share.get("nfs_mapping", "root-squash")
        if mapping not in ("root-squash", "maproot", "mapall"):
            raise ValueError("Choose root squash, Maproot or Mapall for NFS")
        uid, gid = share.get("nfs_uid"), share.get("nfs_gid")
        if mapping == "root-squash":
            if uid is not None or gid is not None:
                raise ValueError("Custom NFS identity requires Maproot or Mapall")
        elif (enabled or uid is not None or gid is not None) and (type(uid) is not int or
              type(gid) is not int or not 1 <= uid <= 65534 or not 1 <= gid <= 65534):
            raise ValueError("Maproot/Mapall require numeric container UID and GID (1-65534)")
        share["nfs_mapping"] = mapping
        share["nfs_uid"] = uid
        share["nfs_gid"] = gid
        insecure = share.get("nfs_insecure", False)
        if type(insecure) is not bool:
            raise ValueError("NFS non-reserved client ports must be enabled or disabled")
        share["nfs_insecure"] = insecure
        clients = share["nfs_clients"]
        if not isinstance(clients, list) or len(clients) > 32:
            raise ValueError("nfs_clients must be a list of up to 32 networks")
        if any(not isinstance(client, str) for client in clients):
            raise ValueError("NFS clients must be IP addresses or CIDR networks")
        networks = [ipaddress.ip_network(client, strict=False) for client in clients]
        if protocols == ["3"] and any(network.version != 4 for network in networks):
            raise ValueError("NFSv3 client networks must use IPv4 in this tested profile")
        share["nfs_clients"] = [str(network) for network in networks]
        if enabled and share["nfs_enabled"] and not share["nfs_clients"]:
            raise ValueError("NFS requires at least one allowed IP network")
        if share["nfs_enabled"]:
            legacy_nfs_id += 1
        if "export_id" not in share and share["nfs_enabled"] and legacy:
            share["export_id"] = legacy_nfs_id
        users = share.get("smb_users")
        guest = share.get("smb_guest", False)
        if type(guest) is not bool or guest and not share["smb_enabled"]:
            raise ValueError("Guest access requires an enabled SMB share")
        if users is not None:
            if (not isinstance(users, list) or len(users) > 128
                    or any(not isinstance(user, str) for user in users)
                    or len(users) != len(set(users))):
                raise ValueError("Invalid SMB user list")
            for user in users:
                validate_user(user)
            if enabled and share["smb_enabled"] and not users and not guest:
                raise ValueError("Choose at least one SMB user")
        if guest and users is None:
            raise ValueError("Choose explicit SMB users when guest access is enabled")
        if guest and not users and not share["read_only"]:
            raise ValueError("Guest-only SMB access must be read-only")
        share["smb_users"] = users
        share["smb_guest"] = guest
        tm = share.get("time_machine", False)
        if type(tm) is not bool or tm and (not share["smb_enabled"] or share["nfs_enabled"]
                                           or share["read_only"] or users is None or guest):
            raise ValueError("Time Machine necesita SMB con escritura, sin NFS ni invitados y con usuarios SMB seleccionados. Usa una carpeta exclusiva para las copias.")
        share["time_machine"] = tm
        # 0 leaves the size announced for this destination to the server-wide setting.
        tm_share_size = share.get("time_machine_max_size_gb", 0)
        if type(tm_share_size) is not int or not 0 <= tm_share_size <= 1048576:
            raise ValueError("El limite de Time Machine debe estar entre 0 y 1048576 GB")
        share["time_machine_max_size_gb"] = tm_share_size if tm else 0
        browseable = share.get("smb_browseable", True)
        encryption = share.get("smb_encryption", "default")
        if type(browseable) is not bool or encryption not in ("default", "required"):
            raise ValueError("Invalid SMB share options")
        if encryption == "required" and maximum in ("SMB2_02", "SMB2_10"):
            raise ValueError("SMB encryption requires SMB3")
        share["smb_browseable"] = browseable
        share["smb_encryption"] = encryption
        smb_clients = share.get("smb_clients", [])
        if not isinstance(smb_clients, list) or len(smb_clients) > 32:
            raise ValueError("smb_clients must be a list of up to 32 IPv4 networks")
        try:
            share["smb_clients"] = [str(ipaddress.IPv4Network(client, strict=False))
                                    for client in smb_clients if isinstance(client, str)]
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError) as exc:
            raise ValueError("Invalid SMB client IP or network") from exc
        if len(share["smb_clients"]) != len(smb_clients):
            raise ValueError("SMB clients must be IPv4 addresses or CIDR networks")
        if share["nfs_enabled"]:
            export_id = nfs_export_id(share)
            if export_id in export_ids:
                raise ValueError("NFS export ID collision; choose another path")
            export_ids.add(export_id)
            share["export_id"] = export_id
        elif share.get("export_id") is not None:
            raise ValueError("NFS export ID requires an enabled NFS share")
    for index, left in enumerate(value["shares"]):
        for right in value["shares"][index + 1:]:
            if left["id"] != right["id"]:
                continue
            a, b = left["path"], right["path"]
            if not a or not b or a.startswith(b + "/") or b.startswith(a + "/"):
                raise ValueError("Overlapping shares could bypass access restrictions")
    return value


def nfs_export_id(share):
    configured = share.get("export_id")
    if configured is not None:
        if type(configured) is not int or not 1 <= configured <= 65535:
            raise ValueError("Invalid NFS export ID")
        return configured
    return 1 + zlib.crc32(f"{share['id']}/{share['path']}".encode()) % 65534


def share_path(share):
    return "/shares/" + share["id"] + ("/" + share["path"] if share["path"] else "")


def validate_user(name):
    if not isinstance(name, str) or not USER.fullmatch(name):
        raise ValueError("Username must use letters, digits, _ or -")
    if name.lower() in {"root", "nobody", "daemon", "admin"}:
        raise ValueError("This username is reserved")
    return name


def load(path):
    if not os.path.exists(path):
        return {"shares": [], "smb_min_protocol": DEFAULTS["smb_min_protocol"],
                "smb_max_protocol": DEFAULTS["smb_max_protocol"],
                "nfs_backend": DEFAULTS["nfs_backend"],
                "nfs_protocols": list(DEFAULTS["nfs_protocols"]),
                "smb_service_enabled": True, "nfs_service_enabled": True,
                "server_name": DEFAULTS["server_name"], "mdns_enabled": True,
                "collaborative_mode": DEFAULTS["collaborative_mode"],
                "shared_uid": DEFAULTS["shared_uid"], "shared_gid": DEFAULTS["shared_gid"],
                "time_machine_max_size_gb": DEFAULTS["time_machine_max_size_gb"]}
    with open(path, encoding="utf-8") as stream:
        value = json.load(stream)
    # Earlier previews allowed writable guest-only shares; tighten them on load.
    if isinstance(value, dict) and isinstance(value.get("shares"), list):
        for share in value["shares"]:
            if isinstance(share, dict) and share.get("smb_guest") is True and not share.get("smb_users"):
                share["read_only"] = True
            # And SMB paths that Samba would expand: they are paused, not refused,
            # so the server still starts with the rest of its resources.
            if (isinstance(share, dict) and isinstance(share.get("path"), str) and "%" in share["path"]
                    and share.get("smb_enabled") is True):
                share["enabled"] = False
    if (isinstance(value, dict) and "nfs_backend" not in value and
            isinstance(value.get("shares"), list) and
            any(isinstance(item, dict) and item.get("nfs_enabled") is True
                for item in value["shares"])):
        value["nfs_backend"] = "legacy-v4"
    return validate_settings(value)


def save(path, value):
    value = validate_settings(value)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".settings-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def samba_conf(value):
    has_guest = any(share["enabled"] and share["smb_enabled"] and share.get("smb_guest", False)
                    for share in value["shares"])
    model = discovery_model(value)
    lines = ["[global]", "  server role = standalone server",
             f"  server string = {value.get('server_name', DEFAULTS['server_name'])}",
             "  multicast dns register = no", f"  fruit:model = {model}",
             f"  map to guest = {'Bad User' if has_guest else 'Never'}",
             "  guest account = nobody",
             f"  server min protocol = {value.get('smb_min_protocol', DEFAULTS['smb_min_protocol'])}",
             f"  server max protocol = {value.get('smb_max_protocol', DEFAULTS['smb_max_protocol'])}",
             "  disable netbios = yes", "  smb ports = 445",
             "  passdb backend = tdbsam", "  private dir = /config/samba-private",
             "  state directory = /config/samba-state",
             # A rejected sign-in is written to the container log with its
             # user, address and reason; smbd is started with --debug-stdout.
             "  log level = 1 auth_audit:2", "  vfs objects = catia fruit streams_xattr"]
    for share in value["shares"]:
        if not share["enabled"] or not share["smb_enabled"]:
            continue
        users = share["smb_users"]
        guest = share.get("smb_guest", False)
        lines += [f"[{share['name']}]", f"  path = {share_path(share)}",
                  f"  browseable = {'yes' if share.get('smb_browseable', True) else 'no'}",
                  f"  guest ok = {'yes' if guest else 'no'}",
                  "  create mask = 0660",
                  "  force create mode = 0660",
                  "  directory mask = 2770",
                  "  force directory mode = 2770",
                  f"  read only = {'yes' if guest or share['read_only'] else 'no'}"]
        if value.get("collaborative_mode", True):
            lines += ["  force user = sharecovex-files"]
        if guest:
            lines += ["  force group = sharecovex"]
            if users:
                lines += ["  valid users = nobody " + " ".join(users)]
                if not share["read_only"]:
                    lines += ["  write list = " + " ".join(users)]
            else:
                lines += ["  guest only = yes"]
        else:
            lines += ["  valid users = " + (" ".join(users) if users is not None else "@sharecovex"),
                      "  force group = sharecovex"]
        if share.get("smb_encryption") == "required":
            lines += ["  server smb encrypt = required"]
        if share.get("smb_clients"):
            networks = [ipaddress.IPv4Network(client, strict=False) for client in share["smb_clients"]]
            lines += ["  hosts allow = " + " ".join(f"{network.network_address}/{network.netmask}"
                                                  for network in networks)]
        if share["time_machine"]:
            lines += ["  fruit:time machine = yes"]
            size = share.get("time_machine_max_size_gb") or value.get("time_machine_max_size_gb", 0)
            if size:
                lines += [f"  fruit:time machine max size = {size}G"]
    return "\n".join(lines) + "\n"


def samba_sections(text):
    """The share sections of a generated smb.conf: {share name: its lines}."""
    sections, name = {}, None
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            sections[name] = []
        elif name is not None:
            sections[name].append(line)
    sections.pop("global", None)
    return sections


def avahi_conf(value, interface="eth0"):
    """`interface` is the one that carries the default route; without one the
    announcement is left on every interface."""
    host = re.sub(r"[^a-z0-9]+", "-", value["server_name"].lower()).strip("-")
    allowed = f"allow-interfaces={interface}\n" if interface else ""
    return ("[server]\n"
            f"host-name={host}\n"
            f"use-ipv4=yes\nuse-ipv6=no\n{allowed}"
            "enable-dbus=no\n\n[publish]\n"
            "publish-hinfo=no\npublish-workstation=no\n\n"
            "[reflector]\nenable-reflector=no\n")


def discovery_model(value):
    has_time_machine = any(share.get("enabled", True) and share.get("time_machine", False)
                           for share in value["shares"])
    return "TimeCapsule8,119" if has_time_machine else "Xserve"


def time_machine_shares(value):
    return [share["name"] for share in value["shares"]
            if share.get("enabled", True) and share["smb_enabled"] and share.get("time_machine", False)]


def avahi_service(value):
    name = escape(value["server_name"])
    model = discovery_model(value)
    # macOS lists a Time Machine destination by itself only when the server
    # announces its disks: one record per destination, flagged as a backup volume.
    disks = "".join(f"<txt-record>dk{index}=adVN={escape(share)},adVF=0x82</txt-record>"
                    for index, share in enumerate(time_machine_shares(value)))
    adisk = ("  <service><type>_adisk._tcp</type><port>9</port>"
             f"<txt-record>sys=waMa=0,adVF=0x100</txt-record>{disks}</service>\n") if disks else ""
    return ("<?xml version=\"1.0\" standalone='no'?>\n"
            "<!DOCTYPE service-group SYSTEM \"avahi-service.dtd\">\n"
            "<service-group>\n"
            f"  <name>{name}</name>\n"
            "  <service><type>_smb._tcp</type><port>445</port></service>\n"
            "  <service><type>_device-info._tcp</type><port>0</port>"
            f"<txt-record>model={model}</txt-record></service>\n"
            f"{adisk}"
            "</service-group>\n")


def unfs3_exports(value):
    lines = ["# Generated by ShareCoveX. Edit resources in the panel instead."]
    if value.get("nfs_backend") == "legacy-v4":
        return "\n".join(lines) + "\n"
    for share in (item for item in value["shares"] if item["enabled"] and item["nfs_enabled"]):
        path = share_path(share)
        if any(char.isspace() for char in path):
            path = '"' + path + '"'
        options = ["ro" if share["nfs_read_only"] else "rw"]
        collaborative = value.get("collaborative_mode", True)
        if collaborative or share["nfs_mapping"] == "mapall":
            options.append("all_squash")
        else:
            options.append("root_squash")
        if collaborative:
            options += [f"anonuid={value['shared_uid']}", f"anongid={value['shared_gid']}"]
        elif share["nfs_mapping"] != "root-squash":
            options += [f"anonuid={share['nfs_uid']}", f"anongid={share['nfs_gid']}"]
        options.append("insecure" if share["nfs_insecure"] else "secure")
        for client in share["nfs_clients"]:
            lines.append(f"{path} {client}({','.join(options)})")
    return "\n".join(lines) + "\n"


def ganesha_conf(value):
    protocols = ",".join(value["nfs_protocols"])
    lines = ["# Generated by ShareCoveX. Edit resources in the panel instead.",
             "NFS_CORE_PARAM {", f"  Protocols = {protocols};", "  NFS_Port = 2049;",
             "  MNT_Port = 20048;", "  Enable_UDP = false;", "  Enable_NLM = false;",
             "  mount_path_pseudo = true;", "}",
             "NFS_KRB5 {", "  Active_krb5 = false;", "}"]
    for share in (item for item in value["shares"] if item["enabled"] and item["nfs_enabled"]):
        access = "RO" if share["nfs_read_only"] else "RW"
        collaborative = value.get("collaborative_mode", True)
        squash = "All_Squash" if collaborative or share["nfs_mapping"] == "mapall" else "Root_Squash"
        lines += ["EXPORT {", f"  Export_Id = {share['export_id']};",
                  f"  Path = \"{share_path(share)}\";", f"  Pseudo = \"{share_path(share)}\";",
                  f"  Protocols = {protocols};", "  Transports = TCP;", f"  Access_Type = {access};",
                  f"  Squash = {squash};", "  SecType = sys;"]
        if collaborative:
            lines += [f"  Anonymous_Uid = {value['shared_uid']};", f"  Anonymous_Gid = {value['shared_gid']};"]
        elif share["nfs_mapping"] != "root-squash":
            lines += [f"  Anonymous_Uid = {share['nfs_uid']};", f"  Anonymous_Gid = {share['nfs_gid']};"]
        for client in share["nfs_clients"]:
            lines += ["  CLIENT {", f"    Clients = {client};", f"    Access_Type = {access};", "  }"]
        lines += ["  FSAL {", "    Name = VFS;", "  }", "}"]
    lines += ["LOG {", "  Default_Log_Level = WARN;", "  Facility {",
              "    name = STDOUT;", "    destination = STDOUT;", "    enable = active;", "  }", "}"]
    return "\n".join(lines) + "\n"
