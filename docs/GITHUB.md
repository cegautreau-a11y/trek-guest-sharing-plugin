# Publishing this repository to GitHub

The repository is already laid out for GitHub.

## First push

```bash
git init
git add .
git commit -m "Initial TREK Guest Portal release"
git branch -M main
git remote add origin git@github.com:YOUR-ACCOUNT/trek-guest-portal.git
git push -u origin main
```

Before pushing, run:

```bash
./scripts/validate.sh
```

Never commit:

- `companion/public/config.js`;
- `.env`;
- `companion/secrets/`;
- provider API keys;
- native TREK/Journey share URLs or tokens;
- SQLite caches/databases;
- real deployment hostnames/IP addresses unless deliberately public.

## Create a release

The included GitHub Actions release workflow runs when you push a version tag:

```bash
git tag v3.5.0
git push origin v2.1.1
```

It validates the source, packages the plugin and companion, and creates GitHub release assets.

You can create the same artifacts locally:

```bash
./scripts/package-release.sh
```

The release command removes older versioned ZIPs and stale `dist/SHA256SUMS` before generating the current plugin, companion, complete-bundle, and checksum artifacts. The complete bundle includes the root-level Compose deployment files and documentation needed for offline transfer.
