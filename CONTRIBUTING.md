# Contributing

Contributions and reproducible bug reports are welcome.

## Development checks

Run all checks before opening a pull request:

```bash
python3 -m unittest discover -s tests
node --check web/app.js
node --check web/i18n.js
node tests/test_web.js
```

Keep changes focused, add tests for behavioral changes, and update all eight locale catalogs when introducing user-facing text. Do not commit passwords, generated `/config` data, Samba databases, host paths, private IP addresses from test environments, or container exports.

## Compatibility

Changes should preserve both Docker and Proxmox OCI/LXC operation. Clearly distinguish standard unprivileged NFSv3 behavior from the advanced NFSv4 capability profile. Never add automatic host ownership, mode, or ACL changes from the web panel.
