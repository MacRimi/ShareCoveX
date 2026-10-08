# ShareCoveX — October 2026 update

This update makes ShareCoveX more resilient as a long-running file-sharing appliance, improves how configuration changes are applied to active clients, introduces panel sessions, and expands the Time Machine integration.

## Highlights

### Services are supervised continuously

Samba, NFS, and Bonjour are now monitored every five seconds even when nobody has the panel open. If one of them exits, ShareCoveX starts it again. A 30-second restart guard prevents a failing service from entering a tight restart loop, and the panel reports the observed failure.

When the container stops, ShareCoveX terminates Bonjour, NFS, and Samba in an orderly sequence and allows up to 20 seconds for the services to close. The supplied Compose configuration now gives the container a 30-second stop grace period.

### Configuration changes avoid unnecessary disruption

Samba reloads its configuration without restarting. Only clients connected to a resource whose definition changed are asked to reconnect; sessions using other resources remain active.

NFS restarts only when an NFS export changes. This avoids disturbing NFS clients for unrelated panel changes. NFSv3 clients continued working during the laboratory test.

### Panel sessions and sign-in protection

The panel now has a sign-in form, an `HttpOnly` and `SameSite=Strict` session cookie, and an explicit sign-out action. Sessions expire after 30 minutes without activity or after 12 hours in total. Changing an administrator password closes that administrator's existing sessions.

New administrator and Samba passwords must contain at least eight characters. Existing passwords remain valid until they are changed.

Five failed sign-ins from one client address within five minutes temporarily block further attempts from that address. Repeated lockouts increase the waiting period, up to 15 minutes.

### Time Machine destinations

Every enabled Time Machine resource is announced over Bonjour as a backup destination. A destination may now define its own advertised maximum size or inherit the general Time Machine limit.

Before enabling a new Time Machine destination, ShareCoveX checks whether its filesystem exposes the extended-attribute support required by macOS metadata. The panel also shows this capability with the resource permissions.

### Operational diagnostics

- The image now includes a health check backed by `GET /healthz`.
- Samba writes rejected access attempts to the container log with the user, address, and reason.
- Bonjour uses the interface carrying the default route and removes stale runtime state before starting.
- Mounted folders whose names cannot become valid ShareCoveX identifiers are reported instead of silently disappearing.
- A Samba user's UID is never reused after that user is removed.
- Folder names containing `%`, which Samba cannot publish safely, are rejected with a specific error.
- The application mark is now rendered from vector paths and no longer depends on an Apple-only font.

## Validation

The updated image was exercised in CT 122 on the Proxmox laboratory host and from two Macs on the same network.

- Samba, NFS, and Bonjour recovered within approximately four seconds after being terminated with the panel closed.
- The restart guard prevented an immediate second failure from looping.
- The container stopped cleanly with exit code 0 while a Mac was connected.
- Forty consecutive writes completed without error while an unrelated Samba resource was changed; the Samba process and the active connection remained in place.
- A complete 1.1 GB Time Machine backup finished successfully on the `TimeMachine` destination.
- Separate destinations advertised 300 GB and 750 GB limits, while a normal share continued to report the real disk size.
- Session enforcement, lockout behavior, password-change invalidation, same-origin write protection, and the container health check were verified.
- The automated suite contains 93 Python tests, plus JavaScript syntax and interface tests.

## Upgrade notes

- The panel now requires form-based sign-in. Clients that previously sent HTTP Basic credentials with every request must establish a session first.
- The eight-character minimum applies only when creating or changing a password; existing credentials are not invalidated.
- Changing an NFS resource restarts the NFS service. Other changes do not.
- Compose deployments should retain the new 30-second stop grace period.
- The panel still serves plain HTTP. When it is placed behind HTTPS, the reverse proxy must pass `X-Forwarded-Proto: https` so the session cookie is marked `Secure`.
- Login limits use the client address visible to ShareCoveX. If a reverse proxy or NAT presents one shared address, the limit is shared by those clients.

## Remaining validation and known limitations

- Restoring data from the completed Time Machine backup has not yet been tested.
- The updated NFSv4/Ganesha path still requires a real privileged-container validation.
- The session cookie behavior still requires validation behind a real HTTPS reverse proxy.
- The `linux/arm64` image is built by CI but still requires runtime validation.
- Some dynamic interface and server error messages are not yet translated across all eight languages.
- A macOS-created file was observed with group read access but without group write access. Collaborative mode continued to work, but the cause has not yet been isolated.
- The panel currently evaluates folder ACL information on every state refresh.
- No stable version tag exists yet; `latest` and commit-derived image tags remain the available references.

ShareCoveX does not change ownership, ACLs, quotas, snapshots, or backup policies on the host. Administrators remain responsible for those storage controls and for validating restores in their own environment.
