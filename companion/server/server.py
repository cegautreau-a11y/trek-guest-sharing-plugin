#!/usr/bin/env python3
"""TREK Guest Portal companion gateway.

Serves the static guest portal, proxies TREK's anonymous share endpoints to the
TREK `app` service, and provides server-side integrations for live flight status and Immich photo dates.

The hardened deployment does not mount TREK plugin databases at runtime. Guest
requests are authorized by HttpOnly sessions created from native
TREK/Journey public-share capabilities.
"""

from __future__ import annotations

import http.client
import hashlib
import ipaddress
import logging
import json
import mimetypes
import os
import pathlib
import re
import resource
import secrets
import shutil
import sqlite3
import sys
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinderL

# TimezoneFinderL is installed via Dockerfile RUN pip install; in-memory mode
# avoids needing system tzdata on read-only filesystems.
_TF = TimezoneFinderL(in_memory=True)
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit


os.umask(0o077)


def _read_secret(env_name: str, file_env_name: str) -> str:
    """Read a short secret from a mounted file first, then an env fallback."""
    file_name = os.environ.get(file_env_name, "").strip()
    if file_name:
        try:
            path = pathlib.Path(file_name)
            value = path.read_text(encoding="utf-8").strip()
            if value and len(value) <= 4096:
                return value
        except Exception:
            # Do not expose file details here; allow the explicitly configured
            # direct environment fallback to be considered below.
            pass
    value = os.environ.get(env_name, "").strip()
    return value if len(value) <= 4096 else ""

def _secret_source(env_name: str, file_env_name: str, value: str) -> str:
    """Report where a configured secret was obtained without exposing it."""
    if not value:
        return "none"
    file_name = os.environ.get(file_env_name, "").strip()
    if file_name:
        try:
            candidate = pathlib.Path(file_name).read_text(encoding="utf-8").strip()
            if candidate and candidate == value:
                return "secret-file"
        except Exception:
            pass
    if os.environ.get(env_name, "").strip():
        return "environment"
    return "configured"

PUBLIC_ROOT = pathlib.Path(os.environ.get("PUBLIC_ROOT", "/srv/public")).resolve()
TREK_HOST = os.environ.get("TREK_HOST", "app")
TREK_PORT = int(os.environ.get("TREK_PORT", "3000"))
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "20"))
PUBLIC_ORIGIN = os.environ.get("PUBLIC_ORIGIN", "").strip().rstrip("/")
# Public origin of the TREK application itself. This may differ from
# PUBLIC_ORIGIN when the guest portal is deliberately placed on a separate
# hostname to isolate it from TREK's PWA service worker and auth state.
TREK_PUBLIC_ORIGIN = os.environ.get("TREK_PUBLIC_ORIGIN", "").strip().rstrip("/") or PUBLIC_ORIGIN
COOKIE_PATH = os.environ.get("COOKIE_PATH", "/guest-portal/").strip() or "/guest-portal/"
# Base path prefix if the companion is mounted under a URL prefix (e.g. /guest-plugin/).
# Must start and end with a slash. Empty string means no prefix.
GUEST_PLUGIN_PATH = os.environ.get("GUEST_PLUGIN_PATH", "/guest-plugin/").strip()
if not GUEST_PLUGIN_PATH.startswith("/") or not GUEST_PLUGIN_PATH.endswith("/"):
    GUEST_PLUGIN_PATH = "/guest-plugin/"
_SESSION_TTL_RAW = int(os.environ.get("SESSION_TTL_SECONDS", "0"))
# 0 disables server-side time expiration. Positive values retain the optional
# finite-TTL behavior for deployments that explicitly want it.
SESSION_TTL_SECONDS = 0 if _SESSION_TTL_RAW <= 0 else max(300, min(_SESSION_TTL_RAW, 31536000))
# The browser cookie must still carry a finite Max-Age. Ten years is used as an
# effectively persistent cookie when server-side expiration is disabled;
# browsers may enforce their own cookie retention limits.
SESSION_COOKIE_MAX_AGE_SECONDS = max(86400, min(int(os.environ.get("SESSION_COOKIE_MAX_AGE_SECONDS", "315360000")), 315360000))
SESSION_MAX = max(32, min(int(os.environ.get("SESSION_MAX", "2048")), 10000))
SESSION_CREATE_PER_MINUTE = max(10, min(int(os.environ.get("SESSION_CREATE_PER_MINUTE", "120")), 2000))
SESSION_COOKIE_NAME = "trek_guest_session"
# Default timezone for the iCal feed's X-WR-TIMEZONE property.  Should match
# the primary timezone of the trip (e.g. America/New_York, Europe/London).
ICAL_TIMEZONE = os.environ.get("ICAL_TIMEZONE", "UTC").strip()

# Airport code → IANA timezone mapping for per-event timezone conversion in iCal feeds.
# This is loaded lazily from the SQLite database at tools/airport_tz.db
_AIRPORT_TZ: dict[str, str] = {}

# Lazy-load flag
_AIRPORT_TZ_LOADED = False

# Embedded airport timezone data - comprehensive mapping for iCal timezone resolution
# This data is used to build the SQLite database on first run
_EMBEDDED_AIRPORT_TZ_DATA = {
    # Brazil
    "GRU": "America/Sao_Paulo", "GIG": "America/Sao_Paulo", "CGH": "America/Sao_Paulo",
    "BSB": "America/Sao_Paulo", "SSA": "America/Bahia", "CWB": "America/Sao_Paulo",
    "POA": "America/Sao_Paulo", "FLN": "America/Sao_Paulo", "REC": "America/Recife",
    "FOR": "America/Fortaleza", "MAO": "America/Manaus", "BEL": "America/Belem",
    "NAT": "America/Recife", "MCZ": "America/Maceio", "JPQ": "America/Sao_Paulo",
    "NVT": "America/Sao_Paulo", "RBE": "America/Sao_Paulo",
    "SDU": "America/Sao_Paulo", "SLZ": "America/Fortaleza", "THE": "America/Fortaleza",
    "BPS": "America/Bahia", "PNB": "America/Bahia", "JPA": "America/Recife",
    "MCZ": "America/Maceio", "CNF": "America/Sao_Paulo", "IGA": "America/Sao_Paulo",
    "CGB": "America/Manaus", "STM": "America/Manaus", "RBR": "America/Acre",
    "PVH": "America/Porto_Velho", "MAO": "America/Manaus",
    # Canada
    "YYZ": "America/Toronto", "YUL": "America/Montreal",
    "YVR": "America/Vancouver", "YWG": "America/Winnipeg", "YEG": "America/Edmonton",
    "YOW": "America/Toronto", "YQB": "America/Toronto", "YTZ": "America/Toronto",
    "YYC": "America/Edmonton", "YHZ": "America/Halifax", "YQR": "America/Regina",
    "YXY": "America/Whitehorse",
    # United States
    "JFK": "America/New_York", "LAX": "America/Los_Angeles", "ORD": "America/Chicago",
    "DFW": "America/Chicago", "DEN": "America/Denver", "SFO": "America/Los_Angeles",
    "SEA": "America/Los_Angeles", "LAS": "America/Los_Angeles", "MCO": "America/New_York",
    "MIA": "America/New_York", "ATL": "America/New_York", "BOS": "America/New_York",
    "PHL": "America/New_York", "EWR": "America/New_York", "LGA": "America/New_York",
    "DCA": "America/New_York", "IAD": "America/New_York", "MSP": "America/Chicago",
    "DTW": "America/Detroit", "PHX": "America/Phoenix", "IAH": "America/Chicago",
    "SAN": "America/Los_Angeles", "PDX": "America/Los_Angeles",
    "AUS": "America/Chicago", "MSY": "America/Chicago", "BWI": "America/New_York",
    "SLC": "America/Denver", "IND": "America/Indiana/Indianapolis", "CMH": "America/Indiana/Indianapolis",
    "CLE": "America/New_York", "RIC": "America/New_York", "BNA": "America/Chicago",
    "RDU": "America/New_York",
    # Europe
    "LHR": "Europe/London", "LGW": "Europe/London", "STN": "Europe/London", "LTN": "Europe/London",
    "CDG": "Europe/Paris", "ORY": "Europe/Paris",
    "FRA": "Europe/Berlin", "MUC": "Europe/Berlin",
    "AMS": "Europe/Amsterdam", "MAD": "Europe/Madrid", "BCN": "Europe/Madrid",
    "FCO": "Europe/Rome", "MXP": "Europe/Rome",
    "ZRH": "Europe/Zurich", "VIE": "Europe/Vienna", "BRU": "Europe/Brussels",
    "DUB": "Europe/Dublin", "CPH": "Europe/Copenhagen", "OSL": "Europe/Oslo",
    "ARN": "Europe/Stockholm", "HEL": "Europe/Helsinki", "WAW": "Europe/Warsaw",
    "PRG": "Europe/Prague", "BUD": "Europe/Budapest", "ATH": "Europe/Athens",
    "IST": "Europe/Istanbul",
    # Asia / Pacific
    "HND": "Asia/Tokyo", "NRT": "Asia/Tokyo", "KIX": "Asia/Tokyo",
    "PVG": "Asia/Shanghai", "SHA": "Asia/Shanghai", "PEK": "Asia/Shanghai",
    "HKG": "Asia/Hong_Kong", "ICN": "Asia/Seoul", "GMP": "Asia/Seoul",
    "SIN": "Asia/Singapore", "BKK": "Asia/Bangkok", "KUL": "Asia/Kuala_Lumpur",
    "DEL": "Asia/Kolkata", "BOM": "Asia/Kolkata", "MAA": "Asia/Kolkata",
    "DXB": "Asia/Dubai", "AUH": "Asia/Dubai", "DOH": "Asia/Qatar",
    "TLV": "Asia/Jerusalem",
    "SYD": "Australia/Sydney", "MEL": "Australia/Melbourne", "BNE": "Australia/Brisbane",
    "PER": "Australia/Perth", "AKL": "Pacific/Auckland",
    # South / Central America
    "EZE": "America/Argentina/Buenos_Aires", "AEP": "America/Argentina/Buenos_Aires",
    "SCL": "America/Santiago", "LIM": "America/Lima", "BOG": "America/Bogota",
    "MDE": "America/Bogota", "CLO": "America/Bogota", "GYE": "America/Guayaquil",
    "UIO": "America/Guayaquil", "MEX": "America/Mexico_City", "CUN": "America/Cancun",
    "GDL": "America/Mexico_City", "MTY": "America/Monterrey", "QRO": "America/Mexico_City",
    "SAP": "America/Tegucigalpa", "SJO": "America/Costa_Rica", "PTY": "America/Panama",
    "HAV": "America/Havana", "NAS": "America/Nassau", "KIN": "America/Jamaica",
    "PUJ": "America/Santo_Domingo", "SDQ": "America/Santo_Domingo", "POP": "America/Santo_Domingo",
    "GDT": "America/Grand_Turk", "PLS": "America/Grand_Turk", "SXM": "America/Marigot",
    # Africa
    "JNB": "Africa/Johannesburg", "CPT": "Africa/Johannesburg", "CAI": "Africa/Cairo",
    "LOS": "Africa/Lagos", "ACC": "Africa/Accra", "ADD": "Africa/Addis_Ababa",
    "NBO": "Africa/Nairobi", "DUR": "Africa/Johannesburg", "ABJ": "Africa/Abidjan",
}


def _build_airport_tz_database(db_path: str) -> int:
    """Build SQLite airport timezone database from embedded data.

    Creates the tools directory if needed and populates the database.
    Returns the number of airports inserted.
    """
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        logging.info(f"Created tools directory: {db_dir}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS airports (
            iata_code TEXT PRIMARY KEY,
            timezone TEXT NOT NULL
        )
    """)
    cursor.execute("DELETE FROM airports")
    for iata_code, timezone in sorted(_EMBEDDED_AIRPORT_TZ_DATA.items()):
        cursor.execute("INSERT INTO airports (iata_code, timezone) VALUES (?, ?)", (iata_code, timezone))
    conn.commit()
    count = cursor.execute("SELECT COUNT(*) FROM airports").fetchone()[0]
    conn.close()
    logging.info(f"Built airport timezone database with {count} airports at {db_path}")
    return count


def _load_airport_tz_database() -> dict[str, str]:
    """Load airport timezone mappings from SQLite database.

    If the database doesn't exist or is empty, builds it from embedded data.
    Falls back to embedded dict if database operations fail.
    """
    global _AIRPORT_TZ, _AIRPORT_TZ_LOADED

    if _AIRPORT_TZ_LOADED:
        return _AIRPORT_TZ

    # Store in the same directory as the guest cache DB (writable location)
    db_path = os.path.join(os.path.dirname(GUEST_CACHE_DB), "airport_tz.db")

    # Start with embedded fallback (used if DB operations fail)
    _AIRPORT_TZ = _EMBEDDED_AIRPORT_TZ_DATA.copy()

    try:
        # Check if database exists and has data
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT iata_code, timezone FROM airports")
            rows = cursor.fetchall()
            conn.close()

            if rows:
                _AIRPORT_TZ = {code: tz for code, tz in rows}
                logging.info(f"Loaded {len(_AIRPORT_TZ)} airport timezone mappings from database at {db_path}")
            else:
                logging.info("Airport timezone database is empty, building from embedded data")
                _build_airport_tz_database(db_path)
                # Reload from the newly built database
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT iata_code, timezone FROM airports")
                rows = cursor.fetchall()
                conn.close()
                if rows:
                    _AIRPORT_TZ = {code: tz for code, tz in rows}
        else:
            logging.info(f"Airport timezone database not found at {db_path}, building from embedded data")
            _build_airport_tz_database(db_path)
            # Reload from the newly built database
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT iata_code, timezone FROM airports")
            rows = cursor.fetchall()
            conn.close()
            if rows:
                _AIRPORT_TZ = {code: tz for code, tz in rows}
    except Exception as e:
        logging.warning(f"Failed to load airport timezone database: {e}, using embedded fallback")

    _AIRPORT_TZ_LOADED = True
    return _AIRPORT_TZ


# Matches 3-letter IATA airport codes embedded in any string (e.g. "LA3307 SDU → CGH → FLN").
_AIRPORT_CODE_RE = re.compile(r"\b([A-Z]{3})\b")


def _resolve_airport_timezone(airport_code: str) -> str | None:
    """Return IANA timezone for an airport code, or None if unknown.

    Handles three input formats:
      1. Plain IATA code  — e.g. ``"GRU"``
      2. Flight title     — e.g. ``"LATAM AIRLINES BRASIL SDU → CGH → FLN"``
      3. Full address     — e.g. ``"Toronto Pearson International Airport (YYZ)"``

    The function extracts the first 3-letter uppercase token that exists in our
    airport→timezone table, giving priority to codes that appear between arrows
    (→) or near the end of the string (destination airports).
    """
    if not airport_code:
        return None

    # Ensure airport timezone database is loaded
    airport_tz = _load_airport_tz_database()

    code = airport_code.strip().upper()

    # Fast path: plain IATA code lookup.
    if code in airport_tz:
        return airport_tz[code]

    # Extract all 3-letter tokens and prefer the last one (destination airport)
    # for round-trip flights where the title lists: DEP → CONNECTION1 → CONNECTION2 → ARR
    tokens = _AIRPORT_CODE_RE.findall(airport_code)
    for icao in reversed(tokens):
        tz = airport_tz.get(icao)
        if tz:
            return tz

    return None


def _resolve_gps_timezone(trip_data: dict, location_name: str) -> str | None:
    """Resolve timezone from a location name using GPS coordinates from the place database.

    Looks up ``location_name`` in the trip's assignments/places to find lat/lng, then
    uses TimezoneFinderL to return the IANA timezone.  Returns None if no coordinates
    are found.
    """
    if not location_name:
        return None
    # Normalise so lookups are case-insensitive.
    needle = location_name.strip().lower()
    # Walk places from assignments.
    for day_id, assigns in (trip_data.get("assignments") or {}).items():
        for ap in (assigns or []):
            p = ap.get("place") or {}
            name = str(p.get("name") or "").strip().lower()
            if name and name == needle:
                try:
                    lat = float(p.get("lat") or p.get("latitude") or "")
                    lng = float(p.get("lng") or p.get("lon") or p.get("longitude") or "")
                except (TypeError, ValueError):
                    lat, lng = None, None
                if lat is not None and lng is not None:
                    tz = _TF.certain_timezone_at(lat=lat, lng=lng)
                    if tz:
                        return tz
    # Walk standalone places list.
    for p in (trip_data.get("places") or []):
        name = str(p.get("name") or "").strip().lower()
        if name and name == needle:
            try:
                lat = float(p.get("lat") or p.get("latitude") or "")
                lng = float(p.get("lng") or p.get("lon") or p.get("longitude") or "")
            except (TypeError, ValueError):
                lat, lng = None, None
            if lat is not None and lng is not None:
                tz = _TF.certain_timezone_at(lat=lat, lng=lng)
                if tz:
                    return tz
    return None

# The public companion never mounts or reads TREK plugin databases at runtime.
# The bundled one-shot helper may be used manually to copy a provider key into
# the dedicated secret file before this container starts.

SHARE_CACHE_TTL = 30.0
MAX_JSON_BODY = 12 * 1024 * 1024
MAX_SESSION_BODY = 16 * 1024
PHOTO_METADATA_PREFIX_BYTES = int(os.environ.get("PHOTO_METADATA_PREFIX_BYTES", str(2 * 1024 * 1024)))
PHOTO_DATE_CACHE_TTL = float(os.environ.get("PHOTO_DATE_CACHE_TTL", str(6 * 3600)))
IMMICH_URL = os.environ.get("IMMICH_URL", "").strip().rstrip("/")
IMMICH_API_KEY = _read_secret("IMMICH_API_KEY", "IMMICH_API_KEY_FILE")
IMMICH_VERIFY_TLS = os.environ.get("IMMICH_VERIFY_TLS", "true").strip().lower() not in {"0", "false", "no", "off"}
IMMICH_TIMEOUT = float(os.environ.get("IMMICH_TIMEOUT", "15"))
IMMICH_DATE_CACHE_TTL = float(os.environ.get("IMMICH_DATE_CACHE_TTL", str(24 * 3600)))
# Provider credentials come only from a dedicated mounted secret file (or the
# explicit process environment fallback supported by _read_secret). The public
# companion never discovers or reads another TREK plugin database at runtime.
AERODATABOX_API_KEY_ENV = _read_secret("AERODATABOX_API_KEY", "AERODATABOX_API_KEY_FILE")
AERODATABOX_API_KEY_SOURCE = _secret_source("AERODATABOX_API_KEY", "AERODATABOX_API_KEY_FILE", AERODATABOX_API_KEY_ENV)
AERODATABOX_HOST = "https://aerodatabox.p.rapidapi.com"
AERODATABOX_TIMEOUT = float(os.environ.get("AERODATABOX_TIMEOUT", "10"))
AERODATABOX_MIN_INTERVAL = float(os.environ.get("AERODATABOX_MIN_INTERVAL", "1.6"))
AERODATABOX_429_RETRIES = int(os.environ.get("AERODATABOX_429_RETRIES", "2"))
AERODATABOX_429_BACKOFF = float(os.environ.get("AERODATABOX_429_BACKOFF", "2.5"))
ADSB_HOST = "https://opendata.adsb.fi/api"
ADSB_TIMEOUT = float(os.environ.get("ADSB_TIMEOUT", "8"))
LIVE_FLIGHT_MAX_CACHE = int(os.environ.get("LIVE_FLIGHT_MAX_CACHE", "256"))
GUEST_CACHE_DB = pathlib.Path(os.environ.get("GUEST_CACHE_DB", "/cache/guest-portal.db")).resolve()
GUEST_CACHE_MAX_ROWS = int(os.environ.get("GUEST_CACHE_MAX_ROWS", "512"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
LOG_FORMAT = os.environ.get("LOG_FORMAT", "kv").strip().lower() or "kv"
FULL_LOGGING = os.environ.get("FULL_LOGGING", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_STATIC_REQUESTS = os.environ.get("LOG_STATIC_REQUESTS", "true").strip().lower() in {"1", "true", "yes", "on"}
LOG_SAFE_REQUEST_HEADERS = os.environ.get("LOG_SAFE_REQUEST_HEADERS", "true").strip().lower() in {"1", "true", "yes", "on"}
CLIENT_EVENT_LOGGING = os.environ.get("CLIENT_EVENT_LOGGING", "true").strip().lower() in {"1", "true", "yes", "on"}
CLIENT_EVENT_RATE_PER_MINUTE = max(30, min(int(os.environ.get("CLIENT_EVENT_RATE_PER_MINUTE", "240")), 2000))
CLIENT_EVENT_MAX_BODY = max(1024, min(int(os.environ.get("CLIENT_EVENT_MAX_BODY", "8192")), 65536))
LOG_HEARTBEAT_SECONDS = max(60, min(int(os.environ.get("LOG_HEARTBEAT_SECONDS", "300")), 3600))
LOG_CLIENT_IP = os.environ.get("LOG_CLIENT_IP", "false").strip().lower() in {"1", "true", "yes", "on"}
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "false").strip().lower() in {"1", "true", "yes", "on"}
CLIENT_IP_HEADER = os.environ.get("CLIENT_IP_HEADER", "X-Guest-Client-IP").strip() or "X-Guest-Client-IP"
TRUSTED_PROXY_CIDRS_RAW = os.environ.get("TRUSTED_PROXY_CIDRS", "").strip()
LOG_PROXY_DETAILS = os.environ.get("LOG_PROXY_DETAILS", "true").strip().lower() in {"1", "true", "yes", "on"}
FLIGHT_API_WINDOW_HOURS = max(1.0, min(float(os.environ.get("FLIGHT_API_WINDOW_HOURS", "48")), 168.0))
FLIGHT_UPCOMING_POLL_SECONDS = max(60, min(int(os.environ.get("FLIGHT_UPCOMING_POLL_SECONDS", "600")), 3600))
FLIGHT_ACTIVE_POLL_SECONDS = max(30, min(int(os.environ.get("FLIGHT_ACTIVE_POLL_SECONDS", "60")), 600))
FLIGHT_ERROR_POLL_SECONDS = max(60, min(int(os.environ.get("FLIGHT_ERROR_POLL_SECONDS", "300")), 3600))

VERSION = "3.4.5"
PRODID = "-//TREK Guest Portal//NONSGML v3.3.12//EN"

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,256}$")
RID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_share_cache: dict[str, tuple[float, dict]] = {}
_share_cache_lock = threading.Lock()
_photo_date_cache: dict[tuple[str, str], tuple[float, str | None]] = {}
_photo_date_cache_lock = threading.Lock()
_immich_date_cache: dict[str, tuple[float, str | None]] = {}
_immich_date_cache_lock = threading.Lock()
_live_flight_cache: dict[tuple[str, str], tuple[int, dict]] = {}
_live_flight_cache_lock = threading.Lock()
_live_refresh_locks: dict[tuple[str, str], threading.Lock] = {}
_live_refresh_locks_guard = threading.Lock()
_aero_rate_lock = threading.Lock()
_last_aero_request = 0.0
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()
_session_create_times: list[float] = []
_session_rate_lock = threading.Lock()
_client_event_times: dict[str, list[float]] = {}
_client_event_rate_lock = threading.Lock()

_log_context = threading.local()
_metrics_lock = threading.Lock()
_metrics = {
    "http_requests": 0, "http_responses": 0, "client_events": 0,
    "sessions_created": 0, "aerodatabox_calls": 0, "adsb_calls": 0,
    "immich_calls": 0, "trek_calls": 0, "warnings": 0, "errors": 0,
}
_started_monotonic = time.monotonic()


def _metric(name: str, amount: int = 1):
    """Increment a named in-process runtime counter under the metrics lock."""
    with _metrics_lock:
        _metrics[name] = int(_metrics.get(name, 0)) + amount


def _metrics_snapshot() -> dict:
    """Return a consistent copy of the current runtime counters."""
    with _metrics_lock:
        return dict(_metrics)


def _parse_trusted_proxy_networks(raw: str):
    """Parse configured trusted proxy CIDRs, ignoring malformed entries fail-closed."""
    networks = []
    for part in re.split(r"[\s,]+", raw or ""):
        value = part.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            # Fail closed: malformed entries are ignored and reported at startup.
            pass
    return tuple(networks)


_TRUSTED_PROXY_NETWORKS = _parse_trusted_proxy_networks(TRUSTED_PROXY_CIDRS_RAW)


def _valid_ip(value: str | None) -> str | None:
    """Normalize a single IP address value and reject lists or malformed input."""
    if not value:
        return None
    candidate = str(value).strip()
    # The application-specific proxy header must contain exactly one address.
    if not candidate or "," in candidate or len(candidate) > 128:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _peer_is_trusted(peer: str | None) -> bool:
    """Return whether the immediate TCP peer belongs to a configured trusted proxy network."""
    try:
        addr = ipaddress.ip_address(str(peer or "").strip())
    except ValueError:
        return False
    return any(addr in network for network in _TRUSTED_PROXY_NETWORKS)


def _resolved_client(handler):
    """Return (client_ip, source, peer_ip).

    Forwarded client identity is accepted only from an explicitly trusted TCP
    peer. The recommended Apache configuration populates X-Guest-Client-IP from
    Apache's post-mod_remoteip REMOTE_ADDR, producing the correct address for
    both Cloudflare traffic and local split-DNS traffic.
    """
    try:
        peer = _valid_ip(handler.client_address[0]) or str(handler.client_address[0])
    except Exception:
        peer = "unknown"
    if TRUST_PROXY_HEADERS and _peer_is_trusted(peer):
        forwarded = _valid_ip(handler.headers.get(CLIENT_IP_HEADER))
        if forwarded:
            return forwarded, "trusted-proxy-header", peer
        return peer, "trusted-proxy-missing-header", peer
    if TRUST_PROXY_HEADERS and _TRUSTED_PROXY_NETWORKS:
        return peer, "untrusted-peer", peer
    return peer, "socket", peer


_logger = logging.getLogger("trek-guest-portal")
_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
_logger.handlers.clear()
_logger.addHandler(_handler)
_logger.propagate = False


def _token_ref(value: str | None) -> str:
    """Return a short irreversible fingerprint for safe token correlation in logs."""
    if not value:
        return "none"
    return hashlib.sha256(str(value).encode("utf-8", "ignore")).hexdigest()[:10]


def _safe_request_target(raw_target: str) -> str:
    """Redact native TREK/Journey share tokens from access logs."""
    try:
        parsed = urlsplit(raw_target)
        path = parsed.path
        path = re.sub(r"(/api/shared/)([^/?]+)", lambda m: m.group(1) + "<token:" + _token_ref(unquote(m.group(2))) + ">", path)
        path = re.sub(r"(/api/public/journey/)([^/?]+)", lambda m: m.group(1) + "<token:" + _token_ref(unquote(m.group(2))) + ">", path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        safe_parts = []
        for key, values in query.items():
            for value in values:
                if key.lower() in {"trip", "journey", "token"}:
                    value = "<token:" + _token_ref(value) + ">"
                safe_parts.append(f"{quote(str(key), safe='')}={quote(str(value), safe='<>:')}" )
        return path + (("?" + "&".join(safe_parts)) if safe_parts else "")
    except Exception:
        return "<unparseable-request>"


def log_event(level: int, event: str, **fields):
    """Emit a structured operational event without secrets.

    kv is human-friendly for `docker logs`; json is useful for log shippers.
    Callers must never pass raw bearer tokens, API keys, cookies or passwords.
    """
    if level >= logging.ERROR:
        _metric("errors")
    elif level >= logging.WARNING:
        _metric("warnings")
    if event == "aerodatabox.request": _metric("aerodatabox_calls")
    elif event.startswith("adsb.request"): _metric("adsb_calls")
    elif event == "immich.asset_lookup_start": _metric("immich_calls")
    elif event == "trek.upstream_start": _metric("trek_calls")
    context = getattr(_log_context, "fields", None) or {}
    merged = dict(context)
    merged.update(fields)
    if LOG_FORMAT == "json":
        payload = {"event": event, **merged}
        # The logging formatter already supplies timestamp/severity externally.
        _logger.log(level, json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
        return
    parts = [event]
    for key, value in merged.items():
        if value is None or value == "":
            continue
        text = str(value).replace("\n", " ").replace("\r", " ")
        if len(text) > 500:
            text = text[:497] + "..."
        if re.search(r"\s|=|\"", text):
            text = json.dumps(text, ensure_ascii=False)
        parts.append(f"{key}={text}")
    _logger.log(level, " ".join(parts))


def _begin_log_context(handler, target: str) -> None:
    """Initialize per-request correlation, client identity and request-start logging."""
    _metric("http_requests")
    request_id = secrets.token_hex(4)
    fields = {"req": request_id}
    client_ip, client_source, peer_ip = _resolved_client(handler)
    handler._resolved_client_ip = client_ip
    handler._client_ip_source = client_source
    handler._proxy_peer_ip = peer_ip
    if LOG_CLIENT_IP:
        fields["client"] = client_ip
        fields["client_source"] = client_source
        if LOG_PROXY_DETAILS and peer_ip != client_ip:
            fields["proxy_peer"] = peer_ip
        if LOG_PROXY_DETAILS:
            cf_ray = (handler.headers.get("CF-Ray") or "").strip()[:96]
            cf_country = (handler.headers.get("CF-IPCountry") or "").strip()[:8]
            if cf_ray:
                fields["cf_ray"] = cf_ray
            if cf_country:
                fields["cf_country"] = cf_country
    _log_context.fields = fields
    handler._request_id = request_id
    handler._request_started = time.monotonic()
    path = urlsplit(target).path
    level = logging.INFO if path.startswith("/api/") else logging.DEBUG
    ua = (handler.headers.get("User-Agent") or "")[:160]
    extra = {}
    if FULL_LOGGING and LOG_SAFE_REQUEST_HEADERS:
        extra = {
            "host": (handler.headers.get("Host") or "")[:160] or "none",
            "accept": (handler.headers.get("Accept") or "")[:160] or "none",
            "accept_language": (handler.headers.get("Accept-Language") or "")[:96] or "none",
            "sec_fetch_site": (handler.headers.get("Sec-Fetch-Site") or "")[:32] or "none",
            "sec_fetch_mode": (handler.headers.get("Sec-Fetch-Mode") or "")[:32] or "none",
            "sec_fetch_dest": (handler.headers.get("Sec-Fetch-Dest") or "")[:32] or "none",
        }
    log_event(level, "http.request_start", method=handler.command, target=_safe_request_target(target), user_agent=ua or "none", **extra)


HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


def json_bytes(obj) -> bytes:
    """Serialize an object to compact UTF-8 JSON bytes."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


_guest_cache_ready = False
_guest_cache_error = "not-initialized"
_guest_cache_lock = threading.Lock()


def init_guest_cache_db() -> bool:
    """Initialize Guest Portal's own persistent SQLite cache.

    Failure is non-fatal: the portal continues using its bounded in-memory
    live-flight cache, but loses cross-restart cache persistence.
    """
    global _guest_cache_ready, _guest_cache_error
    try:
        GUEST_CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(GUEST_CACHE_DB.parent, 0o700)
        except OSError:
            pass
        with sqlite3.connect(GUEST_CACHE_DB, timeout=5) as con:
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("""
                CREATE TABLE IF NOT EXISTS live_flight_cache (
                    trip_id TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    fetched_at INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (trip_id, reservation_id)
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_live_flight_cache_fetched ON live_flight_cache(fetched_at)")
            con.commit()
        try:
            os.chmod(GUEST_CACHE_DB, 0o600)
        except OSError:
            pass
        _guest_cache_ready = True
        _guest_cache_error = ""
        log_event(logging.INFO, "cache.persistent_ready", db=GUEST_CACHE_DB)
        return True
    except Exception as exc:
        _guest_cache_ready = False
        _guest_cache_error = str(exc)
        log_event(logging.WARNING, "cache.persistent_unavailable", db=GUEST_CACHE_DB, error=str(exc))
        return False


def persistent_cache_status() -> dict:
    """Return public-safe status information for the Guest Portal SQLite cache."""
    return {
        "enabled": bool(_guest_cache_ready),
        "path": str(GUEST_CACHE_DB),
        "error": _guest_cache_error or None,
    }


def read_persistent_live_cache(trip_id: str, reservation_id: str) -> tuple[int, dict] | None:
    """Read one live-flight payload from the Guest Portal-owned persistent cache."""
    if not _guest_cache_ready:
        return None
    try:
        with _guest_cache_lock:
            with sqlite3.connect(GUEST_CACHE_DB, timeout=5) as con:
                con.row_factory = sqlite3.Row
                con.execute("PRAGMA busy_timeout=5000")
                row = con.execute(
                    "SELECT fetched_at, payload FROM live_flight_cache WHERE trip_id = ? AND reservation_id = ? LIMIT 1",
                    (str(trip_id), str(reservation_id)),
                ).fetchone()
        if not row:
            log_event(logging.DEBUG, "cache.persistent_miss", trip_id=trip_id, reservation_id=reservation_id)
            return None
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict):
            return None
        fetched_at = int(row["fetched_at"])
        log_event(logging.INFO, "cache.persistent_hit", trip_id=trip_id, reservation_id=reservation_id, fetched_at=fetched_at)
        return fetched_at, payload
    except Exception as exc:
        log_event(logging.WARNING, "cache.persistent_read_failed", trip_id=trip_id, reservation_id=reservation_id, error=str(exc))
        return None


def write_persistent_live_cache(trip_id: str, reservation_id: str, fetched_at: int, payload: dict) -> None:
    """Persist one live-flight payload and enforce the configured row bound."""
    if not _guest_cache_ready:
        return
    try:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with _guest_cache_lock:
            with sqlite3.connect(GUEST_CACHE_DB, timeout=5) as con:
                con.execute("PRAGMA busy_timeout=5000")
                con.execute(
                    """INSERT INTO live_flight_cache(trip_id, reservation_id, fetched_at, payload)
                       VALUES(?, ?, ?, ?)
                       ON CONFLICT(trip_id, reservation_id) DO UPDATE SET
                         fetched_at=excluded.fetched_at, payload=excluded.payload""",
                    (str(trip_id), str(reservation_id), int(fetched_at), body),
                )
                if GUEST_CACHE_MAX_ROWS > 0:
                    count = con.execute("SELECT COUNT(*) FROM live_flight_cache").fetchone()[0]
                    excess = int(count) - GUEST_CACHE_MAX_ROWS
                    if excess > 0:
                        con.execute(
                            "DELETE FROM live_flight_cache WHERE rowid IN (SELECT rowid FROM live_flight_cache ORDER BY fetched_at ASC LIMIT ?)",
                            (excess,),
                        )
                con.commit()
        log_event(logging.INFO, "cache.persistent_write", trip_id=trip_id, reservation_id=reservation_id, fetched_at=fetched_at)
    except Exception as exc:
        log_event(logging.WARNING, "cache.persistent_write_failed", trip_id=trip_id, reservation_id=reservation_id, error=str(exc))


def _decorate_cached_payload(fetched_ms: int, payload: dict, trip_id: str, reservation_id: str, source_event: str) -> dict | None:
    """Return a cache row only while its provider data is still valid."""
    live = payload.get("_guestLive") if isinstance(payload, dict) and isinstance(payload.get("_guestLive"), dict) else {}
    api_fetched_at = live.get("apiFetchedAt")
    if not api_fetched_at:
        src = str(live.get("source") or "")
        if "AeroDataBox" in src or payload.get("source") == "guest-live":
            api_fetched_at = int(fetched_ms)
    plan = _flight_runtime_plan(payload, int(api_fetched_at) if api_fetched_at else None)

    # Once the live-data window is open, a row without an actual provider fetch
    # cannot mask the first AeroDataBox lookup. Likewise, an expired provider TTL
    # triggers a refresh even though the browser may be checking more frequently.
    if plan["apiWindowOpen"] and plan["pollAfterSeconds"] > 0:
        if not api_fetched_at:
            _log_flight_plan("flight.cache_rejected", plan, trip_id, reservation_id,
                             "no-provider-fetch", cache_source=source_event)
            return None
        if plan["apiRefreshAfterSeconds"] is not None and plan["apiRefreshAfterSeconds"] <= 0:
            _log_flight_plan("flight.cache_rejected", plan, trip_id, reservation_id,
                             "provider-ttl-expired", cache_source=source_event)
            return None

    out = _apply_flight_runtime_metadata(payload, int(fetched_ms), None)
    out.setdefault("_guestLive", {}).setdefault("fetchedAt", int(fetched_ms))
    plan = _flight_runtime_plan(out, int(out.get("_guestLive", {}).get("apiFetchedAt") or 0) or None)
    _log_flight_plan(source_event, plan, trip_id, reservation_id, "serve-cache",
                     cache_source=source_event, fetched_at=fetched_ms)
    return out


def upstream_get(path: str, incoming_host: str = "") -> tuple[int, list[tuple[str, str]], bytes]:
    """Fetch a bounded anonymous TREK upstream response with sanitized logging."""
    conn = http.client.HTTPConnection(TREK_HOST, TREK_PORT, timeout=UPSTREAM_TIMEOUT)
    headers = {
        "Accept": "application/json,*/*;q=0.8",
        "Host": incoming_host or TREK_HOST,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": incoming_host or TREK_HOST,
    }
    started = time.monotonic()
    safe_target = _safe_request_target(path)
    log_event(logging.DEBUG, "trek.upstream_start", method="GET", target=safe_target, host=TREK_HOST, port=TREK_PORT)
    try:
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        body = res.read(MAX_JSON_BODY + 1)
        if len(body) > MAX_JSON_BODY:
            raise RuntimeError("TREK response too large")
        log_event(logging.INFO if 200 <= res.status < 400 else logging.WARNING,
                  "trek.upstream_response", target=safe_target, status=res.status,
                  bytes=len(body), elapsed_ms=int((time.monotonic() - started) * 1000))
        return res.status, list(res.getheaders()), body
    except Exception as exc:
        log_event(logging.ERROR, "trek.upstream_failed", target=safe_target,
                  error=str(exc), exc_type=type(exc).__name__,
                  elapsed_ms=int((time.monotonic() - started) * 1000))
        raise
    finally:
        conn.close()


def upstream_read_prefix(path: str, limit: int, incoming_host: str = "") -> tuple[int, str, bytes]:
    """Read only the first *limit* bytes of an upstream response."""
    conn = http.client.HTTPConnection(TREK_HOST, TREK_PORT, timeout=UPSTREAM_TIMEOUT)
    headers = {
        "Accept": "image/*,*/*;q=0.8",
        "Host": incoming_host or TREK_HOST,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": incoming_host or TREK_HOST,
        "Range": f"bytes=0-{max(0, limit - 1)}",
    }
    started = time.monotonic()
    safe_target = _safe_request_target(path)
    log_event(logging.DEBUG, "trek.media_prefix_start", target=safe_target, limit=limit)
    try:
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        body = res.read(limit)
        ctype = res.getheader("Content-Type", "")
        log_event(logging.DEBUG if res.status in (200, 206) else logging.WARNING,
                  "trek.media_prefix_response", target=safe_target, status=res.status,
                  content_type=ctype or "unknown", bytes=len(body),
                  elapsed_ms=int((time.monotonic() - started) * 1000))
        return res.status, ctype, body
    except Exception as exc:
        log_event(logging.WARNING, "trek.media_prefix_failed", target=safe_target,
                  error=str(exc), exc_type=type(exc).__name__,
                  elapsed_ms=int((time.monotonic() - started) * 1000))
        raise
    finally:
        conn.close()


def _embedded_date_key(value: str) -> str | None:
    """Normalize an embedded EXIF/XMP date string to a calendar date."""
    value = (value or "").strip().replace("\x00", "")
    # EXIF commonly uses YYYY:MM:DD HH:MM:SS; XMP commonly uses ISO dates.
    match = re.search(r"(19\d{2}|20\d{2})[-:](0[1-9]|1[0-2])[-:](0[1-9]|[12]\d|3[01])", value)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _extract_tiff_capture_date(data: bytes, tiff_start: int) -> str | None:
    """Minimal, bounded TIFF/EXIF parser for capture dates."""
    if tiff_start < 0 or tiff_start + 8 > len(data):
        return None
    order = data[tiff_start:tiff_start + 2]
    if order == b"II":
        endian = "little"
    elif order == b"MM":
        endian = "big"
    else:
        return None

    def u16(pos: int) -> int | None:
        """Read a TIFF-endian unsigned 16-bit value when the requested bytes are in range."""
        if pos < 0 or pos + 2 > len(data): return None
        return int.from_bytes(data[pos:pos + 2], endian)

    def u32(pos: int) -> int | None:
        """Read a TIFF-endian unsigned 32-bit value when the requested bytes are in range."""
        if pos < 0 or pos + 4 > len(data): return None
        return int.from_bytes(data[pos:pos + 4], endian)

    if u16(tiff_start + 2) != 42:
        return None
    first_ifd = u32(tiff_start + 4)
    if first_ifd is None:
        return None

    seen: set[int] = set()

    def ascii_value(entry_pos: int, count: int, value_or_offset: int) -> str | None:
        """Read a bounded TIFF ASCII field from an inline value or referenced offset."""
        if count <= 0 or count > 512:
            return None
        if count <= 4:
            raw = data[entry_pos + 8: entry_pos + 8 + count]
        else:
            start = tiff_start + value_or_offset
            if start < tiff_start or start + count > len(data):
                return None
            raw = data[start:start + count]
        try:
            return raw.split(b"\x00", 1)[0].decode("ascii", "ignore")
        except Exception:
            return None

    def parse_ifd(relative_offset: int, depth: int = 0) -> str | None:
        """Walk a bounded TIFF IFD tree looking for original capture-date tags."""
        if depth > 3 or relative_offset in seen:
            return None
        seen.add(relative_offset)
        pos = tiff_start + relative_offset
        count = u16(pos)
        if count is None or count > 1024:
            return None
        exif_ifd: int | None = None
        fallback: str | None = None
        for i in range(count):
            ep = pos + 2 + i * 12
            if ep + 12 > len(data):
                break
            tag = u16(ep)
            typ = u16(ep + 2)
            n = u32(ep + 4)
            val = u32(ep + 8)
            if None in (tag, typ, n, val):
                continue
            if tag == 0x8769 and typ in (3, 4):  # ExifIFDPointer
                exif_ifd = val
            elif tag in (0x9003, 0x9004, 0x0132) and typ == 2:  # DateTimeOriginal/Digitized/DateTime
                parsed = _embedded_date_key(ascii_value(ep, n, val) or "")
                if parsed:
                    if tag in (0x9003, 0x9004):
                        return parsed
                    fallback = fallback or parsed
        if exif_ifd is not None:
            nested = parse_ifd(exif_ifd, depth + 1)
            if nested:
                return nested
        return fallback

    return parse_ifd(first_ifd)


def extract_embedded_capture_date(data: bytes) -> str | None:
    """Extract a best-effort capture date from JPEG/TIFF/EXIF/XMP bytes."""
    if not data:
        return None

    # JPEG APP1 EXIF segment(s).
    if data.startswith(b"\xff\xd8"):
        pos = 2
        while pos + 4 <= len(data):
            if data[pos] != 0xFF:
                pos += 1
                continue
            marker = data[pos + 1]
            pos += 2
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            if pos + 2 > len(data):
                break
            seg_len = int.from_bytes(data[pos:pos + 2], "big")
            if seg_len < 2 or pos + seg_len > len(data):
                break
            payload = pos + 2
            if marker == 0xE1 and data[payload:payload + 6] == b"Exif\x00\x00":
                found = _extract_tiff_capture_date(data, payload + 6)
                if found:
                    return found
            pos += seg_len

    # Some containers (PNG/HEIC) still carry a contiguous EXIF/TIFF payload.
    for sig in (b"Exif\x00\x00II*\x00", b"Exif\x00\x00MM\x00*", b"II*\x00", b"MM\x00*"):
        start = data.find(sig)
        if start >= 0:
            if sig.startswith(b"Exif"):
                start += 6
            found = _extract_tiff_capture_date(data, start)
            if found:
                return found

    # XMP is common in exported/edited images and may survive when EXIF does not.
    sample = data.decode("latin1", "ignore")
    patterns = (
        r"(?:DateTimeOriginal|DateTimeDigitized|CreateDate|DateCreated)[^0-9]{0,100}((?:19|20)\d{2}[-:]\d{2}[-:]\d{2})",
        r"((?:19|20)\d{2}[-:]\d{2}[-:]\d{2})[^\n\r]{0,80}(?:DateTimeOriginal|CreateDate)",
    )
    for pattern in patterns:
        m = re.search(pattern, sample, re.IGNORECASE)
        if m:
            found = _embedded_date_key(m.group(1))
            if found:
                return found
    return None


def immich_configured() -> bool:
    """Return whether both the Immich URL and API key are configured."""
    return bool(IMMICH_URL and IMMICH_API_KEY)


def _immich_api_base() -> str:
    """Return the normalized Immich API base URL."""
    base = IMMICH_URL.rstrip("/")
    if base.lower().endswith("/api"):
        return base
    return base + "/api"


def _capture_date_key(value) -> str | None:
    """Return YYYY-MM-DD from Immich/EXIF date values without timezone shifting."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Immich timestamps are ISO-8601; EXIF may use YYYY:MM:DD HH:MM:SS.
    match = re.search(r"((?:19|20)\d{2})[-:](0[1-9]|1[0-2])[-:](0[1-9]|[12]\d|3[01])", text)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _immich_date_from_asset(asset: dict) -> str | None:
    """Select the best original-capture date from an Immich asset response."""
    if not isinstance(asset, dict):
        return None
    exif = asset.get("exifInfo") or {}
    candidates = []
    if isinstance(exif, dict):
        candidates.extend([
            exif.get("dateTimeOriginal"),
            exif.get("dateTimeDigitized"),
        ])
    # localDateTime/fileCreatedAt are the timeline/taken timestamps exposed by
    # current Immich AssetResponseDto. Prefer them over upload-createdAt.
    candidates.extend([
        asset.get("localDateTime"),
        asset.get("fileCreatedAt"),
    ])
    for value in candidates:
        parsed = _capture_date_key(value)
        if parsed:
            return parsed
    return None


def get_immich_asset_capture_date(asset_id: str) -> str | None:
    """Resolve and cache an Immich asset capture date without exposing the API key."""
    if not immich_configured() or not asset_id:
        return None
    now = time.monotonic()
    with _immich_date_cache_lock:
        cached = _immich_date_cache.get(asset_id)
        if cached and now - cached[0] <= IMMICH_DATE_CACHE_TTL:
            log_event(logging.DEBUG, "immich.date_cache_hit", asset=_token_ref(asset_id), date=cached[1] or "none")
            return cached[1]

    url = f"{_immich_api_base()}/assets/{quote(asset_id, safe='')}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "x-api-key": IMMICH_API_KEY,
            "User-Agent": f"TREK-Guest-Portal/{VERSION}",
        },
    )
    context = None
    if url.lower().startswith("https://"):
        context = ssl.create_default_context() if IMMICH_VERIFY_TLS else ssl._create_unverified_context()
    result = None
    started = time.monotonic()
    log_event(logging.DEBUG, "immich.asset_lookup_start", asset=_token_ref(asset_id))
    try:
        with urllib.request.urlopen(req, timeout=IMMICH_TIMEOUT, context=context) as res:
            raw = res.read(2 * 1024 * 1024)
            http_status = int(getattr(res, "status", 200) or 200)
        payload = json.loads(raw.decode("utf-8"))
        result = _immich_date_from_asset(payload)
        log_event(logging.INFO if result else logging.WARNING, "immich.asset_lookup", asset=_token_ref(asset_id), status=http_status, date=result or "not-found", elapsed_ms=int((time.monotonic()-started)*1000))
    except urllib.error.HTTPError as exc:
        result = None
        log_event(logging.WARNING, "immich.asset_lookup_failed", asset=_token_ref(asset_id), status=exc.code, error=str(exc.reason or exc), elapsed_ms=int((time.monotonic()-started)*1000))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError, OSError) as exc:
        result = None
        log_event(logging.WARNING, "immich.asset_lookup_failed", asset=_token_ref(asset_id), error=str(exc), elapsed_ms=int((time.monotonic()-started)*1000))

    with _immich_date_cache_lock:
        if len(_immich_date_cache) > 4096:
            oldest = sorted(_immich_date_cache.items(), key=lambda kv: kv[1][0])[:1024]
            for old_key, _ in oldest:
                _immich_date_cache.pop(old_key, None)
        _immich_date_cache[asset_id] = (now, result)
    return result


def get_photo_capture_date(token: str, photo_id: str, incoming_host: str = "") -> str | None:
    """Resolve and cache an embedded capture date from authorized Journey media."""
    key = (token, photo_id)
    now = time.monotonic()
    with _photo_date_cache_lock:
        cached = _photo_date_cache.get(key)
        if cached and now - cached[0] <= PHOTO_DATE_CACHE_TTL:
            log_event(logging.DEBUG, "photo.embedded_cache_hit", journey=_token_ref(token), photo_id=photo_id, date=cached[1] or "none")
            return cached[1]

    status, _ctype, prefix = upstream_read_prefix(
        f"/api/public/journey/{quote(token, safe='')}/photos/{quote(photo_id, safe='')}/original",
        PHOTO_METADATA_PREFIX_BYTES,
        incoming_host,
    )
    result = extract_embedded_capture_date(prefix) if status in (200, 206) else None
    log_event(logging.DEBUG, "photo.embedded_lookup", journey=_token_ref(token), photo_id=photo_id, upstream_status=status, date=result or "not-found")
    with _photo_date_cache_lock:
        if len(_photo_date_cache) > 1024:
            oldest = sorted(_photo_date_cache.items(), key=lambda kv: kv[1][0])[:256]
            for old_key, _ in oldest:
                _photo_date_cache.pop(old_key, None)
        _photo_date_cache[key] = (now, result)
    return result


_GUEST_CONFIRMATION_NORMALIZED_KEYS = {
    "confirmation", "confirmationcode", "confirmationnumber", "confirmationid",
    "bookingreference", "bookingreferencecode", "bookingreferencenumber",
    "reservationreference", "reservationreferencecode", "reservationreferencenumber",
    "referencenumber", "referencecode",
}


def _is_guest_confirmation_key(key: object, *, strip_plain_reference: bool = False) -> bool:
    """Return whether a shared-trip field can reveal a booking confirmation/reference."""
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    if "confirmation" in normalized or normalized in _GUEST_CONFIRMATION_NORMALIZED_KEYS:
        return True
    if normalized.startswith("bookingreference") or normalized.startswith("reservationreference"):
        return True
    return strip_plain_reference and normalized == "reference"


def _strip_guest_confirmation_fields(value, *, strip_plain_reference: bool = False):
    """Deep-copy shared booking data while removing guest-visible confirmation identifiers.

    TREK may place reservation metadata in either normal JSON objects or a JSON-encoded
    metadata string. Both shapes are sanitized so confirmation/reference values never
    cross the Guest Portal API boundary. The cached/native TREK payload remains intact
    server-side for provider integrations that may need the original reservation data.
    """
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if _is_guest_confirmation_key(key, strip_plain_reference=strip_plain_reference):
                continue
            out[key] = _strip_guest_confirmation_fields(item, strip_plain_reference=strip_plain_reference)
        return out
    if isinstance(value, list):
        return [_strip_guest_confirmation_fields(item, strip_plain_reference=strip_plain_reference) for item in value]
    if isinstance(value, str) and strip_plain_reference:
        candidate = value.strip()
        if candidate.startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except Exception:
                return value
            if isinstance(decoded, (dict, list)):
                sanitized = _strip_guest_confirmation_fields(decoded, strip_plain_reference=True)
                return json.dumps(sanitized, separators=(",", ":"), ensure_ascii=False)
    return value


def _guest_trip_payload(data: dict) -> dict:
    """Build the browser-facing trip payload with confirmation information removed."""
    sanitized = _strip_guest_confirmation_fields(data, strip_plain_reference=False)
    if not isinstance(sanitized, dict):
        return {}
    # A plain ``reference`` field is ambiguous elsewhere in TREK, but within
    # reservations/accommodations it is a confirmation-style identifier and is
    # intentionally removed from the anonymous guest response.
    for collection in ("reservations", "accommodations"):
        original_items = data.get(collection) if isinstance(data, dict) else None
        if isinstance(original_items, list):
            sanitized[collection] = [
                _strip_guest_confirmation_fields(item, strip_plain_reference=True)
                for item in original_items
            ]
    return sanitized


def get_shared_trip(token: str, incoming_host: str = "") -> dict:
    """Fetch and briefly cache a native TREK public trip share."""
    now = time.monotonic()
    with _share_cache_lock:
        cached = _share_cache.get(token)
        if cached and now - cached[0] <= SHARE_CACHE_TTL:
            log_event(logging.DEBUG, "trek.share_cache_hit", trip_share=_token_ref(token))
            return cached[1]

    started = time.monotonic()
    status, _headers, body = upstream_get(f"/api/shared/{quote(token, safe='')}", incoming_host)
    log_event(logging.INFO if status == 200 else logging.WARNING, "trek.share_fetch", trip_share=_token_ref(token), status=status, elapsed_ms=int((time.monotonic()-started)*1000))
    if status != 200:
        raise LookupError(f"TREK share returned {status}")
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("TREK share returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("TREK share returned an unexpected response")

    with _share_cache_lock:
        if len(_share_cache) > 128:
            oldest = sorted(_share_cache.items(), key=lambda kv: kv[1][0])[:32]
            for key, _ in oldest:
                _share_cache.pop(key, None)
        _share_cache[token] = (now, data)

    return data


def get_shared_journey(token: str, incoming_host: str = "") -> dict:
    """Fetch a native TREK public Journey share."""
    started = time.monotonic()
    status, _headers, body = upstream_get(f"/api/public/journey/{quote(token, safe='')}", incoming_host)
    log_event(logging.INFO if status == 200 else logging.WARNING, "trek.journey_fetch",
              journey_share=_token_ref(token), status=status, bytes=len(body),
              elapsed_ms=int((time.monotonic() - started) * 1000))
    if status != 200:
        raise LookupError(f"Journey share returned {status}")
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("Journey share returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Journey share returned an unexpected response")
    return data


def _https_origin_valid(value: str) -> bool:
    """Validate an exact HTTPS origin with no path, query or fragment."""
    try:
        u = urlsplit(value)
        return u.scheme == "https" and bool(u.netloc) and not u.path.rstrip("/") and not u.query and not u.fragment
    except Exception:
        return False


def _public_origin_valid() -> bool:
    """Validate the configured public Guest Portal origin."""
    return _https_origin_valid(PUBLIC_ORIGIN)


def _upstream_host_header() -> str:
    """Return the TREK public host name used for upstream share validation."""
    try:
        return urlsplit(TREK_PUBLIC_ORIGIN).netloc or TREK_HOST
    except Exception:
        return TREK_HOST


def _origin_allowed(origin: str | None) -> bool:
    """Return whether a browser Origin header exactly matches PUBLIC_ORIGIN."""
    return bool(origin) and origin.rstrip("/") == PUBLIC_ORIGIN


def _session_cleanup(now: float | None = None) -> None:
    """Remove expired or excess in-memory guest session state."""
    now = time.time() if now is None else now
    with _sessions_lock:
        expired = []
        if SESSION_TTL_SECONDS > 0:
            expired = [
                sid for sid, item in _sessions.items()
                if item.get("expires") is not None and float(item.get("expires", 0)) <= now
            ]
            for sid in expired:
                _sessions.pop(sid, None)
            if expired and FULL_LOGGING:
                log_event(logging.INFO, "session.cleanup", expired=len(expired), remaining=len(_sessions))
        if len(_sessions) > SESSION_MAX:
            remove_count = len(_sessions) - SESSION_MAX
            oldest = sorted(_sessions.items(), key=lambda kv: float(kv[1].get("created", 0)))[:remove_count]
            for sid, _ in oldest:
                _sessions.pop(sid, None)


def _session_rate_allowed() -> bool:
    """Apply the global rolling session-creation rate limit."""
    now = time.monotonic()
    with _session_rate_lock:
        cutoff = now - 60.0
        _session_create_times[:] = [t for t in _session_create_times if t >= cutoff]
        if len(_session_create_times) >= SESSION_CREATE_PER_MINUTE:
            return False
        _session_create_times.append(now)
        return True


def _client_event_rate_allowed(session_ref: str) -> bool:
    """Apply the rolling per-session browser telemetry rate limit."""
    now = time.monotonic()
    with _client_event_rate_lock:
        cutoff = now - 60.0
        bucket = [t for t in _client_event_times.get(session_ref, []) if t >= cutoff]
        if len(bucket) >= CLIENT_EVENT_RATE_PER_MINUTE:
            _client_event_times[session_ref] = bucket
            return False
        bucket.append(now)
        _client_event_times[session_ref] = bucket
        if len(_client_event_times) > SESSION_MAX * 2:
            for key in list(_client_event_times)[: max(1, len(_client_event_times) // 4)]:
                _client_event_times.pop(key, None)
        return True


_CLIENT_EVENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_CLIENT_FIELD_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,47}$")
_CLIENT_SENSITIVE_RE = re.compile(r"token|secret|password|passwd|api.?key|authorization|cookie|session|confirmation|email|phone", re.I)


def _sanitize_client_fields(raw) -> dict:
    """Filter, bound and normalize browser telemetry fields before logging."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in list(raw.items())[:32]:
        name = str(key)
        if not _CLIENT_FIELD_RE.fullmatch(name) or _CLIENT_SENSITIVE_RE.search(name):
            continue
        if isinstance(value, bool) or value is None:
            out[name] = value
        elif isinstance(value, (int, float)):
            out[name] = value
        elif isinstance(value, str):
            text = value.replace("\n", " ").replace("\r", " ")[:240]
            # Never accept URL fragments from browser telemetry.
            if "#" in text:
                text = text.split("#", 1)[0]
            out[name] = text
        elif isinstance(value, (list, tuple)):
            out[name] = ",".join(str(x)[:40] for x in list(value)[:8])[:240]
    return out


def _new_session(trip_token: str, journey_token: str, title: str, trip_id: str | None, enable_ical: bool = False) -> tuple[str, dict]:
    """Create and store a random memory-only guest session."""
    _session_cleanup()
    sid = secrets.token_urlsafe(32)
    now = time.time()
    item = {
        "created": now,
        "expires": (now + SESSION_TTL_SECONDS) if SESSION_TTL_SECONDS > 0 else None,
        "trip": trip_token,
        "journey": journey_token,
        "title": title[:160],
        "trip_id": None if trip_id is None else str(trip_id),
        "ical": bool(enable_ical),
    }
    with _sessions_lock:
        _sessions[sid] = item
    return sid, item


def _session_from_cookie(cookie_header: str | None) -> tuple[str | None, dict | None]:
    """Resolve a valid guest session from the HttpOnly session cookie."""
    if not cookie_header:
        return None, None
    try:
        jar = SimpleCookie()
        jar.load(cookie_header)
        morsel = jar.get(SESSION_COOKIE_NAME)
        sid = morsel.value if morsel else ""
    except Exception:
        return None, None
    if not sid or len(sid) > 128:
        return None, None
    now = time.time()
    with _sessions_lock:
        item = _sessions.get(sid)
        if not item:
            return None, None
        expires = item.get("expires")
        if SESSION_TTL_SECONDS > 0 and expires is not None and float(expires) <= now:
            _sessions.pop(sid, None)
            return None, None
        return sid, dict(item)


def get_aerodatabox_key() -> tuple[str, str]:
    """Return the configured AeroDataBox key and its non-sensitive source.

    The public companion reads provider credentials only from its dedicated
    mounted secret (or the explicit environment fallback supported by
    ``_read_secret``). It never reads another plugin's database at runtime.
    """
    return AERODATABOX_API_KEY_ENV, AERODATABOX_API_KEY_SOURCE

def aerodatabox_configured() -> bool:
    """Return whether the dedicated AeroDataBox credential is configured."""
    key, _source = get_aerodatabox_key()
    return bool(key)


def aerodatabox_key_source() -> str:
    """Return the non-sensitive source label for the AeroDataBox credential."""
    _key, source = get_aerodatabox_key()
    return source


def _json_url(url: str, headers: dict[str, str], timeout: float) -> tuple[int, object | None, str | None]:
    """Perform a bounded JSON GET and return status, decoded payload and safe error text."""
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read(4 * 1024 * 1024)
            status = int(getattr(res, "status", 200) or 200)
        try:
            return status, json.loads(raw.decode("utf-8")), None
        except Exception:
            return status, None, "invalid JSON"
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            raw = exc.read(256 * 1024)
            if raw:
                parsed = json.loads(raw.decode("utf-8"))
                if isinstance(parsed, dict):
                    detail = str(parsed.get("message") or parsed.get("error") or "")
        except Exception:
            pass
        return int(exc.code or 0), None, detail or f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, None, str(exc)


def _rate_limited_aero_json(url: str) -> tuple[int, object | None, str | None]:
    """Call AeroDataBox under the process-wide rate limiter and retry policy."""
    global _last_aero_request
    key, _source = get_aerodatabox_key()
    if not key:
        return 0, None, "AeroDataBox key unavailable"

    # Keep all AeroDataBox traffic in one process-wide queue. The configurable
    # minimum interval protects provider quotas and leaves headroom for other
    # legitimate uses of the same account. A 429 is retried with backoff.
    with _aero_rate_lock:
        attempts = max(1, AERODATABOX_429_RETRIES + 1)
        last_result = (0, None, "AeroDataBox request was not attempted")
        for attempt in range(attempts):
            wait = max(0.0, AERODATABOX_MIN_INTERVAL - (time.monotonic() - _last_aero_request))
            if wait > 0:
                log_event(logging.DEBUG, "aerodatabox.rate_wait", seconds=round(wait, 3), attempt=attempt + 1)
                time.sleep(wait)

            result = _json_url(url, {
                "Accept": "application/json",
                "x-rapidapi-key": key,
                "x-rapidapi-host": "aerodatabox.p.rapidapi.com",
                "User-Agent": f"TREK-Guest-Portal/{VERSION}",
            }, AERODATABOX_TIMEOUT)
            _last_aero_request = time.monotonic()
            last_result = result
            status, _data, error = result
            if status != 429:
                return result

            if attempt >= attempts - 1:
                break
            backoff = AERODATABOX_429_BACKOFF * (attempt + 1)
            log_event(logging.WARNING, "aerodatabox.rate_limited", status=status, attempt=attempt + 1, retry_in_seconds=round(backoff, 2), error=error or "rate limit")
            time.sleep(backoff)

        log_event(logging.ERROR, "aerodatabox.rate_limit_exhausted", attempts=attempts, min_interval=AERODATABOX_MIN_INTERVAL)
        return last_result


def _norm_flight_number(value) -> str:
    """Normalize flight-number text to an uppercase alphanumeric identifier."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())[:8]


def _parse_meta(reservation: dict) -> dict:
    """Decode TREK reservation metadata into a dictionary when possible."""
    raw = reservation.get("metadata", reservation.get("meta", {}))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _ordered_endpoints(reservation: dict) -> list[dict]:
    """Return reservation endpoints sorted by their sequence field."""
    eps = reservation.get("endpoints")
    if not isinstance(eps, list):
        return []
    return sorted([e for e in eps if isinstance(e, dict)], key=lambda x: x.get("sequence") or 0)


def _reservation_legs(reservation: dict, reference: dict | None, reservation_id: str, shared: dict) -> list[dict]:
    """Build normalized flight legs from shared TREK data and Guest Portal cache references."""
    meta = _parse_meta(reservation)
    raw_legs = meta.get("legs") if isinstance(meta.get("legs"), list) else None
    eps = _ordered_endpoints(reservation)
    leg_count = min(6, max(len(raw_legs or []), len(eps) - 1 if len(eps) > 1 else 1 if (raw_legs or meta) else 0))
    legs: list[dict] = []

    for idx in range(leg_count):
        item = raw_legs[idx] if isinstance(raw_legs, list) and idx < len(raw_legs) and isinstance(raw_legs[idx], dict) else {}
        from_ep = eps[idx] if idx < len(eps) else {}
        to_ep = eps[idx + 1] if idx + 1 < len(eps) else {}

        if raw_legs is None and not eps:
            break

        legs.append({
            "from": item.get("from") or from_ep.get("code") or meta.get("departure_airport"),
            "to": item.get("to") or to_ep.get("code") or meta.get("arrival_airport"),
            "airline": item.get("airline") or meta.get("airline"),
            "airlineCode": item.get("airline_code") or meta.get("airline_code"),
            "flight": item.get("flight_number") or item.get("flightNumber") or meta.get("flight_number") or meta.get("flightNumber"),
            "depTime": item.get("dep_time") or from_ep.get("local_time") or reservation.get("reservation_time"),
            "arrTime": item.get("arr_time") or to_ep.get("local_time") or reservation.get("reservation_end_time"),
            "depDayId": item.get("dep_day_id") or from_ep.get("day_id") or reservation.get("day_id"),
            "arrDayId": item.get("arr_day_id") or to_ep.get("day_id") or reservation.get("end_day_id") or reservation.get("day_id"),
            "seat": item.get("seat") or meta.get("seat"),
            "localDepDate": item.get("local_date") or from_ep.get("local_date"),
        })

    if not legs and eps:
        first = eps[0] if eps else {}
        last = eps[-1] if eps else {}
        legs.append({
            "from": first.get("code") or meta.get("departure_airport"),
            "to": last.get("code") or meta.get("arrival_airport"),
            "airline": meta.get("airline"), "airlineCode": meta.get("airline_code"),
            "flight": meta.get("flight_number") or meta.get("flightNumber"),
            "depTime": first.get("local_time") or reservation.get("reservation_time"),
            "arrTime": last.get("local_time") or reservation.get("reservation_end_time"),
            "depDayId": reservation.get("day_id"), "arrDayId": reservation.get("end_day_id") or reservation.get("day_id"),
            "seat": meta.get("seat"), "localDepDate": first.get("local_date"),
        })

    # A previously cached Guest Portal payload can contribute normalized
    # flight numbers/callsigns without granting access to another plugin database.
    reference_legs = reference.get("legs") if isinstance(reference, dict) and isinstance(reference.get("legs"), list) else []
    day_dates = {}
    for day in shared.get("days") or []:
        if isinstance(day, dict) and day.get("id") is not None and day.get("date"):
            day_dates[str(day.get("id"))] = str(day.get("date"))[:10]

    resolved = []
    for idx, leg in enumerate(legs):
        raw = _norm_flight_number(leg.get("flight"))
        airline_code = _norm_flight_number(leg.get("airlineCode"))
        if raw and raw.isdigit() and len(airline_code) == 2:
            raw = airline_code + raw
        base_leg = reference_legs[idx] if idx < len(reference_legs) and isinstance(reference_legs[idx], dict) else {}
        number = _norm_flight_number(base_leg.get("number")) or raw
        dep_date = ""
        dep_day_id = leg.get("depDayId")
        if dep_day_id is not None:
            dep_date = day_dates.get(str(dep_day_id), "")
        if not dep_date:
            dep_date = str(leg.get("localDepDate") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dep_date or ""):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", str(leg.get("depTime") or reservation.get("reservation_time") or ""))
            dep_date = m.group(1) if m else ""
        resolved.append({
            "number": number, "callSign": base_leg.get("callSign"),
            "airline": leg.get("airline") or base_leg.get("airline"),
            "from": leg.get("from") or base_leg.get("from"), "to": leg.get("to") or base_leg.get("to"),
            "depTime": leg.get("depTime") or base_leg.get("depTime"), "arrTime": leg.get("arrTime") or base_leg.get("arrTime"),
            "depDayId": leg.get("depDayId"), "arrDayId": leg.get("arrDayId"),
            "seat": leg.get("seat") or base_leg.get("seat"), "localDepDate": dep_date or None,
            "_baseline": base_leg,
        })
    return resolved


def _pick_time(block: dict | None) -> dict | None:
    """Select scheduled and revised local/UTC timestamps from a provider block."""
    if not isinstance(block, dict):
        return None
    revised = block.get("revisedTime") or block.get("predictedTime") or block.get("runwayTime") or {}
    scheduled = block.get("scheduledTime") or {}
    def val(obj, key):
        """Safely read a key from a provider timestamp dictionary."""
        return obj.get(key) if isinstance(obj, dict) else None
    return {
        "scheduled": val(scheduled, "local") or val(scheduled, "utc"),
        "revised": val(revised, "local") or val(revised, "utc"),
        "scheduledUtc": val(scheduled, "utc"), "revisedUtc": val(revised, "utc"),
    }


def _time_delay_minutes(times: dict | None) -> int | None:
    """Calculate revised-versus-scheduled delay in minutes when UTC values exist."""
    if not times or not times.get("revisedUtc") or not times.get("scheduledUtc"):
        return None
    try:
        a = _iso_ms(times["revisedUtc"]); b = _iso_ms(times["scheduledUtc"])
        return None if a is None or b is None else round((a - b) / 60000)
    except Exception:
        return None


def _airport_block(block: dict | None, times: dict | None) -> dict:
    """Normalize an AeroDataBox airport endpoint for the guest payload."""
    block = block if isinstance(block, dict) else {}
    ap = block.get("airport") if isinstance(block.get("airport"), dict) else {}
    loc = ap.get("location") if isinstance(ap.get("location"), dict) else {}
    def fnum(v):
        """Convert a provider numeric field to float without propagating malformed values."""
        try: return float(v) if v is not None else None
        except Exception: return None
    return {
        "iata": ap.get("iata") or ap.get("icao"),
        "name": ap.get("shortName") or ap.get("name") or ap.get("municipalityName"),
        "terminal": block.get("terminal"), "gate": block.get("gate"), "baggageBelt": block.get("baggageBelt"),
        "scheduled": times.get("scheduled") if times else None, "revised": times.get("revised") if times else None,
        "scheduledUtc": times.get("scheduledUtc") if times else None, "revisedUtc": times.get("revisedUtc") if times else None,
        "lat": fnum(loc.get("lat", loc.get("latitude"))), "lon": fnum(loc.get("lon", loc.get("longitude"))),
    }


def _normalise_aero(flight: dict) -> dict:
    """Normalize one AeroDataBox flight result into Guest Portal fields."""
    dep = flight.get("departure") if isinstance(flight.get("departure"), dict) else {}
    arr = flight.get("arrival") if isinstance(flight.get("arrival"), dict) else {}
    dt, at = _pick_time(dep), _pick_time(arr)
    airline = flight.get("airline") if isinstance(flight.get("airline"), dict) else {}
    aircraft = flight.get("aircraft") if isinstance(flight.get("aircraft"), dict) else {}
    return {
        "number": str(flight.get("number") or ""), "callSign": flight.get("callSign"),
        "status": flight.get("status") or "Unknown", "airline": airline.get("name"),
        "aircraftModel": aircraft.get("model"), "aircraftReg": aircraft.get("reg"),
        "delayMin": _time_delay_minutes(at), "depDelayMin": _time_delay_minutes(dt),
        "departure": _airport_block(dep, dt), "arrival": _airport_block(arr, at),
    }


def _aero_dep_ms(flight: dict) -> int:
    """Return the best AeroDataBox departure timestamp in epoch milliseconds."""
    dep = flight.get("departure") if isinstance(flight, dict) else None
    if not isinstance(dep, dict): return 0
    times = dep.get("scheduledTime") or dep.get("revisedTime") or {}
    value = times.get("utc") or times.get("local") if isinstance(times, dict) else None
    return _iso_ms(value) or 0


def _fetch_aero(number: str, dep_date: str | None) -> tuple[dict | None, str | None]:
    """Fetch and select the best AeroDataBox match for a flight/date pair."""
    if not aerodatabox_configured() or not number:
        return None, None
    date_path = f"/{quote(dep_date, safe='')}" if dep_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", dep_date) else ""
    url = f"{AERODATABOX_HOST}/flights/number/{quote(number, safe='')}{date_path}?withAircraftImage=false&withLocation=true&dateLocalRole=Both"
    started = time.monotonic()
    log_event(logging.INFO, "aerodatabox.request", flight=number, date=dep_date or "auto")
    status, data, error = _rate_limited_aero_json(url)
    if status < 200 or status >= 300:
        log_event(logging.WARNING, "aerodatabox.response", flight=number, date=dep_date or "auto", status=status, error=error or f"HTTP {status}", elapsed_ms=int((time.monotonic()-started)*1000))
        return None, error or f"HTTP {status}"
    if isinstance(data, list): flights = data
    elif isinstance(data, dict) and isinstance(data.get("flights"), list): flights = data.get("flights")
    elif isinstance(data, dict) and data.get("departure"): flights = [data]
    else: flights = []
    if not flights:
        log_event(logging.INFO, "aerodatabox.response", flight=number, date=dep_date or "auto", status=status, matches=0, elapsed_ms=int((time.monotonic()-started)*1000))
        return None, None
    pool = [f for f in flights if isinstance(f, dict)]
    if dep_date:
        same = []
        for f in pool:
            dep = f.get("departure") if isinstance(f.get("departure"), dict) else {}
            t = dep.get("scheduledTime") if isinstance(dep.get("scheduledTime"), dict) else {}
            local = t.get("local") or t.get("utc")
            if isinstance(local, str) and local[:10] == dep_date:
                same.append(f)
        if same: pool = same
    now_ms = int(time.time() * 1000)
    pool.sort(key=lambda f: abs(_aero_dep_ms(f) - now_ms) if _aero_dep_ms(f) else 10**18)
    normalised = _normalise_aero(pool[0])
    log_event(logging.INFO, "aerodatabox.response", flight=number, date=dep_date or "auto", status=status, matches=len(pool), flight_status=normalised.get("status"), elapsed_ms=int((time.monotonic()-started)*1000))
    return normalised, None


def _normalise_live(ac: dict) -> dict:
    """Normalize one adsb.fi aircraft record for the guest payload."""
    def fnum(v):
        """Convert a provider numeric field to float without propagating malformed values."""
        try: return float(v) if v is not None and v != "" else None
        except Exception: return None
    alt_raw = ac.get("alt_baro")
    alt = "ground" if alt_raw == "ground" else fnum(alt_raw)
    return {
        "hex": ac.get("hex"), "callSign": str(ac.get("flight") or "").strip() or None,
        "reg": ac.get("r"), "type": ac.get("t"), "desc": ac.get("desc"),
        "lat": fnum(ac.get("lat")), "lon": fnum(ac.get("lon")), "altBaro": alt,
        "groundSpeed": fnum(ac.get("gs")), "track": fnum(ac.get("track")),
        "verticalRate": fnum(ac.get("baro_rate")) if fnum(ac.get("baro_rate")) is not None else fnum(ac.get("geom_rate")),
        "squawk": ac.get("squawk"), "onGround": alt == "ground", "seenPos": fnum(ac.get("seen_pos")),
    }


def _fetch_adsb(reg: str | None, callsign: str | None, number: str | None) -> dict | None:
    """Fetch current aircraft information from adsb.fi using bounded identifiers."""
    paths = []
    if reg:
        paths.append(f"/v2/registration/{quote(str(reg), safe='')}")
    else:
        for raw in (callsign, number):
            n = _norm_flight_number(raw)
            if n: paths.append(f"/v2/callsign/{quote(n, safe='')}")
    seen = set()
    for path in paths:
        if path in seen: continue
        seen.add(path)
        log_event(logging.INFO if FULL_LOGGING else logging.DEBUG, "adsb.request", lookup=path.split("/")[2] if len(path.split("/")) > 2 else "unknown")
        status, data, error = _json_url(ADSB_HOST + path, {"Accept":"application/json", "User-Agent":f"TREK-Guest-Portal/{VERSION}"}, ADSB_TIMEOUT)
        if 200 <= status < 300 and isinstance(data, dict) and isinstance(data.get("ac"), list) and data["ac"]:
            if isinstance(data["ac"][0], dict):
                live = _normalise_live(data["ac"][0])
                log_event(logging.INFO, "adsb.response", lookup=path.split("/")[2] if len(path.split("/")) > 2 else "unknown", status=status, found=True, registration=live.get("reg"), callsign=live.get("callSign"))
                return live
        log_event(logging.DEBUG if 200 <= status < 300 else logging.WARNING, "adsb.response", lookup=path.split("/")[2] if len(path.split("/")) > 2 else "unknown", status=status, found=False, error=error)
    return None


def _iso_ms(value) -> int | None:
    """Parse an ISO-like timestamp into epoch milliseconds."""
    if value is None or value == "": return None
    text = str(value).strip().replace(" ", "T")
    if not re.search(r"[zZ]$|[+-]\d\d:?\d\d$", text): text += "Z"
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None


def _reservation_estimate(reservation: dict) -> tuple[int | None, int | None, str | None]:
    """Estimate reservation departure/arrival timestamps from shared TREK values."""
    from datetime import datetime, timezone
    def parse(value):
        """Parse a reservation timestamp into epoch milliseconds without raising."""
        if not value: return None, None
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?", str(value))
        if not m: return None, None
        hour, minute = (int(m.group(4)), int(m.group(5))) if m.group(4) else (12,0)
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), hour, minute, tzinfo=timezone.utc)
            return int(dt.timestamp()*1000), f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        except Exception: return None, None
    dep_ms, dep_date = parse(reservation.get("reservation_time"))
    arr_ms, _ = parse(reservation.get("reservation_end_time"))
    if dep_ms and not arr_ms: arr_ms = dep_ms + 20*3600*1000
    return dep_ms, arr_ms, dep_date


def _flight_api_ttl_seconds(payload: dict) -> int:
    """Return the Guest Portal provider-cache TTL for the current flight state.

    This is deliberately separate from the browser polling interval. A guest may
    check every minute while the server continues serving a still-fresh provider
    result without spending another AeroDataBox request.
    """
    booking = payload.get("booking") if isinstance(payload, dict) and isinstance(payload.get("booking"), dict) else {}
    if booking.get("phase") == "past":
        return 0
    legs = payload.get("legs") if isinstance(payload, dict) and isinstance(payload.get("legs"), list) else []
    statuses = [str((l.get("status") or {}).get("status") or "") for l in legs if isinstance(l, dict)]
    if statuses and all(s == "Arrived" for s in statuses):
        return 0
    if any(s in {"EnRoute", "Departed", "Approaching", "Boarding", "Diverted"} for s in statuses):
        return 60
    dep = booking.get("depMs")
    try:
        dep = int(dep) if dep is not None else None
    except Exception:
        dep = None
    if not dep:
        return 300
    until = dep - int(time.time() * 1000)
    if until < 3 * 3600 * 1000:
        return 60
    if until < 12 * 3600 * 1000:
        return 300
    if until < 48 * 3600 * 1000:
        return 1800
    return 7200


def _flight_ttl_seconds(payload: dict) -> int:
    """Return the effective cache TTL for the supplied live-flight payload."""
    # Backward-compatible internal alias used by older cache helpers/tests.
    return _flight_api_ttl_seconds(payload)


def _payload_departure_ms(payload: dict | None) -> int | None:
    """Extract a usable departure timestamp from a cached/live payload."""
    if not isinstance(payload, dict):
        return None
    booking = payload.get("booking") if isinstance(payload.get("booking"), dict) else {}
    try:
        value = int(booking.get("depMs")) if booking.get("depMs") is not None else None
        return value if value and value > 0 else None
    except Exception:
        return None


def _flight_runtime_plan(payload: dict, fetched_ms: int | None = None) -> dict:
    """Describe browser polling and provider-call timing independently."""
    now_ms = int(time.time() * 1000)
    booking = payload.get("booking") if isinstance(payload, dict) and isinstance(payload.get("booking"), dict) else {}
    legs = payload.get("legs") if isinstance(payload, dict) and isinstance(payload.get("legs"), list) else []
    statuses = [str((l.get("status") or {}).get("status") or "") for l in legs if isinstance(l, dict)]
    all_arrived = bool(statuses) and all(s == "Arrived" for s in statuses)
    moving = any(s in {"EnRoute", "Departed", "Approaching", "Boarding", "Diverted"} for s in statuses)
    phase = str(booking.get("phase") or "unknown")
    dep_ms = _payload_departure_ms(payload)
    until_ms = None if dep_ms is None else dep_ms - now_ms
    window_ms = int(FLIGHT_API_WINDOW_HOURS * 3600 * 1000)
    past = phase == "past" or all_arrived
    api_window_open = (not past) and (dep_ms is None or until_ms <= window_ms)

    if past:
        poll_seconds = 0
        reason = "complete"
    elif not api_window_open or phase == "upcoming":
        poll_seconds = FLIGHT_UPCOMING_POLL_SECONDS
        reason = "upcoming-check"
    else:
        poll_seconds = FLIGHT_ACTIVE_POLL_SECONDS
        reason = "active-check"

    api_ttl = _flight_api_ttl_seconds(payload)
    age_seconds = None
    if fetched_ms:
        age_seconds = max(0, int((now_ms - int(fetched_ms)) / 1000))

    if past:
        api_due_seconds = None
        api_window_opens_in = None
    elif not api_window_open and until_ms is not None:
        api_window_opens_in = max(0, int((until_ms - window_ms + 999) // 1000))
        api_due_seconds = api_window_opens_in
    else:
        api_window_opens_in = 0
        api_due_seconds = 0 if age_seconds is None else max(0, api_ttl - age_seconds)

    return {
        "phase": phase,
        "depMs": dep_ms,
        "untilDepartureSeconds": None if until_ms is None else int(until_ms / 1000),
        "hoursToDeparture": None if until_ms is None else round(until_ms / 3600000.0, 2),
        "estimatedTimes": bool(booking.get("estimatedTimes")),
        "statuses": ",".join(s for s in statuses if s) or "none",
        "moving": moving,
        "allArrived": all_arrived,
        "apiWindowOpen": api_window_open,
        "apiWindowOpensInSeconds": api_window_opens_in,
        "apiTtlSeconds": api_ttl,
        "apiRefreshAfterSeconds": api_due_seconds,
        "pollAfterSeconds": poll_seconds,
        "reason": reason,
        "cacheAgeSeconds": age_seconds,
    }


def _log_flight_plan(event: str, plan: dict, trip_id: str, reservation_id: str, decision: str, **extra) -> None:
    """Emit a structured diagnostic event describing a flight refresh decision."""
    log_event(
        logging.INFO,
        event,
        trip_id=trip_id,
        reservation_id=reservation_id,
        decision=decision,
        phase=plan.get("phase"),
        hours_to_departure=plan.get("hoursToDeparture"),
        estimated_times=plan.get("estimatedTimes"),
        statuses=plan.get("statuses"),
        api_window_open=plan.get("apiWindowOpen"),
        api_window_opens_in=plan.get("apiWindowOpensInSeconds"),
        api_ttl=plan.get("apiTtlSeconds"),
        api_due_in=plan.get("apiRefreshAfterSeconds"),
        poll_after=plan.get("pollAfterSeconds"),
        cache_age=plan.get("cacheAgeSeconds"),
        **extra,
    )


def _apply_flight_runtime_metadata(payload: dict, fetched_ms: int | None, source: str | None = None) -> dict:
    """Attach browser/provider refresh metadata to a flight payload."""
    out = json.loads(json.dumps(payload))
    live = out.setdefault("_guestLive", {})
    if source:
        live["source"] = source
    api_fetched_at = live.get("apiFetchedAt")
    if not api_fetched_at:
        # Legacy persistent rows created before apiFetchedAt did not have that field. Infer it only when
        # the stored source clearly represents an AeroDataBox build.
        src = str(live.get("source") or "")
        if fetched_ms and ("AeroDataBox" in src or out.get("source") == "guest-live"):
            api_fetched_at = int(fetched_ms)
            live["apiFetchedAt"] = int(fetched_ms)
    plan = _flight_runtime_plan(out, int(api_fetched_at) if api_fetched_at else None)
    live.update({
        "refreshAfterSeconds": plan["pollAfterSeconds"],
        "pollAfterSeconds": plan["pollAfterSeconds"],
        "apiWindowOpen": plan["apiWindowOpen"],
        "apiWindowOpensInSeconds": plan["apiWindowOpensInSeconds"],
        "apiTtlSeconds": plan["apiTtlSeconds"],
        "apiRefreshAfterSeconds": plan["apiRefreshAfterSeconds"],
        "refreshReason": plan["reason"],
        "hoursToDeparture": plan["hoursToDeparture"],
    })
    if fetched_ms:
        cache = out.setdefault("_guestCache", {})
        cache["fetchedAt"] = int(fetched_ms)
        cache["ageSeconds"] = max(0, int((time.time() * 1000 - int(fetched_ms)) / 1000))
        cache["stale"] = False
    return out


def _live_lock(key: tuple[str, str]) -> threading.Lock:
    """Return the per-flight lock that serializes provider refreshes."""
    with _live_refresh_locks_guard:
        lock = _live_refresh_locks.get(key)
        if lock is None:
            lock = threading.Lock(); _live_refresh_locks[key] = lock
        return lock


def _build_upcoming_flight_payload(reservation: dict, reservation_id: str, trip_id: str, shared: dict, reference: dict | None) -> dict:
    """Build a schedule-only payload without contacting AeroDataBox.

    Guest Portal does not query AeroDataBox for far-future flights. The browser
    still checks periodically so it notices when the reservation enters the live
    provider window.
    """
    now_ms = int(time.time() * 1000)
    dep_ms, arr_ms, _base_date = _reservation_estimate(reservation)
    ref_dep = _payload_departure_ms(reference)
    if ref_dep:
        dep_ms = ref_dep
    if isinstance(reference, dict):
        rb = reference.get("booking") if isinstance(reference.get("booking"), dict) else {}
        try:
            if rb.get("arrMs") is not None:
                arr_ms = int(rb.get("arrMs"))
        except Exception:
            pass
    legs = []
    for leg in _reservation_legs(reservation, reference, reservation_id, shared):
        leg = dict(leg)
        leg.pop("_baseline", None)
        leg.update({"status": None, "live": None, "inbound": None, "weather": None, "errors": []})
        legs.append(leg)
    payload = {
        "applicable": True,
        "source": "guest-scheduled",
        "legs": legs,
        "booking": {
            "type": "Flight", "depMs": dep_ms, "arrMs": arr_ms, "phase": "upcoming",
            "origin": legs[0].get("from") if legs else None,
            "dest": legs[-1].get("to") if legs else None,
            "legCount": len(legs), "estimatedTimes": not bool(ref_dep),
        },
        "errors": [],
        "updatedAt": now_ms,
        "_guestCache": {"fetchedAt": now_ms, "ageSeconds": 0, "stale": False},
        "_guestLive": {
            "configured": aerodatabox_configured(),
            "source": "Scheduled data only — live provider window not open",
            "apiFetchedAt": None,
            "fetchedAt": now_ms,
        },
    }
    payload = _apply_flight_runtime_metadata(payload, now_ms)
    plan = _flight_runtime_plan(payload, None)
    _log_flight_plan("flight.refresh_decision", plan, trip_id, reservation_id,
                     "suppress-aerodatabox", reason="outside-live-window")
    return payload


def _build_live_flight_payload(reservation: dict, reservation_id: str, trip_id: str, shared: dict, baseline: dict | None) -> dict:
    """Query live providers and build the normalized guest flight payload."""
    now_ms = int(time.time() * 1000)
    dep_ms, arr_ms, base_date = _reservation_estimate(reservation)
    phase = "active"
    if dep_ms and now_ms < dep_ms - int(FLIGHT_API_WINDOW_HOURS * 3600 * 1000):
        phase = "upcoming"
    elif arr_ms and now_ms > arr_ms + 6 * 3600 * 1000:
        phase = "past"

    legs_in = _reservation_legs(reservation, baseline, reservation_id, shared)
    legs_out = []
    errors = []
    for idx, leg in enumerate(legs_in):
        number = leg.get("number")
        status = None
        live = None
        inbound = None
        dep_date = leg.get("localDepDate") or base_date
        log_event(logging.INFO, "flight.leg_refresh_plan", trip_id=trip_id,
                  reservation_id=reservation_id, leg=idx + 1, flight=number or "unknown",
                  departure_date=dep_date or "unknown", aerodatabox=bool(number),
                  adsb_window="evaluate-after-status")
        if number:
            status, aero_err = _fetch_aero(number, dep_date)
            if aero_err:
                errors.append(f"status: {aero_err}")
            airborne = bool(status and status.get("status") in {"EnRoute", "Departed", "Approaching"})
            close_window = not dep_ms or (now_ms >= dep_ms - 3600 * 1000 and (arr_ms is None or now_ms <= arr_ms + 2 * 3600 * 1000))
            log_event(logging.DEBUG, "flight.adsb_decision", trip_id=trip_id,
                      reservation_id=reservation_id, leg=idx + 1, flight=number,
                      airborne=airborne, close_window=close_window,
                      request=bool(airborne or close_window))
            if airborne or close_window:
                live = _fetch_adsb(status.get("aircraftReg") if status else None,
                                   status.get("callSign") if status else leg.get("callSign"), number)
            if status and status.get("aircraftReg") and not airborne and status.get("status") != "Arrived" and close_window and not live:
                candidate = _fetch_adsb(status.get("aircraftReg"), None, None)
                if candidate and candidate.get("lat") is not None and not candidate.get("onGround"):
                    inbound = candidate
        else:
            errors.append("flight number could not be detected")
        base_leg = leg.pop("_baseline", {})
        out = dict(leg)
        out.update({"status": status, "live": live, "inbound": inbound,
                    "weather": base_leg.get("weather"), "errors": []})
        legs_out.append(out)

    # Authoritative AeroDataBox UTC times replace rough reservation estimates.
    if legs_out:
        first = legs_out[0].get("status") or {}
        dep_block = first.get("departure") if isinstance(first.get("departure"), dict) else {}
        first_utc = _iso_ms(dep_block.get("revisedUtc") or dep_block.get("scheduledUtc"))
        last_status = legs_out[-1].get("status") or {}
        arr_block = last_status.get("arrival") if isinstance(last_status.get("arrival"), dict) else {}
        last_utc = _iso_ms(arr_block.get("revisedUtc") or arr_block.get("scheduledUtc"))
        if first_utc is not None:
            dep_ms = first_utc
        if last_utc is not None:
            arr_ms = last_utc
        if dep_ms and now_ms < dep_ms - int(FLIGHT_API_WINDOW_HOURS * 3600 * 1000):
            phase = "upcoming"
        elif arr_ms and now_ms > arr_ms + 6 * 3600 * 1000:
            phase = "past"
        else:
            phase = "active"

    payload = {
        "applicable": True,
        "source": "guest-live",
        "legs": legs_out,
        "booking": {
            "type": "Flight", "depMs": dep_ms, "arrMs": arr_ms, "phase": phase,
            "origin": legs_out[0].get("from") if legs_out else None,
            "dest": legs_out[-1].get("to") if legs_out else None,
            "legCount": len(legs_out),
            "estimatedTimes": not any((l.get("status") or {}).get("departure", {}).get("scheduledUtc") for l in legs_out),
        },
        "errors": list(dict.fromkeys(errors))[:4],
        "updatedAt": now_ms,
        "_guestCache": {"fetchedAt": now_ms, "ageSeconds": 0, "stale": False},
        "_guestLive": {
            "configured": True,
            "source": "AeroDataBox + adsb.fi",
            "apiFetchedAt": now_ms,
            "fetchedAt": now_ms,
        },
    }
    return _apply_flight_runtime_metadata(payload, now_ms)


def _reference_departure_ms(reservation: dict, *payloads: dict | None) -> int | None:
    """Choose the best departure timestamp from cached payloads or TREK data."""
    for payload in payloads:
        dep = _payload_departure_ms(payload)
        if dep:
            return dep
    dep_ms, _arr_ms, _date = _reservation_estimate(reservation)
    return dep_ms


def get_live_flight_payload(reservation: dict, reservation_id: str, trip_id: str, shared: dict) -> dict | None:
    """Serve cached flight data or perform one synchronized live-provider refresh."""
    key = (str(trip_id), str(reservation_id))
    now_ms = int(time.time() * 1000)

    # Read persistent state early because an older provider result may contain a
    # more authoritative UTC departure than TREK's timezone-less reservation.
    persisted = read_persistent_live_cache(str(trip_id), str(reservation_id))
    persisted_fetched = persisted[0] if persisted else None
    persisted_payload = persisted[1] if persisted else None
    dep_ref = _reference_departure_ms(reservation, persisted_payload)
    window_ms = int(FLIGHT_API_WINDOW_HOURS * 3600 * 1000)
    if dep_ref is not None and now_ms < dep_ref - window_ms:
        return _build_upcoming_flight_payload(reservation, reservation_id, str(trip_id), shared, persisted_payload)

    # Memory cache first for hot requests, then persistent cache for restart-safe
    # reuse. The browser may check every minute; provider TTL determines whether
    # those checks result in an external API request.
    with _live_flight_cache_lock:
        cached = _live_flight_cache.get(key)
    if cached:
        fetched_ms, cached_payload = cached
        out = _decorate_cached_payload(fetched_ms, cached_payload, str(trip_id), str(reservation_id), "flight.memory_cache_hit")
        if out is not None:
            return out

    if persisted:
        out = _decorate_cached_payload(persisted_fetched, persisted_payload, str(trip_id), str(reservation_id), "flight.persistent_cache_hit")
        if out is not None:
            with _live_flight_cache_lock:
                _live_flight_cache[key] = (persisted_fetched, persisted_payload)
            return out

    if not aerodatabox_configured():
        log_event(logging.WARNING, "flight.refresh_decision", trip_id=trip_id,
                  reservation_id=reservation_id, decision="no-provider-key")
        return None

    lock = _live_lock(key)
    with lock:
        # Another request may have refreshed the row while this request waited.
        now_ms = int(time.time() * 1000)
        with _live_flight_cache_lock:
            cached = _live_flight_cache.get(key)
        if cached:
            fetched_ms, cached_payload = cached
            out = _decorate_cached_payload(fetched_ms, cached_payload, str(trip_id), str(reservation_id), "flight.memory_cache_hit_after_lock")
            if out is not None:
                return out

        persisted2 = read_persistent_live_cache(str(trip_id), str(reservation_id))
        if persisted2:
            fetched_ms, cached_payload = persisted2
            out = _decorate_cached_payload(fetched_ms, cached_payload, str(trip_id), str(reservation_id), "flight.persistent_cache_hit_after_lock")
            if out is not None:
                with _live_flight_cache_lock:
                    _live_flight_cache[key] = (fetched_ms, cached_payload)
                return out

        cached_reference = persisted2[1] if persisted2 else None
        dep_ref = _reference_departure_ms(reservation, cached_reference)
        if dep_ref is not None and now_ms < dep_ref - window_ms:
            return _build_upcoming_flight_payload(reservation, reservation_id, str(trip_id), shared,
                                                  cached_reference)

        preview = {
            "booking": {"depMs": dep_ref, "phase": "active", "estimatedTimes": True},
            "legs": [],
        }
        plan = _flight_runtime_plan(preview, None)
        _log_flight_plan("flight.refresh_decision", plan, trip_id, reservation_id,
                         "call-aerodatabox", key_source=aerodatabox_key_source())
        try:
            log_event(logging.INFO, "flight.live_refresh_start", trip_id=trip_id,
                      reservation_id=reservation_id, cached_reference=bool(cached_reference),
                      key_source=aerodatabox_key_source())
            started = time.monotonic()
            payload = _build_live_flight_payload(reservation, reservation_id, str(trip_id), shared, cached_reference)
            post_plan = _flight_runtime_plan(payload, int(payload.get("_guestLive", {}).get("apiFetchedAt") or now_ms))
            _log_flight_plan("flight.live_refresh_complete", post_plan, trip_id, reservation_id,
                             "provider-refresh-complete", legs=len(payload.get("legs") or []),
                             errors=len(payload.get("errors") or []),
                             elapsed_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:
            log_event(logging.ERROR, "flight.live_refresh_failed", trip_id=trip_id,
                      reservation_id=reservation_id, error=str(exc), exc_type=type(exc).__name__)
            if isinstance(cached_reference, dict):
                payload = json.loads(json.dumps(cached_reference))
                payload.setdefault("errors", []).append("live provider refresh failed")
                fetched_at = int((payload.get("_guestCache") or {}).get("fetchedAt") or now_ms)
                payload = _apply_flight_runtime_metadata(payload, fetched_at, "Guest Portal cache fallback")
                payload.setdefault("_guestLive", {})["refreshAfterSeconds"] = FLIGHT_ERROR_POLL_SECONDS
                payload["_guestLive"]["pollAfterSeconds"] = FLIGHT_ERROR_POLL_SECONDS
                log_event(logging.WARNING, "flight.cache_fallback", trip_id=trip_id,
                          reservation_id=reservation_id, retry_after=FLIGHT_ERROR_POLL_SECONDS)
                return payload
            raise

        with _live_flight_cache_lock:
            if len(_live_flight_cache) >= LIVE_FLIGHT_MAX_CACHE:
                oldest = min(_live_flight_cache.items(), key=lambda kv: kv[1][0])[0]
                _live_flight_cache.pop(oldest, None)
                log_event(logging.DEBUG, "flight.memory_cache_evict", key=f"{oldest[0]}:{oldest[1]}")
            fetched_at = int(payload.get("_guestLive", {}).get("apiFetchedAt") or payload.get("updatedAt") or now_ms)
            _live_flight_cache[key] = (fetched_at, payload)
        write_persistent_live_cache(str(trip_id), str(reservation_id), fetched_at, payload)
        return json.loads(json.dumps(payload))


def _ical_escape(text: str) -> str:
    """Escape a string for use in an iCal property value."""
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

def _ical_dt(value: str | None, tz: str | None = None, is_date: bool = False) -> tuple[str, str]:
    """Format an ISO-8601 datetime string as an iCal DTSTART/DTEND value.

    Returns a (formatted_string, tz_name) tuple.  tz_name is empty when no
    conversion was applied.  Callers use TZID= on the property line when
    tz_name is non-empty.
    """
    if not value:
        return "", ""
    value = value.replace("Z", "").rstrip("Z").rstrip(":")
    # Ensure seconds are present so fromisoformat parses correctly.
    if len(value) == 16:  # YYYY-MM-DDTHH:MM
        value = value + ":00"
    elif len(value) == 12:  # YYYYMMDDTHHMM (compact, no seconds)
        # Convert to YYYY-MM-DDTHH:MM:00 for fromisoformat.
        value = f"{value[:4]}-{value[4:6]}-{value[6:8]}T{value[9:11]}:{value[11:13]}:00"
    if is_date:
        return value[:10].replace("-", ""), ""
    if tz:
        try:
            tz_obj = ZoneInfo(tz)
            # Validate the timezone by checking it can produce a UTC offset.
            tz_obj.utcoffset(datetime.now())
            naive = datetime.fromisoformat(value)
            # reservation_time from TREK is the local wall-clock time at the departure
            # airport, NOT UTC.  Return the local time with the IANA tz name so that
            # Google Calendar (with VTIMEZONE blocks present) can display it correctly.
            local = naive.replace(tzinfo=tz_obj)
            return local.strftime("%Y%m%dT%H%M%S"), tz
        except Exception:
            pass
    return value.replace("-", "").replace(":", ""), ""


def _build_vtimezone(tz_name: str) -> str:
    """Generate a minimal RFC 5545 VTIMEZONE component for the given IANA timezone.

    Produces a single STANDARD sub-component with a fixed UTC offset and a real
    historical DTSTART (1967-10-29) to satisfy parsers that require it.
    This is sufficient for Google Calendar to correctly interpret DTSTART/DTEND
    times that use TZID= on the property line.
    """
    try:
        tz_obj = ZoneInfo(tz_name)
        now = datetime.now(tz_obj)
        offset = tz_obj.utcoffset(now)
        if offset is None:
            return ""
        total_seconds = int(offset.total_seconds())
        sign = "+" if total_seconds >= 0 else "-"
        total_seconds = abs(total_seconds)
        hh = total_seconds // 3600
        mm = (total_seconds % 3600) // 60
        offset_str = f"{sign}{hh:02d}{mm:02d}"
        lines = [
            "BEGIN:VTIMEZONE",
            f"TZID:{tz_name}",
            "BEGIN:STANDARD",
            "DTSTART:19671029T020000",
            f"TZOFFSETFROM:{offset_str}",
            f"TZOFFSETTO:{offset_str}",
            f"TZNAME:{tz_name}",
            "END:STANDARD",
            "END:VTIMEZONE",
        ]
        return "\r\n".join(lines)
    except Exception:
        return ""


def _tz_offset(tz_name: str) -> int | None:
    """Return the UTC offset in seconds for an IANA timezone, or None if invalid."""
    try:
        return ZoneInfo(tz_name).utcoffset(datetime.now()).total_seconds()
    except Exception:
        return None


def _make_uid(kind: str, item_id: str, trip_id: str | None) -> str:
    """Generate a safe unique identifier for a trip item."""
    safe_id = re.sub(r"[^A-Za-z0-9]", "-", f"{kind}-{item_id}")
    trip_part = f"{re.sub(r'[^A-Za-z0-9]', '-', str(trip_id or 'unknown'))}-" if trip_id else ""
    return f"{trip_part}{safe_id}@guest-portal"

def _resolve_accommodation_times(
    item: dict,
    assignments: dict,
    day_dates: dict,
) -> tuple[str | None, str | None]:
    """Resolve start/end datetimes for a v4 accommodation using day assignments.

    v4 TREK provides accommodation records with time-only values (``arr_time``,
    ``dep_time``) and no explicit dates.  The actual dates are carried by the
    day assignments: each assignment links a ``place_id`` to a ``day_id`` which
    in turn has a ``date``.  We walk the assignments to find which day(s) the
    accommodation's place appears on and reconstruct full datetimes from the
    day date plus the time-only value.
    """
    place_id = str(item.get("place_id") or item.get("accommodation_id") or "")
    if not place_id:
        return item.get("arr_time"), item.get("dep_time")

    start_dt: str | None = None
    end_dt: str | None = None

    for day_id, date in day_dates.items():
        assigned_places = assignments.get(day_id) or []
        if not isinstance(assigned_places, list):
            continue
        for ap in assigned_places:
            if not isinstance(ap, dict):
                continue
            if str(ap.get("place_id") or ap.get("id") or "") != place_id:
                continue
            arr_t = str(item.get("arr_time") or "")
            dep_t = str(item.get("dep_time") or "")
            start_dt = f"{date}T{arr_t}" if arr_t and not start_dt else start_dt
            end_dt = f"{date}T{dep_t}" if dep_t and not end_dt else end_dt
            if start_dt and end_dt:
                return start_dt, end_dt

    return item.get("arr_time"), item.get("dep_time")

def _build_ical_feed(trip_data: dict) -> str:
    """Generate a VCALENDAR iCal string for the trip identified by the given token."""
    trip = trip_data.get("trip") or {}
    trip_id = str(trip.get("id") or "")

    # Build day-id -> date lookup
    day_dates: dict[str, str] = {}
    for day in (trip_data.get("days") or []):
        if isinstance(day, dict) and day.get("id") and day.get("date"):
            day_dates[str(day["id"])] = str(day["date"])[:10]

    # Build place-id -> assignment map for time resolution
    raw_assignments: dict = trip_data.get("assignments") or {}
    # {day_id: [assignment, ...]}

    reservations = trip_data.get("reservations") or []
    accommodations = trip_data.get("accommodations") or []

    # TRANSPORT_TYPES from app.js
    TRANSPORT_KINDS = {"flight", "train", "bus", "car", "taxi", "bicycle", "cruise", "ferry"}

    def kind(r: dict) -> str:
        """Extract the transport kind from a reservation dict."""
        for k in ("type", "reservation_type", "category"):
            v = r.get(k)
            if v is not None and str(v).strip():
                return str(v).strip().lower()
        return ""

    items: list[dict] = []

    for r in reservations:
        k = kind(r)
        if k not in TRANSPORT_KINDS:
            continue
        items.append({**r, "_kind": k, "_type": "reservation"})

    for a in accommodations:
        items.append({**a, "_kind": "accommodation", "_type": "accommodation"})

    # Sort by earliest available datetime
    def sort_key(it: dict) -> float:
        """Return timestamp for sorting trip items by start time."""
        t = it.get("reservation_time") or it.get("arr_time") or it.get("start_time") or ""
        try:
            return time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            return 0.0

    items.sort(key=sort_key)

    # Collect all unique timezones used in this feed for VTIMEZONE blocks.
    # Use a dict to count occurrences so we can determine the primary timezone.
    feed_tz_counts: dict[str, int] = {}

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TREK Guest Portal//NONSGML v3.3.13//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ical_escape(trip.get('title') or trip.get('name') or 'Trip')}",
    ]

    # Pre-compute start/end times and collect timezones before writing VEVENT lines.
    # Each entry is (item, start_dt, start_tz, end_dt, end_tz, summary, location, notes, uid)
    event_data: list[tuple] = []
    for item in items:
        kind_str = item["_kind"]
        item_id = str(item.get("id") or item.get("reservation_id") or "")
        uid = _make_uid(kind_str, item_id, trip_id)
        summary = ""

        if item["_type"] == "reservation":
            from_loc = str(item.get("from") or item.get("from_location") or "")
            to_loc = str(item.get("to") or item.get("to_location") or "")
            if kind_str == "flight":
                flight = item.get("flight") or ""
                title = item.get("title") or ""
                # Prefer the descriptive title (may contain IATA codes like "LATAM AIRLINES BRASIL SDU → CGH → FLN").
                summary = f"✈ {title}" if title else "✈ Flight"
                if from_loc and to_loc:
                    summary += f" {from_loc} → {to_loc}"
            elif kind_str == "train":
                summary = "🚆 Train"
                if from_loc and to_loc:
                    summary += f" {from_loc} → {to_loc}"
            elif kind_str == "bus":
                summary = "🚌 Bus"
                if from_loc and to_loc:
                    summary += f" {from_loc} → {to_loc}"
            elif kind_str == "cruise":
                summary = "🚢 Cruise"
                if from_loc and to_loc:
                    summary += f" {from_loc} → {to_loc}"
            elif kind_str == "ferry":
                summary = "⛴ Ferry"
                if from_loc and to_loc:
                    summary += f" {from_loc} → {to_loc}"
            elif kind_str == "bicycle":
                summary = "🚲 Bicycle"
            else:
                summary = f"🚗 {kind_str.capitalize()}"
                if from_loc and to_loc:
                    summary += f" {from_loc} → {to_loc}"

            # Resolve per-event timezone from metadata first (TREK provides proper IANA tz).
            # Fall back to airport-code extraction from title, then GPS lookup, then ICAL_TIMEZONE.
            from_tz = ICAL_TIMEZONE
            to_tz = ICAL_TIMEZONE
            title = item.get("title") or ""
            meta_str = item.get("metadata") or ""
            if meta_str:
                try:
                    meta = json.loads(meta_str) if isinstance(meta_str, str) else meta_str
                    if kind_str == "flight":
                        from_tz = str(meta.get("departure_timezone") or "").strip() or ICAL_TIMEZONE
                        to_tz = str(meta.get("arrival_timezone") or "").strip() or ICAL_TIMEZONE
                except (json.JSONDecodeError, TypeError):
                    pass
            # Validate timezones; fall back to airport code extraction from title.
            # Also re-resolve if we only have the default ICAL_TIMEZONE (not a real event tz).
            if from_tz and (_tz_offset(from_tz) is None or from_tz == ICAL_TIMEZONE):
                from_tz = (_resolve_airport_timezone(title)
                           or _resolve_gps_timezone(trip_data, title)
                           or ICAL_TIMEZONE)
            if to_tz and (_tz_offset(to_tz) is None or to_tz == ICAL_TIMEZONE):
                to_tz = (_resolve_airport_timezone(title)
                           or _resolve_gps_timezone(trip_data, title)
                           or ICAL_TIMEZONE)
            start_dt, start_tz = _ical_dt(item.get("reservation_time"), from_tz)
            end_dt, end_tz = "", ""
            # Use reservation_end_time with the SAME timezone as start to ensure
            # end time matches exactly what was entered in TREK (no timezone conversion).
            # This makes Google Calendar show the same start/end times as TREK.
            end_dt, end_tz = _ical_dt(item.get("reservation_end_time"), from_tz)
            if not end_dt:
                # Fallback to leg arrival time only if reservation_end_time not available
                legs = item.get("legs") or []
                if legs and isinstance(legs, list):
                    last_leg = legs[-1] if isinstance(legs[-1], dict) else {}
                    end_dt, end_tz = _ical_dt(last_leg.get("arr_time") or last_leg.get("arrival_time"), to_tz)
            if not end_dt:
                end_dt, end_tz = _ical_dt(
                    item.get("end_time") or item.get("arrival_time"),
                    from_tz  # Use same tz as start for consistency
                )

        else:  # accommodation
            place = item.get("place") or {}
            name = str(place.get("name") or item.get("name") or "Accommodation")
            address = str(place.get("address") or item.get("address") or "")
            summary = f"🏨 {name}"
            arr_t, dep_t = _resolve_accommodation_times(item, raw_assignments, day_dates)
            # Use ICAL_TIMEZONE until geocoding is available for accommodations.
            start_dt, start_tz = _ical_dt(arr_t, ICAL_TIMEZONE)
            end_dt, end_tz = _ical_dt(dep_t, ICAL_TIMEZONE)

        location = ""
        if item["_type"] == "reservation":
            location = str(item.get("from") or item.get("from_location") or "")
        else:
            place = item.get("place") or {}
            location = str(place.get("address") or item.get("address") or "")

        notes = str(item.get("notes") or item.get("confirmation_code") or "").strip()

        # Skip events with no start or end time — Google Calendar requires both.
        if not start_dt or not end_dt:
            continue

        # Collect timezones for VTIMEZONE generation and primary timezone detection.
        if start_tz:
            feed_tz_counts[start_tz] = feed_tz_counts.get(start_tz, 0) + 1
        if end_tz:
            feed_tz_counts[end_tz] = feed_tz_counts.get(end_tz, 0) + 1

        event_data.append((start_dt, start_tz, end_dt, end_tz, summary, location, notes, uid))

    # Determine the primary timezone as the most common timezone across all events.
    # Fall back to ICAL_TIMEZONE, then UTC.
    primary_tz = ICAL_TIMEZONE
    if feed_tz_counts:
        primary_tz = max(feed_tz_counts, key=lambda tz: feed_tz_counts[tz])

    # Set the calendar's default timezone to the primary timezone.
    # This tells Google Calendar what timezone to use as the baseline.
    # Individual events can override via TZID= in DTSTART/DTEND.
    if primary_tz:
        lines.append(f"X-WR-TIMEZONE:{primary_tz}")

    # Add VTIMEZONE blocks for each timezone used in the feed.
    for tz_name in sorted(feed_tz_counts.keys()):
        vtz = _build_vtimezone(tz_name)
        if vtz:
            lines.append(vtz)

    # Add all VEVENT blocks now that VTIMEZONEs are defined.
    dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for start_dt, start_tz, end_dt, end_tz, summary, location, notes, uid in event_data:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{uid}")
        lines.append(f"DTSTAMP:{dtstamp}")
        tzid = f";TZID={start_tz}" if start_tz else ""
        lines.append(f"DTSTART{tzid}:{start_dt}")
        tzid = f";TZID={end_tz}" if end_tz else ""
        lines.append(f"DTEND{tzid}:{end_dt}")
        lines.append(f"SUMMARY:{_ical_escape(summary)}")
        if location:
            lines.append(f"LOCATION:{_ical_escape(location)}")
        if notes:
            lines.append(f"DESCRIPTION:{_ical_escape(notes)}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)

def reservation_kind(item: dict) -> str:
    """Classify a shared TREK reservation into the guest transport/non-transport model."""
    for key in ("type", "reservation_type", "category"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().lower()
    return ""


class Handler(BaseHTTPRequestHandler):
    """HTTP request handler for static assets, guest APIs, sessions and media proxying."""
    server_version = "GuestPortal"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def version_string(self):
        """Return the server banner without leaking the Python base-server version."""
        return "GuestPortal"

    def log_message(self, fmt, *args):
        """Route BaseHTTPRequestHandler diagnostics into structured application logging."""
        # BaseHTTPRequestHandler's default logger sees only the reverse proxy.
        # Correlated request context already contains the safely resolved client.
        log_event(logging.DEBUG, "http.internal", message=fmt % args)

    def log_request(self, code="-", size="-"):
        """Emit the correlated response event used in place of default access logging."""
        _metric("http_responses")
        path = urlsplit(self.path).path
        level = logging.INFO if path.startswith("/api/") else logging.DEBUG
        started = getattr(self, "_request_started", None)
        elapsed = int((time.monotonic() - started) * 1000) if started else None
        fields = {"method": self.command, "target": _safe_request_target(self.path),
                  "status": code, "bytes": size, "elapsed_ms": elapsed}
        # Do not overwrite the request-context client field with the proxy socket.
        log_event(level, "http.response", **fields)

    def _ical_feed(self, trip_token: str) -> None:
        """Return an iCal feed for the trip identified by the trip share token.

        The token is validated against TREK on every request so the feed URL is
        stable across redeployments — it does not depend on in-memory sessions.
        """
        if not TOKEN_RE.fullmatch(trip_token):
            self._send_json(404, {"error": "Trip share link is invalid or expired"})
            return
        try:
            trip_data = get_shared_trip(trip_token, _upstream_host_header())
        except LookupError:
            self._send_json(404, {"error": "Trip share link is invalid or expired"})
            return
        except Exception as exc:
            log_event(logging.ERROR, "ical.trip_fetch_failed", error=str(exc))
            self._send_json(502, {"error": "Trip data unavailable"})
            return

        ical_body = _build_ical_feed(trip_data)
        filename = f"guest-portal-calendar.ics"
        trip_title = str((trip_data.get("trip") or {}).get("title") or (trip_data.get("trip") or {}).get("name") or "trip")
        safe_name = re.sub(r"[^A-Za-z0-9]", "-", trip_title).strip("-") or "trip"
        filename = f"{safe_name}.ics"

        body_bytes = ical_body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, private")
        self.send_header("Pragma", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body_bytes)

    def _security_headers(self):
        """Return the security headers applied to guest/static/API responses."""
        csp = (
            "default-src 'self'; "
            "script-src 'self' https://api.mapbox.com 'wasm-unsafe-eval'; "
            "style-src 'self' 'unsafe-inline' https://api.mapbox.com; "
            "img-src 'self' data: blob: https:; "
            "font-src 'self' data:; "
            "connect-src 'self' https://*.tiles.mapbox.com https://api.mapbox.com https://events.mapbox.com https://routing.openstreetmap.de; "
            "worker-src blob:; child-src blob:; media-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; upgrade-insecure-requests"
        )
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Permitted-Cross-Domain-Policies", "none")
        self.send_header("Origin-Agent-Cluster", "?1")
        if PUBLIC_ORIGIN.startswith("https://"):
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def _send_json(self, status: int, obj: dict, extra_headers: dict[str, str] | None = None):
        """Send a compact JSON response with security and request-correlation headers."""
        body = json_bytes(obj)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        request_id = getattr(self, "_request_id", None)
        if request_id:
            self.send_header("X-Guest-Request-ID", request_id)
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _set_session_cookie(self, sid: str) -> str:
        """Build the hardened Set-Cookie value for a new guest session."""
        max_age = SESSION_TTL_SECONDS if SESSION_TTL_SECONDS > 0 else SESSION_COOKIE_MAX_AGE_SECONDS
        return f"{SESSION_COOKIE_NAME}={sid}; Path={COOKIE_PATH}; Max-Age={max_age}; HttpOnly; Secure; SameSite=Strict"

    def _clear_session_cookie(self) -> str:
        """Build a Set-Cookie value that expires the guest session cookie."""
        return f"{SESSION_COOKIE_NAME}=; Path={COOKIE_PATH}; Max-Age=0; HttpOnly; Secure; SameSite=Strict"

    def _request_content_length(self) -> int:
        """Parse a bounded non-negative Content-Length from the current request."""
        try:
            return max(0, int(self.headers.get("Content-Length", "0") or "0"))
        except Exception:
            return 0

    def _drain_unread_request_body(self, max_discard: int = 64 * 1024) -> None:
        """Consume a bounded unread request body before HTTP/1.1 connection reuse."""
        if getattr(self, "_request_body_consumed", False):
            return
        length = self._request_content_length()
        if length <= 0:
            self._request_body_consumed = True
            return
        if length > max_discard:
            # Do not spend unbounded time draining an attacker-controlled body.
            # Closing this connection prevents those bytes from becoming the
            # next parsed HTTP request.
            self.close_connection = True
            log_event(logging.WARNING, "http.request_body_not_drained", content_length=length, reason="too-large", connection="close")
            return
        remaining = length
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(8192, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            self._request_body_consumed = remaining == 0
            if FULL_LOGGING and length:
                log_event(logging.INFO, "http.request_body_drained", bytes=length-remaining, expected=length)
        except Exception as exc:
            self.close_connection = True
            log_event(logging.WARNING, "http.request_body_drain_failed", error=str(exc), exc_type=type(exc).__name__, connection="close")

    def _read_json_body(self, max_length: int = MAX_SESSION_BODY) -> dict | None:
        """Read and decode a bounded JSON request body, returning None on invalid input."""
        length = self._request_content_length()
        if length <= 0:
            self._request_body_consumed = True
            return None
        if length > max_length:
            self.close_connection = True
            return None
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        raw = self.rfile.read(length)
        self._request_body_consumed = True
        if ctype != "application/json":
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _require_session(self) -> tuple[str, dict] | None:
        """Validate the guest cookie and send a 401 response when authorization is absent."""
        sid, item = _session_from_cookie(self.headers.get("Cookie"))
        if not sid or not item:
            # POST telemetry can reach this path after a process restart or if
            # the browser cookie was removed. Consume its body before sending
            # the 401 so keep-alive cannot parse JSON bytes as the next method.
            if self.command == "POST":
                self._drain_unread_request_body()
            log_event(logging.WARNING, "session.auth_failed", reason="missing")
            self._send_json(401, {"error": "Guest session required"})
            return None
        if FULL_LOGGING:
            now = time.time()
            expires = item.get("expires")
            expires_in = "never" if expires is None else max(0, int(float(expires)-now))
            log_event(logging.INFO, "session.auth_ok", session=_token_ref(sid), trip_id=item.get("trip_id") or "unknown", age_seconds=max(0, int(now-float(item.get("created", now)))), expires_in=expires_in, has_journey=bool(item.get("journey")))
        return sid, item

    def _serve_static(self, request_path: str):
        """Resolve and serve a file strictly beneath PUBLIC_ROOT with SPA fallback."""
        raw = unquote(request_path)
        rel = "index.html" if raw in ("", "/") else raw.lstrip("/")
        candidate = (PUBLIC_ROOT / rel).resolve()
        try:
            candidate.relative_to(PUBLIC_ROOT)
        except ValueError:
            return self._send_json(403, {"error": "Forbidden"})
        fallback = False
        if not candidate.is_file():
            candidate = PUBLIC_ROOT / "index.html"
            fallback = True
            if not candidate.is_file():
                log_event(logging.WARNING, "static.not_found", requested=raw)
                return self._send_json(404, {"error": "Not found"})
        ctype, _ = mimetypes.guess_type(str(candidate))
        ctype = ctype or "application/octet-stream"
        size = candidate.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype in ("application/javascript", "application/json") else ""))
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store" if candidate.name in {"index.html", "config.js", "app.js", "style.css"} else "public, max-age=3600")
        request_id = getattr(self, "_request_id", None)
        if request_id:
            self.send_header("X-Guest-Request-ID", request_id)
        if LOG_STATIC_REQUESTS:
            log_event(logging.INFO if FULL_LOGGING else logging.DEBUG, "static.serve", requested=raw or "/", served=candidate.name, fallback=fallback, content_type=ctype, bytes=size)
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            with candidate.open("rb") as fh:
                while True:
                    chunk = fh.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

    def _proxy_journey_media(self, photo_id: str, variant: str, session: dict):
        """Proxy an authorized Journey thumbnail/original without exposing native share tokens."""
        journey_token = str(session.get("journey") or "")
        if not journey_token or not TOKEN_RE.fullmatch(journey_token) or not RID_RE.fullmatch(photo_id) or variant not in {"thumbnail", "original"}:
            return self._send_json(404, {"error": "Photo not found"})
        target = f"/api/public/journey/{quote(journey_token, safe='')}/photos/{quote(photo_id, safe='')}/{variant}"
        conn = http.client.HTTPConnection(TREK_HOST, TREK_PORT, timeout=UPSTREAM_TIMEOUT)
        headers = {
            "Host": _upstream_host_header(),
            "Accept": self.headers.get("Accept", "*/*"),
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": _upstream_host_header(),
        }
        for key in ("Range", "If-None-Match", "If-Modified-Since"):
            value = self.headers.get(key)
            if value:
                headers[key] = value
        try:
            started = time.monotonic()
            log_event(logging.INFO, "trek.media_proxy_start", photo_id=photo_id, variant=variant)
            conn.request("GET", target, headers=headers)
            res = conn.getresponse()
            log_event(logging.INFO if 200 <= res.status < 400 else logging.WARNING,
                      "trek.media_proxy_response", photo_id=photo_id, variant=variant,
                      status=res.status, content_type=res.getheader("Content-Type", "unknown"),
                      content_length=res.getheader("Content-Length", "unknown"),
                      elapsed_ms=int((time.monotonic() - started) * 1000))
            self.send_response(res.status)
            allowed = {"content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified"}
            for key, value in res.getheaders():
                if key.lower() in allowed:
                    self.send_header(key, value)
            self.send_header("Cache-Control", "no-store")
            request_id = getattr(self, "_request_id", None)
            if request_id:
                self.send_header("X-Guest-Request-ID", request_id)
            self._security_headers()
            self.end_headers()
            sent = 0
            if self.command != "HEAD":
                while True:
                    chunk = res.read(64 * 1024)
                    if not chunk:
                        break
                    sent += len(chunk)
                    self.wfile.write(chunk)
            if FULL_LOGGING:
                log_event(logging.INFO, "trek.media_proxy_complete", photo_id=photo_id, variant=variant, bytes_sent=sent, status=res.status)
        except Exception as exc:
            log_event(logging.ERROR, "trek.media_proxy_failed", photo_id=photo_id, variant=variant, error=str(exc))
            try:
                self._send_json(502, {"error": "Media backend unavailable"})
            except Exception:
                pass
        finally:
            conn.close()

    def _client_log(self, sid: str, session: dict):
        """Validate, sanitize and record one browser telemetry event."""
        if not CLIENT_EVENT_LOGGING:
            return self._send_json(200, {"ok": True, "logging": False})
        if not _origin_allowed(self.headers.get("Origin")):
            log_event(logging.WARNING, "client.event_rejected", reason="origin")
            return self._send_json(403, {"error": "Invalid request origin"})
        session_ref = _token_ref(sid)
        if not _client_event_rate_allowed(session_ref):
            log_event(logging.WARNING, "client.event_rate_limited", session=session_ref, limit_per_minute=CLIENT_EVENT_RATE_PER_MINUTE)
            return self._send_json(429, {"error": "Client logging rate limit exceeded"}, {"Retry-After": "60"})
        body = self._read_json_body(CLIENT_EVENT_MAX_BODY)
        if not body:
            log_event(logging.WARNING, "client.event_rejected", reason="invalid-body", session=session_ref)
            return self._send_json(400, {"error": "Invalid client log event"})
        name = str(body.get("event") or "").strip().lower()
        if not _CLIENT_EVENT_RE.fullmatch(name):
            log_event(logging.WARNING, "client.event_rejected", reason="invalid-name", session=session_ref)
            return self._send_json(400, {"error": "Invalid client log event"})
        level_name = str(body.get("level") or "info").strip().lower()
        level = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING, "warning": logging.WARNING, "error": logging.ERROR}.get(level_name, logging.INFO)
        fields = _sanitize_client_fields(body.get("fields"))
        _metric("client_events")
        log_event(level, "client.event", name=name, session=session_ref, trip_id=session.get("trip_id") or "unknown", **{f"ui_{k}": v for k,v in fields.items()})
        return self._send_json(200, {"ok": True})

    def _create_guest_session(self):
        """Validate native public shares and exchange them for a hardened guest session."""
        log_event(logging.INFO, "session.create_attempt", origin=self.headers.get("Origin") or "missing")
        if not _origin_allowed(self.headers.get("Origin")):
            log_event(logging.WARNING, "session.origin_rejected", origin=self.headers.get("Origin") or "missing")
            return self._send_json(403, {"error": "Invalid request origin"})
        if not _session_rate_allowed():
            log_event(logging.WARNING, "session.rate_limited", limit_per_minute=SESSION_CREATE_PER_MINUTE)
            return self._send_json(429, {"error": "Too many session requests"}, {"Retry-After": "60"})
        body = self._read_json_body()
        if body is None:
            return self._send_json(400, {"error": "Invalid request"})
        trip_token = str(body.get("trip") or "").strip()
        journey_token = str(body.get("journey") or "").strip()
        title = str(body.get("title") or "").strip()[:160]
        enable_ical = bool(body.get("enable_ical"))
        if not TOKEN_RE.fullmatch(trip_token):
            return self._send_json(400, {"error": "Invalid trip share link"})
        if journey_token and not TOKEN_RE.fullmatch(journey_token):
            return self._send_json(400, {"error": "Invalid Journey share link"})
        try:
            shared = get_shared_trip(trip_token, _upstream_host_header())
        except LookupError:
            return self._send_json(404, {"error": "Trip share link is invalid or expired"})
        except Exception as exc:
            log_event(logging.ERROR, "session.trip_validation_failed", trip_share=_token_ref(trip_token), error=str(exc))
            return self._send_json(502, {"error": "Unable to validate trip share"})
        if journey_token:
            try:
                get_shared_journey(journey_token, _upstream_host_header())
            except LookupError:
                return self._send_json(404, {"error": "Journey share link is invalid or expired"})
            except Exception as exc:
                log_event(logging.ERROR, "session.journey_validation_failed", journey_share=_token_ref(journey_token), error=str(exc))
                return self._send_json(502, {"error": "Unable to validate Journey share"})
        trip = shared.get("trip") or {}
        sid, _item = _new_session(trip_token, journey_token, title, trip.get("id"), enable_ical)
        _metric("sessions_created")
        log_event(logging.INFO, "session.created", session=_token_ref(sid), trip_share=_token_ref(trip_token), journey=bool(journey_token), ical=enable_ical, expires_in=(SESSION_TTL_SECONDS if SESSION_TTL_SECONDS > 0 else "never"))
        return self._send_json(200, {"ok": True, "hasJourney": bool(journey_token)}, {"Set-Cookie": self._set_session_cookie(sid)})

    def _logout(self):
        """Delete the current memory-only session and expire its browser cookie."""
        sid, _item = _session_from_cookie(self.headers.get("Cookie"))
        if sid:
            with _sessions_lock:
                _sessions.pop(sid, None)
            log_event(logging.INFO, "session.deleted", session=_token_ref(sid))
        return self._send_json(200, {"ok": True}, {"Set-Cookie": self._clear_session_cookie()})

    def _trip_json(self, session: dict):
        """Return the currently authorized native TREK public trip payload."""
        token = str(session.get("trip") or "")
        started = time.monotonic()
        log_event(logging.INFO, "trip.read_start", trip_share=_token_ref(token))
        try:
            data = get_shared_trip(token, _upstream_host_header())
            log_event(logging.INFO, "trip.read_complete", trip_share=_token_ref(token),
                      trip_id=(data.get("trip") or {}).get("id"), days=len(data.get("days") or []),
                      reservations=len(data.get("reservations") or []), accommodations=len(data.get("accommodations") or []),
                      elapsed_ms=int((time.monotonic() - started) * 1000))
            return self._send_json(200, _guest_trip_payload(data))
        except LookupError:
            log_event(logging.WARNING, "trip.read_invalid", trip_share=_token_ref(token))
            return self._send_json(404, {"error": "Trip share link is invalid or expired"})
        except Exception as exc:
            log_event(logging.ERROR, "trip.read_failed", trip_share=_token_ref(token), error=str(exc), exc_type=type(exc).__name__)
            return self._send_json(502, {"error": "Trip data unavailable"})

    def _journey_json(self, session: dict):
        """Return the currently authorized native Journey public payload."""
        token = str(session.get("journey") or "")
        if not token:
            return self._send_json(404, {"error": "No Journey share is configured"})
        started = time.monotonic()
        log_event(logging.INFO, "journey.read_start", journey_share=_token_ref(token))
        try:
            data = get_shared_journey(token, _upstream_host_header())
            log_event(logging.INFO, "journey.read_complete", journey_share=_token_ref(token),
                      gallery=len(data.get("gallery") or []), entries=len(data.get("entries") or []),
                      elapsed_ms=int((time.monotonic() - started) * 1000))
            return self._send_json(200, data)
        except LookupError:
            log_event(logging.WARNING, "journey.read_invalid", journey_share=_token_ref(token))
            return self._send_json(404, {"error": "Journey share link is invalid or expired"})
        except Exception as exc:
            log_event(logging.ERROR, "journey.read_failed", journey_share=_token_ref(token), error=str(exc), exc_type=type(exc).__name__)
            return self._send_json(502, {"error": "Journey data unavailable"})

    def _flight_status(self, reservation_id: str, session: dict):
        """Validate a shared flight reservation and return cached/live flight status."""
        token = str(session.get("trip") or "")
        log_event(logging.INFO, "flight.request", reservation_id=reservation_id, session_trip=_token_ref(token))
        if not RID_RE.fullmatch(reservation_id):
            return self._send_json(400, {"error": "Invalid reservation id"})
        try:
            shared = get_shared_trip(token, _upstream_host_header())
        except LookupError:
            return self._send_json(404, {"error": "Trip share link is invalid or expired"})
        except Exception as exc:
            log_event(logging.ERROR, "flight.trip_validation_failed", error=str(exc))
            return self._send_json(502, {"error": "Unable to validate shared trip"})
        if not (shared.get("permissions") or {}).get("share_bookings"):
            return self._send_json(403, {"error": "Bookings are not shared for this trip"})
        reservation = next((item for item in (shared.get("reservations") or []) if isinstance(item, dict) and str(item.get("id")) == reservation_id), None)
        if reservation is None:
            return self._send_json(404, {"error": "Reservation not found"})
        if reservation_kind(reservation) != "flight":
            return self._send_json(404, {"error": "Reservation is not a flight"})
        trip_id = (shared.get("trip") or {}).get("id")
        if trip_id is None:
            return self._send_json(502, {"error": "Trip data unavailable"})
        try:
            payload = get_live_flight_payload(reservation, reservation_id, str(trip_id), shared)
        except Exception as exc:
            log_event(logging.ERROR, "flight.live_refresh_failed", reservation_id=reservation_id, trip_id=trip_id, error=str(exc))
            return self._send_json(502, {"error": "Live flight data temporarily unavailable"})
        if payload is None:
            log_event(logging.WARNING, "flight.unavailable", reservation_id=reservation_id, trip_id=trip_id, key_source=aerodatabox_key_source())
            return self._send_json(503, {"error": "Live flight data is not configured"})
        if isinstance(payload.get("_guestLive"), dict):
            payload["_guestLive"].pop("error", None)
        if isinstance(payload.get("errors"), list):
            payload["errors"] = ["Some live flight details could not be refreshed"] if payload["errors"] else []
        live_meta = payload.get("_guestLive") if isinstance(payload.get("_guestLive"), dict) else {}
        plan = _flight_runtime_plan(payload, int(live_meta.get("apiFetchedAt") or 0) or None)
        _log_flight_plan("flight.response", plan, str(trip_id), reservation_id, "respond",
                         source=live_meta.get("source"), configured=live_meta.get("configured"),
                         browser_refresh_after=live_meta.get("refreshAfterSeconds"),
                         api_refresh_after=live_meta.get("apiRefreshAfterSeconds"))
        return self._send_json(200, payload)

    def _photo_dates(self, session: dict):
        """Resolve capture dates for authorized Journey gallery items in parallel."""
        token = str(session.get("journey") or "")
        if not token:
            return self._send_json(404, {"error": "No Journey share is configured"})
        photo_started = time.monotonic()
        log_event(logging.INFO, "photos.date_request", journey_share=_token_ref(token), immich_configured=immich_configured())
        try:
            journey = get_shared_journey(token, _upstream_host_header())
        except LookupError:
            return self._send_json(404, {"error": "Journey share link is invalid or expired"})
        except Exception as exc:
            log_event(logging.ERROR, "photos.journey_validation_failed", error=str(exc))
            return self._send_json(502, {"error": "Journey data unavailable"})
        if not (journey.get("permissions") or {}).get("share_gallery"):
            return self._send_json(403, {"error": "Journey gallery is not shared"})
        gallery = journey.get("gallery") or []
        targets: list[dict[str, str]] = []
        seen: set[str] = set()
        for photo in gallery[:500]:
            if not isinstance(photo, dict):
                continue
            pid = photo.get("photo_id") if photo.get("photo_id") is not None else photo.get("id")
            if pid is None:
                continue
            pid_text = str(pid)
            if not RID_RE.fullmatch(pid_text) or pid_text in seen:
                continue
            seen.add(pid_text)
            targets.append({
                "photo_id": pid_text,
                "provider": str(photo.get("provider") or "").strip().lower(),
                "asset_id": "" if photo.get("asset_id") is None else str(photo.get("asset_id")).strip(),
            })
        dates: dict[str, str] = {}
        immich_checked = immich_resolved = fallback_checked = 0
        parent_log_context = dict(getattr(_log_context, "fields", None) or {})

        def resolve(item: dict[str, str]) -> tuple[str, str | None, str]:
            """Resolve one Journey photo date using Immich first and embedded metadata fallback."""
            # ThreadPoolExecutor workers do not inherit thread-local request
            # context automatically; copy the correlation id for coherent logs.
            _log_context.fields = dict(parent_log_context)
            pid = item["photo_id"]
            if item["provider"] == "immich" and item["asset_id"] and immich_configured():
                value = get_immich_asset_capture_date(item["asset_id"])
                if value:
                    return pid, value, "immich"
            value = get_photo_capture_date(token, pid, _upstream_host_header())
            return pid, value, "fallback"

        if targets:
            with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
                jobs = {pool.submit(resolve, item): item for item in targets}
                for future in as_completed(jobs):
                    item = jobs[future]
                    if item["provider"] == "immich" and item["asset_id"] and immich_configured():
                        immich_checked += 1
                    try:
                        pid, value, source = future.result()
                    except Exception as exc:
                        log_event(logging.WARNING, "photos.date_resolve_failed", photo_id=item["photo_id"], provider=item["provider"] or "unknown", error=str(exc))
                        pid, value, source = item["photo_id"], None, "error"
                    if source == "immich" and value:
                        immich_resolved += 1
                    elif source == "fallback":
                        fallback_checked += 1
                    if value:
                        dates[pid] = value
        log_event(logging.INFO, "photos.date_response", journey_share=_token_ref(token), checked=len(targets), resolved=len(dates), immich_checked=immich_checked, immich_resolved=immich_resolved, fallback_checked=fallback_checked, unresolved=max(0, len(targets)-len(dates)), elapsed_ms=int((time.monotonic()-photo_started)*1000))
        return self._send_json(200, {"dates": dates})

    def do_HEAD(self):
        """Handle HEAD requests through the same safe GET dispatcher."""
        self._dispatch_get()

    def do_GET(self):
        """Handle GET requests through the central route dispatcher."""
        self._dispatch_get()

    def do_POST(self):
        """Dispatch the small allowlist of state-changing/session POST endpoints."""
        self._request_body_consumed = False
        _begin_log_context(self, self.path)
        path = urlsplit(self.path).path
        if path == "/api/session":
            return self._create_guest_session()
        if path == "/api/logout":
            if not _origin_allowed(self.headers.get("Origin")):
                return self._send_json(403, {"error": "Invalid request origin"})
            return self._logout()
        if path == "/api/client-log":
            auth = self._require_session()
            if auth is None:
                return
            sid, session = auth
            return self._client_log(sid, session)
        self._drain_unread_request_body()
        log_event(logging.WARNING, "http.route_not_found", method=self.command, target=_safe_request_target(self.path))
        return self._send_json(404, {"error": "Not found"})

    def do_OPTIONS(self):
        """Reject CORS preflight/unsupported methods because cross-origin API use is not allowed."""
        _begin_log_context(self, self.path)
        self._send_json(405, {"error": "Method not allowed"}, {"Allow": "GET, HEAD, POST"})

    def _dispatch_get(self):
        """Dispatch health, authenticated guest APIs and static-file GET/HEAD requests."""
        _begin_log_context(self, self.path)
        raw_path = urlsplit(self.path).path
        # Strip GUEST_PLUGIN_PATH prefix for route matching.
        path = raw_path
        if GUEST_PLUGIN_PATH != "/" and raw_path.startswith(GUEST_PLUGIN_PATH):
            path = raw_path[len(GUEST_PLUGIN_PATH) - 1:]
            if not path.startswith("/"):
                path = "/" + path
        if path == "/health":
            return self._send_json(200, {"ok": True, "version": VERSION})
        m = re.fullmatch(r"/ical/([A-Za-z0-9_-]{8,256})", path)
        if m:
            return self._ical_feed(m.group(1))
        if path.startswith("/api/"):
            auth = self._require_session()
            if auth is None:
                return
            _sid, session = auth
            if path == "/api/trip":
                return self._trip_json(session)
            if path == "/api/ical-link":
                if not session.get("ical"):
                    return self._send_json(403, {"error": "iCal is not enabled for this portal"})
                trip_token = str(session.get("trip") or "")
                webcal_url = f"{PUBLIC_ORIGIN}{GUEST_PLUGIN_PATH}ical/{trip_token}"
                return self._send_json(200, {"webcal_url": webcal_url})
            if path == "/api/journey":
                return self._journey_json(session)
            if path == "/api/photo-dates":
                return self._photo_dates(session)
            if path.startswith("/api/flights/"):
                return self._flight_status(unquote(path[len("/api/flights/"):]), session)
            m = re.fullmatch(r"/api/photos/([A-Za-z0-9_-]{1,64})/(thumbnail|original)", path)
            if m:
                return self._proxy_journey_media(m.group(1), m.group(2), session)
            log_event(logging.WARNING, "http.route_not_found", method=self.command, target=_safe_request_target(self.path))
            return self._send_json(404, {"error": "Not found"})
        return self._serve_static(path)


def _persistent_cache_stats() -> tuple[int | str, int | str]:
    """Return persistent cache row and file-size statistics for heartbeat logging."""
    if not _guest_cache_ready:
        return "unavailable", "unavailable"
    try:
        with _guest_cache_lock, sqlite3.connect(GUEST_CACHE_DB, timeout=2) as con:
            rows = int(con.execute("SELECT COUNT(*) FROM live_flight_cache").fetchone()[0])
        size = GUEST_CACHE_DB.stat().st_size if GUEST_CACHE_DB.exists() else 0
        return rows, size
    except Exception:
        return "error", "error"


def _runtime_heartbeat(stop_event: threading.Event):
    """Emit periodic bounded runtime/cache/metric health events until shutdown."""
    while not stop_event.wait(LOG_HEARTBEAT_SECONDS):
        try:
            with _sessions_lock:
                sessions = len(_sessions)
            with _share_cache_lock:
                share_cache = len(_share_cache)
            with _photo_date_cache_lock:
                photo_cache = len(_photo_date_cache)
            with _immich_date_cache_lock:
                immich_cache = len(_immich_date_cache)
            with _live_flight_cache_lock:
                live_cache = len(_live_flight_cache)
            db_rows, db_bytes = _persistent_cache_stats()
            try:
                disk = shutil.disk_usage(GUEST_CACHE_DB.parent)
                disk_free = disk.free
            except Exception:
                disk_free = "unknown"
            try:
                rss_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            except Exception:
                rss_kb = "unknown"
            metrics = _metrics_snapshot()
            log_event(logging.INFO, "runtime.heartbeat",
                      uptime_seconds=int(time.monotonic()-_started_monotonic), active_sessions=sessions,
                      threads=threading.active_count(), rss_kb=rss_kb,
                      share_cache_entries=share_cache, photo_cache_entries=photo_cache,
                      immich_cache_entries=immich_cache, live_flight_cache_entries=live_cache,
                      persistent_cache_rows=db_rows, persistent_cache_bytes=db_bytes,
                      cache_disk_free_bytes=disk_free, **metrics)
        except Exception as exc:
            log_event(logging.WARNING, "runtime.heartbeat_failed", error=str(exc), exc_type=type(exc).__name__)


def main():
    """Validate configuration, initialize state and run the threaded HTTP server."""
    if not PUBLIC_ROOT.is_dir():
        raise SystemExit(f"PUBLIC_ROOT does not exist: {PUBLIC_ROOT}")
    if not _public_origin_valid():
        raise SystemExit("PUBLIC_ORIGIN must be an https:// origin without a path, query, or fragment")
    if not _https_origin_valid(TREK_PUBLIC_ORIGIN):
        raise SystemExit("TREK_PUBLIC_ORIGIN must be an https:// origin without a path, query, or fragment")
    if not COOKIE_PATH.startswith("/") or not COOKIE_PATH.endswith("/"):
        raise SystemExit("COOKIE_PATH must start and end with /")
    init_guest_cache_db()
    # Pre-load airport timezone database at startup to confirm it's available
    airport_tz_count = len(_load_airport_tz_database())
    log_event(logging.INFO, "startup.airport_timezone_db", airport_count=airport_tz_count, db_path=os.path.join(os.path.dirname(GUEST_CACHE_DB), "airport_tz.db"))
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    log_event(logging.INFO, "startup", version=VERSION, listen=f"{LISTEN_HOST}:{LISTEN_PORT}", public_root=PUBLIC_ROOT, log_level=LOG_LEVEL, public_origin=PUBLIC_ORIGIN, trek_public_origin=TREK_PUBLIC_ORIGIN, guest_plugin_path=GUEST_PLUGIN_PATH, cookie_path=COOKIE_PATH)
    invalid_proxy_entries = [part for part in re.split(r"[\s,]+", TRUSTED_PROXY_CIDRS_RAW) if part.strip() and not _parse_trusted_proxy_networks(part.strip())]
    log_event(logging.INFO, "startup.runtime", uid=os.getuid() if hasattr(os, "getuid") else "unknown", gid=os.getgid() if hasattr(os, "getgid") else "unknown", client_ip_logging=LOG_CLIENT_IP, session_ttl=(SESSION_TTL_SECONDS if SESSION_TTL_SECONDS > 0 else "disabled"), session_cookie_max_age=SESSION_COOKIE_MAX_AGE_SECONDS, session_max=SESSION_MAX, session_rate_per_minute=SESSION_CREATE_PER_MINUTE)
    log_event(logging.INFO, "startup.logging", format=LOG_FORMAT, full_logging=FULL_LOGGING, static_requests=LOG_STATIC_REQUESTS, safe_request_headers=LOG_SAFE_REQUEST_HEADERS, client_event_logging=CLIENT_EVENT_LOGGING, client_event_rate_per_minute=CLIENT_EVENT_RATE_PER_MINUTE, heartbeat_seconds=LOG_HEARTBEAT_SECONDS)
    log_event(logging.INFO, "startup.proxy_logging", trust_proxy_headers=TRUST_PROXY_HEADERS, client_ip_header=CLIENT_IP_HEADER, trusted_proxy_networks=len(_TRUSTED_PROXY_NETWORKS), invalid_proxy_entries=len(invalid_proxy_entries), proxy_details=LOG_PROXY_DETAILS)
    log_event(logging.INFO, "startup.flight_scheduler", api_window_hours=FLIGHT_API_WINDOW_HOURS, upcoming_poll_seconds=FLIGHT_UPCOMING_POLL_SECONDS, active_poll_seconds=FLIGHT_ACTIVE_POLL_SECONDS, error_poll_seconds=FLIGHT_ERROR_POLL_SECONDS, aerodatabox_min_interval=AERODATABOX_MIN_INTERVAL, rate_limit_retries=AERODATABOX_429_RETRIES)
    log_event(logging.INFO, "startup.timeouts", trek_seconds=UPSTREAM_TIMEOUT, aerodatabox_seconds=AERODATABOX_TIMEOUT, adsb_seconds=ADSB_TIMEOUT, immich_seconds=IMMICH_TIMEOUT)
    log_event(logging.INFO, "integration.aerodatabox", configured=aerodatabox_configured(), key_source=aerodatabox_key_source())
    log_event(logging.INFO, "integration.immich", configured=immich_configured(), verify_tls=IMMICH_VERIFY_TLS, date_cache_ttl=IMMICH_DATE_CACHE_TTL)
    log_event(logging.INFO, "integration.persistent_cache", enabled=_guest_cache_ready, db=GUEST_CACHE_DB, max_rows=GUEST_CACHE_MAX_ROWS, error=_guest_cache_error or "none")
    log_event(logging.INFO, "integration.mapbox_config", config_file_exists=(PUBLIC_ROOT / "config.js").is_file())
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(target=_runtime_heartbeat, args=(heartbeat_stop,), name="guest-portal-heartbeat", daemon=True)
    heartbeat_thread.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log_event(logging.INFO, "shutdown", reason="keyboard-interrupt")
    except Exception as exc:
        log_event(logging.CRITICAL, "server.crash", error=str(exc), exc_type=type(exc).__name__)
        raise
    finally:
        try:
            heartbeat_stop.set()
        except Exception:
            pass
        server.server_close()


if __name__ == "__main__":
    main()
