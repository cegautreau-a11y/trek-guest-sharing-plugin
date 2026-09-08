# Calendar

Guest Portal provides an iCal/WebCal feed for subscribing to the trip itinerary in calendar applications.

## Calendar tab

The **Calendar** tab appears when the guest portal configuration has iCal enabled. It displays a WebCal subscription URL and step-by-step instructions for Apple Calendar.

## iCal feed

The feed is generated server-side on every request — there is no scheduled regeneration or cached feed file. The upstream TREK share payload is cached for 30 seconds (`SHARE_CACHE_TTL`), so TREK edits appear in the feed within about half a minute. How often subscribers actually fetch the feed is determined by their calendar client (Apple Calendar typically polls every few minutes). It follows RFC 5545 with VTIMEZONE components for proper timezone support.

### Routes

| Route | Description |
|-------|-------------|
| `GET /calendar/<trip_token>.ics` | iCal feed with VTIMEZONE components |
| `GET /ical/<trip_token>` | Redirects to `webcal://` variant |
| `GET /api/ical-link` | Returns the WebCal URL for the current session |

### Supported clients

- **Apple Calendar** (macOS/iOS) — fully supported; VTIMEZONE with DST transitions are respected
- **Other CalDAV clients** — supported when they respect RFC 5545 VTIMEZONE components

Google Calendar, Outlook, and other CalDAV clients that ignore VTIMEZONE are not supported.

### Timezone handling

- Flight departure/arrival times are emitted in the respective airport's local timezone using `TZID=` prefixes (metadata timezones first, then endpoint timezones, then airport-code database lookup, then `ICAL_TIMEZONE`)
- Hotel check-in/check-out events use the hotel's local timezone resolved from GPS coordinates (`place_lat`/`place_lng` or the linked place record), falling back to name matching against the trip's places list, then `ICAL_TIMEZONE`
- Events (restaurants, tours, activities) and accommodations without resolvable coordinates use `ICAL_TIMEZONE` as fallback
- Times are emitted as **local wall-clock time with a `TZID=` prefix** (no `Z` suffix) paired with VTIMEZONE components — this is required for Apple Calendar to display times correctly

## What appears in the feed

### Flights

- **Single-leg flights** produce one VEVENT: `✈ {flight number} - {departure airport} → {arrival airport}`, with departure/arrival times in each airport's local timezone
- **Multi-leg flights** produce one VEVENT per leg, each with its own flight number from `metadata.legs` (e.g. `✈ LA 3292 - SDU → CGH` and `✈ LA 3074 - CGH → FLN`). Each leg uses its per-leg departure/arrival times and the timezone of each endpoint airport
- Airport codes are resolved from `endpoints` first, then `metadata.departure_airport`/`arrival_airport`, then extracted from the reservation title as a last resort

### Hotels

- The full multi-day hotel stay event is **not** emitted
- Each hotel produces two separate 1-hour VEVENTs:
  - **Check-in**: ends at the check-in time (e.g. 13:00–14:00 for a 14:00 check-in)
  - **Check-out**: starts at the check-out time (e.g. 20:00–21:00 for a 20:00 check-out)
- This non-overlapping layout ensures Apple Calendar displays all events separately
- Hotels come from two sources, which are de-duplicated:
  - The `accommodations` array (full stay records with `start_day_id`/`end_day_id` and time-only `check_in`/`check_out` values)
  - Reservations with `type: "hotel"` (single `day_id`, with `check_in_time`/`check_out_time` in metadata). Reservations linking to an accommodation via `accommodation_id` are skipped to avoid duplicates
- When a hotel reservation links to an accommodation record, the reservation's `title` (e.g. "Hotel Tru By Hilton Criciúma") is preferred over the accommodation's place name (e.g. "Criciúma") in event summaries
- Hotel events include the hotel address in the LOCATION field and in the summary

### Other events

- Non-transport reservations (restaurants, tours, activities) appear as `📍 {title}` VEVENTs with the venue address in the LOCATION field and summary
- Addresses are resolved from `address`, `place.address`, the trip's places list (via `place_id`), `location`, or `from` fields
- Events without both a start and end time are skipped
- Other transport (trains, buses, taxis, etc.) appear with their departure point as LOCATION

### Excluded data

- Confirmation codes and other sensitive booking references are never included in the feed

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

Each event carries its own `TZID=` (airport or hotel local timezone). For events that cannot be resolved to a specific timezone, `ICAL_TIMEZONE` is used as the fallback. Set it to the primary trip timezone in `.env`:

```dotenv
ICAL_TIMEZONE=America/New_York
```

### Hotel check-in/check-out events are missing

Hotels appear in the feed only when TREK provides enough data to derive dates:

- Accommodation records need `start_day_id` and `end_day_id` plus time-only `check_in`/`check_out` values
- Hotel-type reservations need a `day_id` (and `end_day_id` if the check-out is on a later day) plus `check_in_time`/`check_out_time` in the reservation metadata
- A hotel reservation with no `day_id` cannot be scheduled — set its day in TREK

### Events are duplicated

If a hotel appears twice in the feed, check that hotel reservations in TREK link to their accommodation record via `accommodation_id`. Reservations that cannot be linked are treated as separate hotels.

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
