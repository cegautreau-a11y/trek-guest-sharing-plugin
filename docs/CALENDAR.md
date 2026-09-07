# Calendar

Guest Portal provides an iCal/WebCal feed for subscribing to the trip itinerary in calendar applications.

## Calendar tab

The **Calendar** tab appears when the guest portal configuration has iCal enabled. It displays a WebCal subscription URL and step-by-step instructions for Apple Calendar.

## iCal feed

The feed is generated server-side and validated against TREK on every request. It follows RFC 5545 with VTIMEZONE components for proper timezone support.

### Routes

| Route | Description |
|-------|-------------|
| `GET /calendar/<trip_token>.ics` | iCal feed with VTIMEZONE components |
| `GET /ical/<trip_token>` | Redirects to `webcal://` variant |
| `GET /api/ical-link` | Returns the WebCal URL for the current session |

### Timezone handling

- Flight departure/arrival times are converted to the respective airport's local timezone
- Accommodation events use `ICAL_TIMEZONE` as fallback
- When per-event airport timezone resolution fails, `ICAL_TIMEZONE` is used
- Times are emitted as UTC with `Z` suffix for Apple Calendar compatibility

### Supported clients

- **Apple Calendar** (macOS/iOS) — fully supported; VTIMEZONE with DST transitions are respected
- **Other CalDAV clients** — supported when they respect RFC 5545 VTIMEZONE components

Google Calendar, Outlook, and other CalDAV clients that ignore VTIMEZONE are not supported.

## Configuration

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ICAL_TIMEZONE` | `UTC` | IANA timezone name (e.g. `America/New_York`) used as fallback for accommodation events and when per-event airport timezone resolution fails. |
| `GUEST_PLUGIN_PATH` | `/guest-plugin/` | Base URL path prefix if the companion is mounted under a URL prefix. Must start and end with a slash. Set this to match the reverse-proxy mount point so iCal/WebCal URLs are generated correctly. |

### Per-trip enablement

iCal must be explicitly enabled in the TREK plugin configuration for each guest portal:

1. In TREK, go to the trip's Guest Portal settings
2. Enable **Calendar Feed**
3. Save and share the updated guest URL

The Calendar tab will only appear for guests when iCal is enabled for that portal.

## Security

- The iCal feed requires a valid guest session cookie
- The feed URL contains the trip share token but no bearer credentials
- Share revocation in TREK is respected on every feed request

## Troubleshooting

### Calendar tab is not visible

Confirm iCal is enabled in the Guest Portal configuration for that trip in TREK.

### Events show wrong times

Set `ICAL_TIMEZONE` to the primary trip timezone in `.env`:

```dotenv
ICAL_TIMEZONE=America/New_York
```

### Apple Calendar not updating

Apple Calendar should refresh automatically. If events are stale:

1. Open Apple Calendar → Preferences → General
2. Click "Refresh Calendars"
3. Or right-click the subscribed calendar and choose "Refresh"

The feed refreshes on every request; Apple typically polls every few minutes.

### WebCal link not working

Some browsers may not register the `webcal://` protocol automatically. If the link does not open Apple Calendar:

1. Copy the WebCal URL manually
2. Open Apple Calendar → File → New Calendar Subscription
3. Paste the URL and click Subscribe
