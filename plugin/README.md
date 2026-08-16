# TREK Guest Portal Plugin

This directory contains the **Admin-uploadable TREK trip-page plugin**.

It stores per-trip Guest Portal configuration and generates the owner guest URL. The anonymous guest site itself is served by the separate companion under `../companion`.

## Permissions

```text
db:own
db:read:trips
```

## Package

From the repository root:

```bash
./scripts/package-plugin.sh
```

Upload the resulting `dist/trek-guest-portal-<version>.zip` through **TREK → Admin → Plugins → Upload**.

See [docs/INSTALL.md](../docs/INSTALL.md) for the complete first-time setup.
