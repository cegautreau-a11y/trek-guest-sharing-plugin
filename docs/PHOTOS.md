# Photos and Immich

Guest Portal displays media only when the configured Journey public share allows Gallery access. Journal content is intentionally not rendered.

## Display behavior

Photos and videos are sorted chronologically, grouped under date headings, and opened in the built-in lightbox. The browser requests thumbnails/original media only through session-protected companion endpoints; it does not receive the Journey share capability after the initial session exchange.

## Date resolution priority

For each shared media item, Guest Portal resolves the best available capture date in this order:

1. Immich EXIF `dateTimeOriginal` when the Journey item contains an Immich asset ID and Immich enrichment is configured;
2. Immich `localDateTime`;
3. Immich `fileCreatedAt`;
4. embedded EXIF/XMP metadata parsed from a bounded prefix of the authorized original media;
5. Journey entry/photo timestamp;
6. `Undated` only when no usable date is available.

Resolved dates are cached in memory. `IMMICH_DATE_CACHE_TTL` controls Immich asset-date caching and `PHOTO_DATE_CACHE_TTL` controls embedded-media date caching. `PHOTO_METADATA_PREFIX_BYTES` caps how much of an original photo is read for metadata inspection.

## Security boundary

The Immich API key exists only in the companion container as a read-only mounted secret. Browser JavaScript receives resolved dates, not the API key. The companion begins with a validated Journey guest session and only attempts enrichment for asset/media identifiers contained in that shared Journey data.

## Configure Immich

Set the server URL in `.env`:

```dotenv
IMMICH_URL=https://photos.example.com
IMMICH_VERIFY_TLS=true
IMMICH_TIMEOUT=15
IMMICH_DATE_CACHE_TTL=86400
PHOTO_METADATA_PREFIX_BYTES=2097152
PHOTO_DATE_CACHE_TTL=21600
```

Create a dedicated Immich API key with the minimum read permissions needed for asset metadata and store it in:

```text
/opt/trek-guest-portal/secrets/immich_api_key
```

The normal file permissions are:

```text
0600, owned by 65532:65532
```

Do not place the Immich key in `.env` or `public/config.js`. An empty secret file and empty `IMMICH_URL` disable Immich enrichment without disabling Journey gallery display.

Apply `.env` changes with:

```bash
docker compose config
docker compose up -d
```

See [CONFIGURATION.md](CONFIGURATION.md) for the full variable reference and [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for missing-date diagnostics.
