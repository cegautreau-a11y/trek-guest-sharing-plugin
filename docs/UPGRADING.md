# Upgrading

## Back up first

Back up deployment-local state:

```text
public/config.js
cache/
secrets/
```

Example:

```bash
sudo cp -a /opt/trek-guest-portal /opt/trek-guest-portal.backup
```

## Normal 1.x companion update

1. Stop/redeploy the companion as appropriate.
2. Extract the new companion package over the install root.
3. **Preserve `public/config.js`**, `cache/`, and `secrets/`.
4. Review new environment variables in the release notes/example Compose.
5. Restart and inspect startup logs.
6. Test from an incognito browser.

Release packages intentionally ship `config.js.example`, not a real `config.js`.

## TREK plugin update

Upload the new `trek-guest-portal-<version>.zip` through TREK Admin → Plugins. Review permission changes before enabling.

## Migrating from 0.3.x

The pre-1.0 design used native TREK/Journey share tokens in some later API request URLs. If your reverse proxy used standard request-line access logging, old logs may contain replayable old share capabilities.

After a 1.x deployment is verified:

1. regenerate the native TREK public trip share;
2. regenerate the Journey public share if used;
3. save the new URLs in Guest Portal;
4. verify the replacement owner Guest Portal URL;
5. handle old proxy logs according to your retention/security policy.

## TREK major upgrades

The plugin manifest intentionally caps compatibility below TREK 4.0.0. Before moving to a new TREK major version, validate:

- plugin API compatibility;
- public trip-share response fields;
- Journey public-share fields/photo proxy paths;
- Mapbox planner assumptions;
- transport/reservation schema.

Do not simply edit the manifest to force installation without testing.
