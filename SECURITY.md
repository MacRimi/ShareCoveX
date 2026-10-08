# Security policy

## Supported versions

ShareCoveX is currently preview software. Security fixes are applied to the latest commit on the default branch; no older release line is supported yet.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private vulnerability reporting feature for this repository. Include the affected revision, deployment type, reproduction steps, impact, and any suggested mitigation. Please avoid accessing data that is not yours while validating a report.

## Deployment boundary

The panel signs administrators in with a form and keeps the session in an `HttpOnly`, `SameSite=Strict` cookie; the password travels only when signing in. The panel itself speaks plain HTTP: keep it on a trusted network, bind it to loopback as in the sample Compose file, or place it behind an HTTPS reverse proxy that sets `X-Forwarded-Proto: https`, which marks the cookie `Secure`. Failed sign-ins are limited per client address as the panel sees it; behind a reverse proxy or a NAT that address is shared, so the limit then applies to everyone who arrives through it. Restrict SMB and NFS with a host firewall. NFS AUTH_SYS trusts client identities and should only be offered to trusted client networks.

ShareCoveX does not alter ownership or ACLs on mounted host data. Administrators remain responsible for host mount definitions, mapped container IDs, filesystem permissions, storage quotas, snapshots, and backups.
