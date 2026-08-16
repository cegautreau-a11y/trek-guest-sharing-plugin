# Contributing

## Development requirements

- Python 3.12+
- Node.js 20+ (syntax checking; the runtime plugin itself is packaged by TREK)
- `zip`
- Docker for end-to-end deployment testing

## Before a pull request

```bash
./scripts/validate.sh
python -m unittest discover -s tests -v
```

## Security rules for contributions

Never commit:

- real TREK/Journey share tokens;
- credentials;
- provider API keys;
- `companion/public/config.js` from a real deployment;
- `.env` files;
- SQLite databases or cache files;
- real private hostnames/IPs from a deployment.

Use placeholders such as `trek.example.com`, `photos.example.com`, and synthetic tokens in tests.

## Versioning

Keep these synchronized for a release:

- root `VERSION`;
- `plugin/trek-plugin.json`;
- `plugin/package.json`;
- `companion/server/server.py` `VERSION`;
- companion static cache-busting query versions in `public/index.html`.

Run `scripts/validate.sh` to catch common mismatches.
