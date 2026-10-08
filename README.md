<p align="center"><img src="assets/sharecovex.svg" alt="ShareCoveX" width="1000"></p>

<h1 align="center">ShareCoveX</h1>
<p align="center"><strong>Share existing host folders over SMB, NFS, or both.</strong></p>
<p align="center">
  <a href="https://github.com/MacRimi/ShareCoveX/actions/workflows/tests.yml"><img src="https://github.com/MacRimi/ShareCoveX/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/MacRimi/ShareCoveX/pkgs/container/sharecovex"><img src="https://img.shields.io/badge/container-GHCR-315f8c" alt="GHCR image"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-f0a95a" alt="MIT license"></a>
</p>

<p align="center">
  <a href="#quick-start-with-docker">Docker</a> ·
  <a href="#proxmox-oci-container">Proxmox OCI</a> ·
  <a href="#files-and-permissions">Permissions</a> ·
  <a href="#protocol-notes">Protocols</a> ·
  <a href="#upgrades-and-backups">Upgrades</a>
</p>

ShareCoveX is a container-first file-sharing appliance for Linux hosts and Proxmox OCI/LXC. It discovers folders mounted below `/shares`, lets an administrator publish a complete mount or selected non-overlapping subfolders, and generates Samba and NFS configuration without moving the original data.

## At a glance

| | |
| --- | --- |
| **Use existing folders** | Publish bind-mounted host directories without importing or moving their data. |
| **Independent resources** | Give every mount or subfolder its own SMB users, NFS clients, protocol and read/write policy. |
| **Docker and Proxmox** | One image designed for Docker Engine and Proxmox OCI/LXC containers. |
| **Mac friendly** | Bonjour discovery, Samba fruit extensions and dedicated Time Machine destinations. |
| **Permission aware** | Shows real UID, GID, mode and effective write compatibility without silently changing host data. |
| **Self-contained** | No database, cloud account or external control plane. Configuration lives in `/config`. |

## Features

- Independent SMB and NFS settings for every published folder.
- SMB 2.0.2 through 3.1.1, authenticated users, optional read-only guests, client-network restrictions, encryption, browse visibility, and Time Machine profile.
- NFSv3 in ordinary Docker and unprivileged Proxmox OCI/LXC environments.
- NFSv4 through NFS-Ganesha when the runtime can provide real file handles and `CAP_DAC_READ_SEARCH`.
- Multiple panel administrators and Samba users with password rotation and removal.
- Panel sign-in with a session cookie, a limit on failed attempts, and sign-out.
- Bonjour/mDNS discovery with a configurable server name; Time Machine destinations are announced as backup disks.
- Supervised services: SMB, NFS, and discovery are started again if they end, and are stopped in order when the container stops.
- Settings applied without restarting Samba: saving a resource leaves the SMB clients of every other resource connected. The NFS server restarts only when an NFS export changes.
- Server and share pause controls that preserve configuration.
- Per-mount and per-subfolder UID/GID, mode, and effective collaborative-write diagnostics.
- Eight interface languages: English, German, Spanish, French, Italian, Portuguese, Slovak, and Swedish.
- Persistent configuration and Samba account databases in `/config`.

ShareCoveX never creates host mounts, browses arbitrary host paths, deletes shared data, or changes ownership and ACLs automatically.

## Quick start with Docker

Requirements: Linux, Docker Engine, Docker Compose, free TCP ports `445`, `2049`, `20048`, and a trusted local network.

```bash
git clone https://github.com/MacRimi/ShareCoveX.git
cd ShareCoveX
cp .env.example .env
```

Set a unique administrator password and an absolute host folder in `.env`, then start the standard NFSv3 profile. Compose pulls the published image from GHCR:

```bash
docker compose pull
docker compose up -d
```

The example exposes the panel only on `127.0.0.1:8080`. Reach it locally, through an SSH tunnel, or through a trusted HTTPS reverse proxy. Add one bind mount per source folder:

```yaml
volumes:
  - ./config:/config
  - /srv/shares/media:/shares/media
  - /srv/shares/backups:/shares/backups
```

Each target below `/shares` must have a unique lowercase identifier. ShareCoveX detects the mount after the container is recreated with the new volume.

Open `http://HOST:8080` when exposing the panel on your LAN, or retain the safer loopback binding from the example and use a trusted reverse proxy or SSH tunnel. Sign in as `admin` with `SHARECOVEX_ADMIN_PASSWORD`; additional panel administrators can be created in Settings. Passwords for administrators and Samba users have at least 8 characters. A session ends after 30 minutes without activity, after 12 hours, on sign-out, or when the password of its administrator changes. Five failed sign-ins from one address within five minutes block that address for one minute, and for longer each time it happens again.

### Docker Compose example

```yaml
services:
  sharecovex:
    image: ghcr.io/macrimi/sharecovex:latest
    container_name: sharecovex
    restart: unless-stopped
    stop_grace_period: 30s
    environment:
      SHARECOVEX_ADMIN_PASSWORD: "replace-with-a-unique-password"
    ports:
      - "8080:8080/tcp"
      - "445:445/tcp"
      - "2049:2049/tcp"
      - "20048:20048/tcp"
    volumes:
      - ./config:/config
      - /srv/shares/media:/shares/media
      - /srv/shares/backups:/shares/backups
```

Do not publish duplicate SMB/NFS ports from another service on the same host. Bonjour discovery also requires the container to reach the local multicast network; host networking or an appropriate macvlan/ipvlan network may be preferable when bridge multicast is restricted.

## Proxmox OCI container

ShareCoveX is designed and tested as a Proxmox OCI/LXC application container. Use the published image `ghcr.io/macrimi/sharecovex:latest`, persist `/config`, and attach every host directory as a mount point below `/shares/<id>`.

Recommended OCI layout:

| Container path | Purpose | Required |
| --- | --- | --- |
| `/config` | Settings, administrator hashes and Samba account database | Yes, persistent |
| `/shares/media` | First host directory | At least one share |
| `/shares/<id>` | Additional host directories | Optional, repeatable |

Expose TCP `8080`, `445`, `2049`, and `20048` to the trusted LAN. Give the container a stable address so SMB/NFS clients and allowed CIDR rules do not change unexpectedly.

### Proxmox compatibility

| Capability | Unprivileged OCI/LXC | Privileged OCI/LXC |
| --- | --- | --- |
| Web administration | Yes | Yes |
| SMB 2/3 | Yes | Yes |
| Bonjour discovery | Yes | Yes |
| Time Machine over SMB | Yes | Yes |
| NFSv3 | Yes | Yes |
| NFSv4 with Ganesha | No | Yes, with `CAP_DAC_READ_SEARCH` and usable file handles |

The normal and recommended Proxmox profile is unprivileged with SMB and/or NFSv3. NFSv4 is intentionally hidden there because an unprivileged LXC user namespace cannot provide the real file-handle operations Ganesha requires. Use a privileged OCI container only when NFSv4 is a deliberate requirement and the additional trust is acceptable.

## NFSv4 profile

NFSv4 uses NFS-Ganesha and is intentionally separate from the standard profile:

```bash
docker compose -f compose.yaml -f compose.nfsv4.yaml up -d
```

The host must allow `CAP_DAC_READ_SEARCH`, `open_by_handle_at`, and usable file handles for the mounted filesystem. Many unprivileged LXC environments cannot provide these capabilities; use NFSv3 there. Do not make a container privileged only to bypass an unexplained capability error.

## Files and permissions

Network authorization does not override Unix filesystem permissions. By default ShareCoveX uses the collaborative identity `1000:1000` for writable SMB/NFS content. Mounted folders must permit that identity to traverse, read, and write.

In an unprivileged LXC, container IDs are mapped to a host range. For a common map starting at `100000`, container UID `1000` is host UID `101000`. Check the real map instead of assuming it:

```bash
cat /proc/self/uid_map
cat /proc/self/gid_map
```

Prepare ownership or ACLs deliberately on the host. ShareCoveX reports the effective state but never applies `chown`, `chmod`, or `setfacl` to mounted data. An explicit host ACL can preserve the existing owner while granting the mapped service identity access:

```bash
setfacl -R -m u:101000:rwX,m::rwx /srv/shares/media
find /srv/shares/media -type d -exec setfacl -m d:u:101000:rwx,d:m::rwx {} +
```

Adapt the path and mapped ID to your installation. Existing files and new-file inheritance are separate concerns, so verify both from real SMB and NFS clients.

## Protocol notes

### SMB

SMB1 is not offered. The server-wide minimum and maximum can be selected in Settings. Each resource controls users, guest read-only access, client networks, encryption, visibility, and authenticated read/write access. Samba credentials are separate from panel administrator accounts.

### NFS

NFS uses AUTH_SYS identities and trusted client IP/CIDR rules, not Samba users. NFSv3 uses UNFS3 over TCP without NLM locking. A compatible client command is:

```bash
mount -t nfs -o vers=3,proto=tcp,mountproto=tcp,port=2049,mountport=2049,nolock SERVER:/shares/media /mnt/media
```

NFSv4 uses Ganesha when the environment passes the runtime capability probe. Neither profile currently provides Kerberos.

### Time Machine

A Time Machine destination must be a dedicated writable SMB-only resource with at least one authenticated Samba user, no guests, and no NFS. ShareCoveX passes the configured maximum size to Samba so macOS can recycle backups: a general size in Settings, and optionally a different one for each destination. This is not a host-filesystem quota; enforce a hard limit in the host dataset or volume as well.

Each enabled destination is announced over Bonjour as a backup disk (`_adisk._tcp`), so it appears in the Time Machine settings of the Macs on the network. The folder must sit on a filesystem that keeps extended attributes; the panel checks this when a destination is enabled and shows a notice on any published folder that lacks them.

## Operation

The services are watched every few seconds whether or not the panel is open. One that ends is started again, at most once every 30 seconds, and the reason is shown on its card in the panel.

Stopping the container ends discovery, NFS, and SMB in that order and waits up to 20 seconds for them, so Samba closes its clients and databases before the container exits. The Compose examples set `stop_grace_period: 30s` to leave room for it.

`GET /healthz` answers without a session: `200` while the services are being watched, `503` once the container is stopping. The image declares it as its `HEALTHCHECK`.

Bonjour is announced on the interface that carries the default route.

## Upgrades and backups

Back up `/config`. It contains settings, administrator hashes, and Samba's credential database. Shared folders remain separate and need their own backup policy. Recreating or upgrading the application container must retain `/config` and remount every `/shares/<id>` source at the same target.

Docker Compose upgrade:

```bash
docker compose pull
docker compose up -d
```

Before changing major versions, back up `/config` and verify that all original mount targets remain present. Never restore `/config` as a substitute for backing up the shared files themselves.

## Security

- Never expose the HTTP panel directly to the Internet. The panel speaks plain HTTP; put it behind an HTTPS reverse proxy that sets `X-Forwarded-Proto: https` so the session cookie is marked `Secure`.
- Restrict SMB/NFS ports at the host firewall to trusted networks.
- Use explicit NFS client networks and understand that AUTH_SYS trusts client-provided IDs.
- Prefer authenticated SMB over guest access for private data.
- Review [`SECURITY.md`](SECURITY.md) before deployment.

## Development

The runtime uses Python's standard library; frontend tests require Node.js.

```bash
python3 -m unittest discover -s tests
node --check web/app.js
node --check web/i18n.js
node tests/test_web.js
```

Build the image with:

```bash
docker build -t sharecovex:dev .
```

Published builds are produced by [GitHub Actions](.github/workflows/container.yml) for `linux/amd64` and `linux/arm64`. Every main-branch build receives `latest` and a commit-derived tag; version tags such as `v0.1.0` also create matching immutable image tags.

## Validation

The current image has been exercised with real SMB read/write round trips from Plex, qBittorrent, Jellyfin, Navidrome, aMule and macOS clients. NFSv3 has been validated with authorized and rejected networks and bidirectional SMB/NFS edits. NFSv4 has been validated through Ganesha in a privileged Proxmox OCI container, including remounting after restart.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution guidelines.

## Current limitations

- Perform real client and restore tests for your own environment before using important data.
- Time Machine behavior depends on macOS and the backing filesystem; a successful connection is not proof of a restorable backup.
- NFSv4 is unavailable in ordinary unprivileged LXC containers that cannot expose file handles.
- NFS has no Kerberos support, and the NFSv3 profile has no NLM locking.
- ShareCoveX diagnoses host filesystem access but intentionally does not repair it.

## License

MIT. See [`LICENSE`](LICENSE). Third-party software in the image retains its own licenses; the UNFS3 license is copied into the image at `/usr/share/doc/sharecovex/UNFS3-LICENSE`.
