# Photos and Immich

## Display behavior

Photos are grouped by date and sorted chronologically. Journal content is not rendered.

## Date resolution priority

When Immich integration is configured and the Journey photo includes an Immich asset ID:

1. Immich EXIF `dateTimeOriginal` when available;
2. Immich `localDateTime`;
3. Immich `fileCreatedAt`;
4. Journey entry/photo timestamp;
5. `Undated` only if no date can be resolved.

## Security boundary

The Immich API key exists only in the companion container as a mounted secret. Browser JavaScript receives resolved date values, not the API key.

The companion first works from a validated Journey public session and only looks up the asset IDs associated with that shared Journey data.

## Immich key

Create a dedicated API key with the minimum read permissions needed for asset metadata. Immich supports scoped API keys.

Recommended file:

```text
/opt/trek-guest-portal/secrets/immich_api_key
```

Permissions:

```text
0600, owned by 65532:65532
```
