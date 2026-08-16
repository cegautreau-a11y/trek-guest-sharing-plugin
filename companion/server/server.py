#!/usr/bin/env python3
"""TREK Guest Portal companion gateway.

Serves the static guest portal, proxies TREK's anonymous share endpoints to the
TREK `app` service, and provides server-side integrations for live flight status and Immich photo dates.

The hardened deployment does not mount TREK plugin databases at runtime. Guest
requests are authorized by short-lived HttpOnly sessions created from native
TREK/Journey public-share capabilities.
"""

from __future__ import annotations

import http.client
import hashlib
import logging
import json
import mimetypes
import os
import pathlib
import re
import secrets
import sqlite3
import sys
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
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
COOKIE_PATH = os.environ.get("COOKIE_PATH", "/guest-portal/").strip() or "/guest-portal/"
SESSION_TTL_SECONDS = max(300, min(int(os.environ.get("SESSION_TTL_SECONDS", "43200")), 86400))
SESSION_MAX = max(32, min(int(os.environ.get("SESSION_MAX", "2048")), 10000))
SESSION_CREATE_PER_MINUTE = max(10, min(int(os.environ.get("SESSION_CREATE_PER_MINUTE", "120")), 2000))
SESSION_COOKIE_NAME = "trek_guest_session"

# Optional legacy compatibility: one explicitly-mounted Flight Tracker DB may
# be provided, but the hardened default mounts no plugin database at runtime.
# Use the one-shot key extractor and AERODATABOX_API_KEY_FILE instead.
_TRACKER_DB_RAW = os.environ.get("FLIGHT_TRACKER_DB", "").strip()
TRACKER_DB = pathlib.Path(_TRACKER_DB_RAW).resolve() if _TRACKER_DB_RAW else None

SHARE_CACHE_TTL = 30.0
MAX_JSON_BODY = 12 * 1024 * 1024
MAX_SESSION_BODY = 16 * 1024
PHOTO_METADATA_PREFIX_BYTES = int(os.environ.get("PHOTO_METADATA_PREFIX_BYTES", str(2 * 1024 * 1024)))
PHOTO_DATE_CACHE_TTL = 6 * 3600.0
IMMICH_URL = os.environ.get("IMMICH_URL", "").strip().rstrip("/")
IMMICH_API_KEY = _read_secret("IMMICH_API_KEY", "IMMICH_API_KEY_FILE")
IMMICH_VERIFY_TLS = os.environ.get("IMMICH_VERIFY_TLS", "true").strip().lower() not in {"0", "false", "no", "off"}
IMMICH_TIMEOUT = float(os.environ.get("IMMICH_TIMEOUT", "15"))
IMMICH_DATE_CACHE_TTL = float(os.environ.get("IMMICH_DATE_CACHE_TTL", str(24 * 3600)))
# Hardened default: read the AeroDataBox key from a mounted secret file.
# An explicitly-mounted Flight Tracker DB is supported only as an optional
# compatibility fallback and is not part of the recommended deployment.
AERODATABOX_API_KEY_ENV = _read_secret("AERODATABOX_API_KEY", "AERODATABOX_API_KEY_FILE")
AERODATABOX_API_KEY_SOURCE = _secret_source("AERODATABOX_API_KEY", "AERODATABOX_API_KEY_FILE", AERODATABOX_API_KEY_ENV)
AERODATABOX_KEY_CACHE_TTL = float(os.environ.get("AERODATABOX_KEY_CACHE_TTL", "30"))
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

VERSION = "1.0.2"

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
_aero_key_cache: tuple[float, str, str] = (0.0, "", "none")

_aero_key_lock = threading.Lock()
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()
_session_create_times: list[float] = []
_session_rate_lock = threading.Lock()

_logger = logging.getLogger("trek-guest-portal")
_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
_logger.handlers.clear()
_logger.addHandler(_handler)
_logger.propagate = False


def _token_ref(value: str | None) -> str:
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
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).replace("\n", " ").replace("\r", " ")
        if len(text) > 500:
            text = text[:497] + "..."
        if re.search(r"\s|=|\"", text):
            text = json.dumps(text, ensure_ascii=False)
        parts.append(f"{key}={text}")
    _logger.log(level, " ".join(parts))


HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


def json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


_guest_cache_ready = False
_guest_cache_error = "not-initialized"
_guest_cache_lock = threading.Lock()


def init_guest_cache_db() -> bool:
    """Initialize Guest Portal's own persistent SQLite cache.

    Failure is non-fatal: the portal continues using the in-memory cache and
    Flight Tracker's read-only cache.
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
    return {
        "enabled": bool(_guest_cache_ready),
        "path": str(GUEST_CACHE_DB),
        "error": _guest_cache_error or None,
    }


def read_persistent_live_cache(trip_id: str, reservation_id: str) -> tuple[int, dict] | None:
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
    """Return a fresh cached payload with updated age/next-refresh metadata."""
    now_ms = int(time.time() * 1000)
    ttl = _flight_ttl_seconds(payload)
    age_ms = max(0, now_ms - int(fetched_ms))
    if ttl != 0 and age_ms >= ttl * 1000:
        return None
    out = json.loads(json.dumps(payload))
    remaining = 0 if ttl == 0 else max(15, int((ttl * 1000 - age_ms + 999) // 1000))
    out.setdefault("_guestCache", {})["ageSeconds"] = age_ms // 1000
    out["_guestCache"]["fetchedAt"] = int(fetched_ms)
    out["_guestCache"]["stale"] = False
    out.setdefault("_guestLive", {})["refreshAfterSeconds"] = remaining
    out["_guestLive"].setdefault("fetchedAt", int(fetched_ms))
    log_event(logging.INFO, source_event, trip_id=trip_id, reservation_id=reservation_id, age_seconds=age_ms // 1000, refresh_after=remaining)
    return out


def upstream_get(path: str, incoming_host: str = "") -> tuple[int, list[tuple[str, str]], bytes]:
    conn = http.client.HTTPConnection(TREK_HOST, TREK_PORT, timeout=UPSTREAM_TIMEOUT)
    headers = {
        "Accept": "application/json,*/*;q=0.8",
        "Host": incoming_host or TREK_HOST,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": incoming_host or TREK_HOST,
    }
    try:
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        body = res.read(MAX_JSON_BODY + 1)
        if len(body) > MAX_JSON_BODY:
            raise RuntimeError("TREK response too large")
        return res.status, list(res.getheaders()), body
    finally:
        conn.close()


def upstream_read_prefix(path: str, limit: int, incoming_host: str = "") -> tuple[int, str, bytes]:
    """Read only the first *limit* bytes of an upstream response.

    Used for best-effort EXIF/XMP inspection of a photo already authorized by
    TREK's public Journey share token. Closing the connection after the prefix
    avoids downloading a full large original when metadata is near the start.
    """
    conn = http.client.HTTPConnection(TREK_HOST, TREK_PORT, timeout=UPSTREAM_TIMEOUT)
    headers = {
        "Accept": "image/*,*/*;q=0.8",
        "Host": incoming_host or TREK_HOST,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": incoming_host or TREK_HOST,
        "Range": f"bytes=0-{max(0, limit - 1)}",
    }
    try:
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        body = res.read(limit)
        ctype = res.getheader("Content-Type", "")
        return res.status, ctype, body
    finally:
        conn.close()


def _embedded_date_key(value: str) -> str | None:
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
        if pos < 0 or pos + 2 > len(data): return None
        return int.from_bytes(data[pos:pos + 2], endian)

    def u32(pos: int) -> int | None:
        if pos < 0 or pos + 4 > len(data): return None
        return int.from_bytes(data[pos:pos + 4], endian)

    if u16(tiff_start + 2) != 42:
        return None
    first_ifd = u32(tiff_start + 4)
    if first_ifd is None:
        return None

    seen: set[int] = set()

    def ascii_value(entry_pos: int, count: int, value_or_offset: int) -> str | None:
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
    return bool(IMMICH_URL and IMMICH_API_KEY)


def _immich_api_base() -> str:
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


def get_shared_trip(token: str, incoming_host: str = "") -> dict:
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
    status, _headers, body = upstream_get(f"/api/public/journey/{quote(token, safe='')}", incoming_host)
    if status != 200:
        raise LookupError(f"Journey share returned {status}")
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("Journey share returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Journey share returned an unexpected response")
    return data


def _public_origin_valid() -> bool:
    try:
        u = urlsplit(PUBLIC_ORIGIN)
        return u.scheme == "https" and bool(u.netloc) and not u.path.rstrip("/") and not u.query and not u.fragment
    except Exception:
        return False


def _upstream_host_header() -> str:
    try:
        return urlsplit(PUBLIC_ORIGIN).netloc or TREK_HOST
    except Exception:
        return TREK_HOST


def _origin_allowed(origin: str | None) -> bool:
    return bool(origin) and origin.rstrip("/") == PUBLIC_ORIGIN


def _session_cleanup(now: float | None = None) -> None:
    now = time.time() if now is None else now
    with _sessions_lock:
        expired = [sid for sid, item in _sessions.items() if float(item.get("expires", 0)) <= now]
        for sid in expired:
            _sessions.pop(sid, None)
        if len(_sessions) > SESSION_MAX:
            remove_count = len(_sessions) - SESSION_MAX
            oldest = sorted(_sessions.items(), key=lambda kv: float(kv[1].get("created", 0)))[:remove_count]
            for sid, _ in oldest:
                _sessions.pop(sid, None)


def _session_rate_allowed() -> bool:
    now = time.monotonic()
    with _session_rate_lock:
        cutoff = now - 60.0
        _session_create_times[:] = [t for t in _session_create_times if t >= cutoff]
        if len(_session_create_times) >= SESSION_CREATE_PER_MINUTE:
            return False
        _session_create_times.append(now)
        return True


def _new_session(trip_token: str, journey_token: str, title: str, trip_id: str | None) -> tuple[str, dict]:
    _session_cleanup()
    sid = secrets.token_urlsafe(32)
    now = time.time()
    item = {
        "created": now,
        "expires": now + SESSION_TTL_SECONDS,
        "trip": trip_token,
        "journey": journey_token,
        "title": title[:160],
        "trip_id": None if trip_id is None else str(trip_id),
    }
    with _sessions_lock:
        _sessions[sid] = item
    return sid, item


def _session_from_cookie(cookie_header: str | None) -> tuple[str | None, dict | None]:
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
        if not item or float(item.get("expires", 0)) <= now:
            _sessions.pop(sid, None)
            return None, None
        return sid, dict(item)


def _looks_like_flight_tracker_db(path: pathlib.Path) -> bool:
    try:
        uri = f"file:{quote(str(path))}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=1)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"cache", "flights", "kv"}.issubset(tables):
                return False
            cols = {r[1] for r in con.execute("PRAGMA table_info(cache)")}
            return {"reservation_id", "payload", "fetched_at"}.issubset(cols)
        finally:
            con.close()
    except Exception:
        return False


def find_tracker_db() -> str | None:
    """Return only the explicitly configured Flight Tracker DB, if valid."""
    try:
        if TRACKER_DB is not None and TRACKER_DB.is_file() and _looks_like_flight_tracker_db(TRACKER_DB):
            return str(TRACKER_DB)
    except Exception:
        pass
    return None


def read_tracker_cache(reservation_id: str, trip_id: str | None) -> dict | None:
    db_path = find_tracker_db()
    if not db_path:
        raise FileNotFoundError("Flight Tracker database not found")

    uri = f"file:{quote(db_path)}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=2)
    con.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(cache)")}
        if "trip_id" in cols:
            row = con.execute(
                "SELECT reservation_id, trip_id, payload, fetched_at FROM cache WHERE reservation_id = ? LIMIT 1",
                (reservation_id,),
            ).fetchone()
        else:
            row = con.execute(
                "SELECT reservation_id, payload, fetched_at FROM cache WHERE reservation_id = ? LIMIT 1",
                (reservation_id,),
            ).fetchone()
        if not row:
            log_event(logging.DEBUG, "flight_tracker.cache_miss", reservation_id=reservation_id, trip_id=trip_id)
            return None

        cached_trip = str(row["trip_id"]) if "trip_id" in row.keys() and row["trip_id"] is not None else None
        if cached_trip is not None and trip_id is not None and cached_trip != str(trip_id):
            return None

        try:
            payload = json.loads(row["payload"] or "null")
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None

        fetched_at = int(row["fetched_at"] or 0)
        age_seconds = max(0, int((time.time() * 1000 - fetched_at) / 1000)) if fetched_at else None
        # Do not pass plugin/admin capability hints through to an anonymous viewer.
        payload.pop("canSetKey", None)
        payload.pop("hasKey", None)
        payload["_guestCache"] = {
            "fetchedAt": fetched_at or None,
            "ageSeconds": age_seconds,
            "stale": age_seconds is not None and age_seconds > 1800,
        }
        log_event(logging.DEBUG, "flight_tracker.cache_hit", reservation_id=reservation_id, trip_id=trip_id, age_seconds=age_seconds, stale=payload["_guestCache"]["stale"])
        return payload
    finally:
        con.close()



def get_aerodatabox_key() -> tuple[str, str]:
    """Return (key, source) without ever exposing the key to the browser.

    Hardened path: read a mounted secret via AERODATABOX_API_KEY_FILE.
    An explicitly-mounted Flight Tracker DB is accepted only as a compatibility
    fallback; the recommended v1.0.2 deployment mounts no plugin data at runtime.
    """
    global _aero_key_cache

    if AERODATABOX_API_KEY_ENV:
        return AERODATABOX_API_KEY_ENV, AERODATABOX_API_KEY_SOURCE

    now = time.monotonic()
    with _aero_key_lock:
        checked_at, cached_key, cached_source = _aero_key_cache
        if now - checked_at < AERODATABOX_KEY_CACHE_TTL:
            return cached_key, cached_source

        key = ""
        source = "none"
        db_path = find_tracker_db()
        if db_path:
            try:
                con = sqlite3.connect(f"file:{quote(db_path)}?mode=ro", uri=True, timeout=2)
                try:
                    row = con.execute(
                        "SELECT v FROM kv WHERE k = 'aerodatabox_key' LIMIT 1"
                    ).fetchone()
                    if row and row[0] is not None:
                        candidate = str(row[0]).strip()
                        # RapidAPI keys are short opaque strings. Bound the value
                        # so a corrupt DB row cannot become an enormous header.
                        if candidate and len(candidate) <= 1024:
                            key = candidate
                            source = "flight-tracker-kv"
                finally:
                    con.close()
            except Exception:
                # Live refresh remains optional; the installed tracker cache is
                # still used as a fallback if its key row cannot be read.
                key = ""
                source = "none"

        previous_source = _aero_key_cache[2]
        _aero_key_cache = (now, key, source)
        if source != previous_source:
            log_event(logging.INFO if key else logging.WARNING, "aerodatabox.key", configured=bool(key), source=source)
        return key, source


def aerodatabox_configured() -> bool:
    key, _source = get_aerodatabox_key()
    return bool(key)


def aerodatabox_key_source() -> str:
    _key, source = get_aerodatabox_key()
    return source


def _json_url(url: str, headers: dict[str, str], timeout: float) -> tuple[int, object | None, str | None]:
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
    global _last_aero_request
    key, _source = get_aerodatabox_key()
    if not key:
        return 0, None, "AeroDataBox key unavailable"

    # RapidAPI's AeroDataBox Basic plan allows 1 request/second. Keep every
    # guest-portal request in one process-wide queue and deliberately leave a
    # safety margin because the separately installed Flight Tracker may use the
    # same key at the same time. A 429 is retried with increasing backoff.
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
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())[:8]


def _parse_meta(reservation: dict) -> dict:
    raw = reservation.get("metadata", reservation.get("meta", {}))
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _ordered_endpoints(reservation: dict) -> list[dict]:
    eps = reservation.get("endpoints")
    if not isinstance(eps, list):
        return []
    return sorted([e for e in eps if isinstance(e, dict)], key=lambda x: x.get("sequence") or 0)


def _tracker_override_number(reservation_id: str) -> str:
    db_path = find_tracker_db()
    if not db_path:
        return ""
    try:
        con = sqlite3.connect(f"file:{quote(db_path)}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute("SELECT flight_number FROM flights WHERE reservation_id = ? LIMIT 1", (reservation_id,)).fetchone()
            return _norm_flight_number(row[0]) if row and row[0] else ""
        finally:
            con.close()
    except Exception:
        return ""


def _reservation_legs(reservation: dict, baseline: dict | None, reservation_id: str, shared: dict) -> list[dict]:
    meta = _parse_meta(reservation)
    raw_legs = meta.get("legs") if isinstance(meta.get("legs"), list) else None
    legs: list[dict] = []
    if raw_legs:
        for item in raw_legs[:6]:
            if not isinstance(item, dict):
                continue
            legs.append({
                "from": item.get("from"), "to": item.get("to"),
                "airline": item.get("airline"), "airlineCode": item.get("airline_code"),
                "flight": item.get("flight_number") or item.get("flightNumber"),
                "depTime": item.get("dep_time"), "arrTime": item.get("arr_time"),
                "depDayId": item.get("dep_day_id"), "arrDayId": item.get("arr_day_id"),
                "seat": item.get("seat"), "localDepDate": item.get("local_date"),
            })
    else:
        eps = _ordered_endpoints(reservation)
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

    baseline_legs = baseline.get("legs") if isinstance(baseline, dict) and isinstance(baseline.get("legs"), list) else []
    override = _tracker_override_number(reservation_id)
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
        base_leg = baseline_legs[idx] if idx < len(baseline_legs) and isinstance(baseline_legs[idx], dict) else {}
        number = override if (override and idx == 0) else _norm_flight_number(base_leg.get("number")) or raw
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
    if not isinstance(block, dict):
        return None
    revised = block.get("revisedTime") or block.get("predictedTime") or block.get("runwayTime") or {}
    scheduled = block.get("scheduledTime") or {}
    def val(obj, key):
        return obj.get(key) if isinstance(obj, dict) else None
    return {
        "scheduled": val(scheduled, "local") or val(scheduled, "utc"),
        "revised": val(revised, "local") or val(revised, "utc"),
        "scheduledUtc": val(scheduled, "utc"), "revisedUtc": val(revised, "utc"),
    }


def _time_delay_minutes(times: dict | None) -> int | None:
    if not times or not times.get("revisedUtc") or not times.get("scheduledUtc"):
        return None
    try:
        a = _iso_ms(times["revisedUtc"]); b = _iso_ms(times["scheduledUtc"])
        return None if a is None or b is None else round((a - b) / 60000)
    except Exception:
        return None


def _airport_block(block: dict | None, times: dict | None) -> dict:
    block = block if isinstance(block, dict) else {}
    ap = block.get("airport") if isinstance(block.get("airport"), dict) else {}
    loc = ap.get("location") if isinstance(ap.get("location"), dict) else {}
    def fnum(v):
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
    dep = flight.get("departure") if isinstance(flight, dict) else None
    if not isinstance(dep, dict): return 0
    times = dep.get("scheduledTime") or dep.get("revisedTime") or {}
    value = times.get("utc") or times.get("local") if isinstance(times, dict) else None
    return _iso_ms(value) or 0


def _fetch_aero(number: str, dep_date: str | None) -> tuple[dict | None, str | None]:
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
    def fnum(v):
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
        status, data, error = _json_url(ADSB_HOST + path, {"Accept":"application/json", "User-Agent":f"TREK-Guest-Portal/{VERSION}"}, ADSB_TIMEOUT)
        if 200 <= status < 300 and isinstance(data, dict) and isinstance(data.get("ac"), list) and data["ac"]:
            if isinstance(data["ac"][0], dict):
                live = _normalise_live(data["ac"][0])
                log_event(logging.INFO, "adsb.response", lookup=path.split("/")[2] if len(path.split("/")) > 2 else "unknown", status=status, found=True, registration=live.get("reg"), callsign=live.get("callSign"))
                return live
        log_event(logging.DEBUG if 200 <= status < 300 else logging.WARNING, "adsb.response", lookup=path.split("/")[2] if len(path.split("/")) > 2 else "unknown", status=status, found=False, error=error)
    return None


def _iso_ms(value) -> int | None:
    if value is None or value == "": return None
    text = str(value).strip().replace(" ", "T")
    if not re.search(r"[zZ]$|[+-]\d\d:?\d\d$", text): text += "Z"
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return None


def _reservation_estimate(reservation: dict) -> tuple[int | None, int | None, str | None]:
    from datetime import datetime, timezone
    def parse(value):
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


def _flight_ttl_seconds(payload: dict) -> int:
    booking = payload.get("booking") if isinstance(payload, dict) and isinstance(payload.get("booking"), dict) else {}
    if booking.get("phase") == "past": return 0
    legs = payload.get("legs") if isinstance(payload, dict) and isinstance(payload.get("legs"), list) else []
    statuses = [str((l.get("status") or {}).get("status") or "") for l in legs if isinstance(l, dict)]
    if statuses and all(s == "Arrived" for s in statuses): return 0
    if any(s in {"EnRoute","Departed","Approaching","Boarding","Diverted"} for s in statuses): return 60
    dep = booking.get("depMs")
    try: dep = int(dep) if dep is not None else None
    except Exception: dep = None
    if not dep: return 300
    until = dep - int(time.time()*1000)
    if until < 3*3600*1000: return 60
    if until < 12*3600*1000: return 300
    if until < 48*3600*1000: return 1800
    return 7200


def _live_lock(key: tuple[str, str]) -> threading.Lock:
    with _live_refresh_locks_guard:
        lock = _live_refresh_locks.get(key)
        if lock is None:
            lock = threading.Lock(); _live_refresh_locks[key] = lock
        return lock


def _build_live_flight_payload(reservation: dict, reservation_id: str, trip_id: str, shared: dict, baseline: dict | None) -> dict:
    now_ms = int(time.time()*1000)
    dep_ms, arr_ms, base_date = _reservation_estimate(reservation)
    phase = "active"
    if dep_ms and now_ms < dep_ms - 48*3600*1000: phase = "upcoming"
    elif arr_ms and now_ms > arr_ms + 6*3600*1000: phase = "past"
    legs_in = _reservation_legs(reservation, baseline, reservation_id, shared)
    legs_out = []
    errors = []
    for idx, leg in enumerate(legs_in):
        number = leg.get("number")
        status = None; live = None; inbound = None
        if number:
            status, aero_err = _fetch_aero(number, leg.get("localDepDate") or base_date)
            if aero_err: errors.append(f"status: {aero_err}")
            airborne = bool(status and status.get("status") in {"EnRoute","Departed","Approaching"})
            close_window = not dep_ms or (now_ms >= dep_ms - 3600*1000 and (arr_ms is None or now_ms <= arr_ms + 2*3600*1000))
            if airborne or close_window:
                live = _fetch_adsb(status.get("aircraftReg") if status else None, status.get("callSign") if status else leg.get("callSign"), number)
            if status and status.get("aircraftReg") and not airborne and status.get("status") != "Arrived" and close_window and not live:
                candidate = _fetch_adsb(status.get("aircraftReg"), None, None)
                if candidate and candidate.get("lat") is not None and not candidate.get("onGround"):
                    inbound = candidate
        else:
            errors.append("flight number could not be detected")
        base_leg = leg.pop("_baseline", {})
        out = dict(leg)
        out.update({"status": status, "live": live, "inbound": inbound, "weather": base_leg.get("weather"), "errors": []})
        legs_out.append(out)
    # authoritative AeroDataBox UTC times replace rough reservation estimates
    if legs_out:
        first = legs_out[0].get("status") or {}
        dep_block = first.get("departure") if isinstance(first.get("departure"), dict) else {}
        first_utc = _iso_ms(dep_block.get("revisedUtc") or dep_block.get("scheduledUtc"))
        last_status = legs_out[-1].get("status") or {}
        arr_block = last_status.get("arrival") if isinstance(last_status.get("arrival"), dict) else {}
        last_utc = _iso_ms(arr_block.get("revisedUtc") or arr_block.get("scheduledUtc"))
        if first_utc is not None: dep_ms = first_utc
        if last_utc is not None: arr_ms = last_utc
        if dep_ms and now_ms < dep_ms - 48*3600*1000: phase = "upcoming"
        elif arr_ms and now_ms > arr_ms + 6*3600*1000: phase = "past"
        else: phase = "active"
    payload = {
        "applicable": True, "source": "guest-live", "legs": legs_out,
        "booking": {"type":"Flight", "depMs":dep_ms, "arrMs":arr_ms, "phase":phase,
                    "origin": legs_out[0].get("from") if legs_out else None,
                    "dest": legs_out[-1].get("to") if legs_out else None,
                    "legCount": len(legs_out), "estimatedTimes": not any((l.get("status") or {}).get("departure",{}).get("scheduledUtc") for l in legs_out)},
        "errors": list(dict.fromkeys(errors))[:4], "updatedAt": now_ms,
    }
    ttl = _flight_ttl_seconds(payload)
    payload["_guestCache"] = {"fetchedAt": now_ms, "ageSeconds": 0, "stale": False}
    payload["_guestLive"] = {"configured": True, "source": "AeroDataBox + adsb.fi", "ttlSeconds": ttl, "refreshAfterSeconds": ttl, "fetchedAt": now_ms}
    return payload


def _fresh_tracker_baseline(baseline: dict | None, trip_id: str, reservation_id: str) -> dict | None:
    """Reuse Flight Tracker's cache while it is still inside the same TTL curve.

    This avoids a duplicate AeroDataBox request when the installed tracker has
    already refreshed the flight recently.
    """
    if not isinstance(baseline, dict):
        return None
    cache = baseline.get("_guestCache") if isinstance(baseline.get("_guestCache"), dict) else {}
    try:
        fetched_ms = int(cache.get("fetchedAt") or 0)
    except Exception:
        fetched_ms = 0
    if not fetched_ms:
        return None
    now_ms = int(time.time() * 1000)
    age_ms = max(0, now_ms - fetched_ms)
    ttl = _flight_ttl_seconds(baseline)
    if ttl != 0 and age_ms >= ttl * 1000:
        return None

    out = json.loads(json.dumps(baseline))
    remaining = 0 if ttl == 0 else max(15, int((ttl * 1000 - age_ms + 999) // 1000))
    out.setdefault("_guestCache", {})["ageSeconds"] = age_ms // 1000
    out["_guestCache"]["fetchedAt"] = fetched_ms
    out["_guestCache"]["stale"] = False
    out["_guestLive"] = {
        "configured": True,
        "source": "Flight Tracker cache (fresh)",
        "ttlSeconds": ttl,
        "refreshAfterSeconds": remaining,
        "fetchedAt": fetched_ms,
    }
    log_event(logging.INFO, "flight.tracker_cache_reused", trip_id=trip_id, reservation_id=reservation_id, age_seconds=age_ms // 1000, refresh_after=remaining)
    return out


def get_live_flight_payload(reservation: dict, reservation_id: str, trip_id: str, shared: dict, baseline: dict | None) -> dict | None:
    key = (str(trip_id), str(reservation_id))
    now_ms = int(time.time()*1000)

    # Persistent Guest Portal cache is usable even if AeroDataBox is currently
    # unavailable or its key cannot be discovered after a restart.
    persisted = read_persistent_live_cache(str(trip_id), str(reservation_id))
    if persisted:
        fetched_ms, persisted_payload = persisted
        out = _decorate_cached_payload(fetched_ms, persisted_payload, str(trip_id), str(reservation_id), "flight.persistent_cache_hit")
        if out is not None:
            with _live_flight_cache_lock:
                _live_flight_cache[key] = (fetched_ms, persisted_payload)
            return out

    if not aerodatabox_configured():
        if baseline is None: return None
        payload = json.loads(json.dumps(baseline))
        ttl = _flight_ttl_seconds(payload)
        payload["_guestLive"] = {"configured": False, "source": "Flight Tracker cache", "ttlSeconds": ttl, "refreshAfterSeconds": min(ttl or 300, 300)}
        return payload
    with _live_flight_cache_lock:
        cached = _live_flight_cache.get(key)
        if cached:
            fetched_ms, payload = cached
            ttl = _flight_ttl_seconds(payload)
            age_ms = max(0, now_ms - fetched_ms)
            if ttl == 0 or age_ms < ttl*1000:
                out = json.loads(json.dumps(payload))
                remaining = 0 if ttl == 0 else max(15, int((ttl*1000 - age_ms + 999)//1000))
                out.setdefault("_guestCache", {})["ageSeconds"] = age_ms//1000
                out["_guestCache"]["fetchedAt"] = fetched_ms
                out["_guestLive"]["refreshAfterSeconds"] = remaining
                log_event(logging.INFO, "flight.live_cache_hit", trip_id=trip_id, reservation_id=reservation_id, age_seconds=age_ms//1000, refresh_after=remaining)
                return out

    tracker_fresh = _fresh_tracker_baseline(baseline, trip_id, reservation_id)
    if tracker_fresh is not None:
        return tracker_fresh

    lock = _live_lock(key)
    with lock:
        now_ms = int(time.time()*1000)
        with _live_flight_cache_lock:
            cached = _live_flight_cache.get(key)
            if cached:
                fetched_ms, payload = cached
                ttl = _flight_ttl_seconds(payload); age_ms = max(0, now_ms-fetched_ms)
                if ttl == 0 or age_ms < ttl*1000:
                    out = json.loads(json.dumps(payload)); remaining = 0 if ttl == 0 else max(15,int((ttl*1000-age_ms+999)//1000))
                    out.setdefault("_guestCache", {})["ageSeconds"] = age_ms//1000; out["_guestCache"]["fetchedAt"] = fetched_ms
                    out["_guestLive"]["refreshAfterSeconds"] = remaining
                    log_event(logging.INFO, "flight.live_cache_hit", trip_id=trip_id, reservation_id=reservation_id, age_seconds=age_ms//1000, refresh_after=remaining)
                    return out
        persisted = read_persistent_live_cache(str(trip_id), str(reservation_id))
        if persisted:
            fetched_ms, persisted_payload = persisted
            out = _decorate_cached_payload(fetched_ms, persisted_payload, str(trip_id), str(reservation_id), "flight.persistent_cache_hit")
            if out is not None:
                with _live_flight_cache_lock:
                    _live_flight_cache[key] = (fetched_ms, persisted_payload)
                return out
        tracker_fresh = _fresh_tracker_baseline(baseline, trip_id, reservation_id)
        if tracker_fresh is not None:
            return tracker_fresh
        try:
            log_event(logging.INFO, "flight.live_refresh_start", trip_id=trip_id, reservation_id=reservation_id, baseline=bool(baseline), key_source=aerodatabox_key_source())
            started = time.monotonic()
            payload = _build_live_flight_payload(reservation, reservation_id, str(trip_id), shared, baseline)
            log_event(logging.INFO, "flight.live_refresh_complete", trip_id=trip_id, reservation_id=reservation_id, legs=len(payload.get("legs") or []), ttl_seconds=_flight_ttl_seconds(payload), errors=len(payload.get("errors") or []), elapsed_ms=int((time.monotonic()-started)*1000))
        except Exception as exc:
            log_event(logging.ERROR, "flight.live_refresh_failed", trip_id=trip_id, reservation_id=reservation_id, error=str(exc))
            if baseline is not None:
                payload = json.loads(json.dumps(baseline))
                payload.setdefault("errors", []).append(f"guest live refresh failed: {exc}")
                payload["_guestLive"] = {"configured": True, "source":"Flight Tracker cache fallback", "ttlSeconds":300, "refreshAfterSeconds":300, "error":str(exc)}
                log_event(logging.WARNING, "flight.cache_fallback", trip_id=trip_id, reservation_id=reservation_id)
                return payload
            raise
        with _live_flight_cache_lock:
            if len(_live_flight_cache) >= LIVE_FLIGHT_MAX_CACHE:
                oldest = min(_live_flight_cache.items(), key=lambda kv: kv[1][0])[0]
                _live_flight_cache.pop(oldest, None)
            fetched_at = int(payload.get("updatedAt") or now_ms)
            _live_flight_cache[key] = (fetched_at, payload)
        write_persistent_live_cache(str(trip_id), str(reservation_id), fetched_at, payload)
        return json.loads(json.dumps(payload))

def reservation_kind(item: dict) -> str:
    for key in ("type", "reservation_type", "category"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().lower()
    return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "GuestPortal"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def version_string(self):
        return "GuestPortal"

    def log_message(self, fmt, *args):
        log_event(logging.DEBUG, "http.internal", client=self.client_address[0], message=fmt % args)

    def log_request(self, code="-", size="-"):
        path = urlsplit(self.path).path
        level = logging.INFO if path.startswith("/api/") else logging.DEBUG
        started = getattr(self, "_request_started", None)
        elapsed = int((time.monotonic() - started) * 1000) if started else None
        log_event(level, "http.request", client=self.client_address[0], method=self.command, target=_safe_request_target(self.path), status=code, bytes=size, elapsed_ms=elapsed)

    def _security_headers(self):
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
        body = json_bytes(obj)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _set_session_cookie(self, sid: str) -> str:
        return f"{SESSION_COOKIE_NAME}={sid}; Path={COOKIE_PATH}; Max-Age={SESSION_TTL_SECONDS}; HttpOnly; Secure; SameSite=Strict"

    def _clear_session_cookie(self) -> str:
        return f"{SESSION_COOKIE_NAME}=; Path={COOKIE_PATH}; Max-Age=0; HttpOnly; Secure; SameSite=Strict"

    def _read_json_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except Exception:
            return None
        if length <= 0 or length > MAX_SESSION_BODY:
            return None
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _require_session(self) -> tuple[str, dict] | None:
        sid, item = _session_from_cookie(self.headers.get("Cookie"))
        if not sid or not item:
            self._send_json(401, {"error": "Guest session required"})
            return None
        return sid, item

    def _serve_static(self, request_path: str):
        raw = unquote(request_path)
        rel = "index.html" if raw in ("", "/") else raw.lstrip("/")
        candidate = (PUBLIC_ROOT / rel).resolve()
        try:
            candidate.relative_to(PUBLIC_ROOT)
        except ValueError:
            return self._send_json(403, {"error": "Forbidden"})
        if not candidate.is_file():
            candidate = PUBLIC_ROOT / "index.html"
            if not candidate.is_file():
                return self._send_json(404, {"error": "Not found"})
        ctype, _ = mimetypes.guess_type(str(candidate))
        ctype = ctype or "application/octet-stream"
        size = candidate.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype in ("application/javascript", "application/json") else ""))
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store" if candidate.name in {"index.html", "config.js", "app.js", "style.css"} else "public, max-age=3600")
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
            conn.request("GET", target, headers=headers)
            res = conn.getresponse()
            self.send_response(res.status)
            allowed = {"content-type", "content-length", "content-range", "accept-ranges", "etag", "last-modified"}
            for key, value in res.getheaders():
                if key.lower() in allowed:
                    self.send_header(key, value)
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                while True:
                    chunk = res.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception as exc:
            log_event(logging.ERROR, "trek.media_proxy_failed", photo_id=photo_id, variant=variant, error=str(exc))
            try:
                self._send_json(502, {"error": "Media backend unavailable"})
            except Exception:
                pass
        finally:
            conn.close()

    def _create_guest_session(self):
        if not _origin_allowed(self.headers.get("Origin")):
            log_event(logging.WARNING, "session.origin_rejected", origin=self.headers.get("Origin") or "missing")
            return self._send_json(403, {"error": "Invalid request origin"})
        if not _session_rate_allowed():
            return self._send_json(429, {"error": "Too many session requests"}, {"Retry-After": "60"})
        body = self._read_json_body()
        if body is None:
            return self._send_json(400, {"error": "Invalid request"})
        trip_token = str(body.get("trip") or "").strip()
        journey_token = str(body.get("journey") or "").strip()
        title = str(body.get("title") or "").strip()[:160]
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
        sid, _item = _new_session(trip_token, journey_token, title, trip.get("id"))
        log_event(logging.INFO, "session.created", session=_token_ref(sid), trip_share=_token_ref(trip_token), journey=bool(journey_token), expires_in=SESSION_TTL_SECONDS)
        return self._send_json(200, {"ok": True, "hasJourney": bool(journey_token)}, {"Set-Cookie": self._set_session_cookie(sid)})

    def _logout(self):
        sid, _item = _session_from_cookie(self.headers.get("Cookie"))
        if sid:
            with _sessions_lock:
                _sessions.pop(sid, None)
            log_event(logging.INFO, "session.deleted", session=_token_ref(sid))
        return self._send_json(200, {"ok": True}, {"Set-Cookie": self._clear_session_cookie()})

    def _trip_json(self, session: dict):
        token = str(session.get("trip") or "")
        try:
            return self._send_json(200, get_shared_trip(token, _upstream_host_header()))
        except LookupError:
            return self._send_json(404, {"error": "Trip share link is invalid or expired"})
        except Exception as exc:
            log_event(logging.ERROR, "trip.read_failed", trip_share=_token_ref(token), error=str(exc))
            return self._send_json(502, {"error": "Trip data unavailable"})

    def _journey_json(self, session: dict):
        token = str(session.get("journey") or "")
        if not token:
            return self._send_json(404, {"error": "No Journey share is configured"})
        try:
            return self._send_json(200, get_shared_journey(token, _upstream_host_header()))
        except LookupError:
            return self._send_json(404, {"error": "Journey share link is invalid or expired"})
        except Exception as exc:
            log_event(logging.ERROR, "journey.read_failed", journey_share=_token_ref(token), error=str(exc))
            return self._send_json(502, {"error": "Journey data unavailable"})

    def _flight_status(self, reservation_id: str, session: dict):
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
        baseline = None
        try:
            baseline = read_tracker_cache(reservation_id, str(trip_id))
        except (FileNotFoundError, sqlite3.Error):
            baseline = None
        try:
            payload = get_live_flight_payload(reservation, reservation_id, str(trip_id), shared, baseline)
        except Exception as exc:
            log_event(logging.ERROR, "flight.live_refresh_failed", reservation_id=reservation_id, trip_id=trip_id, error=str(exc))
            return self._send_json(502, {"error": "Live flight data temporarily unavailable"})
        if payload is None:
            log_event(logging.WARNING, "flight.unavailable", reservation_id=reservation_id, trip_id=trip_id, baseline=bool(baseline), key_source=aerodatabox_key_source())
            return self._send_json(503, {"error": "Live flight data is not configured"})
        if isinstance(payload.get("_guestLive"), dict):
            payload["_guestLive"].pop("error", None)
        if isinstance(payload.get("errors"), list):
            payload["errors"] = ["Some live flight details could not be refreshed"] if payload["errors"] else []
        log_event(logging.INFO, "flight.response", reservation_id=reservation_id, trip_id=trip_id, source=(payload.get("_guestLive") or {}).get("source"), refresh_after=(payload.get("_guestLive") or {}).get("refreshAfterSeconds"))
        return self._send_json(200, payload)

    def _photo_dates(self, session: dict):
        token = str(session.get("journey") or "")
        if not token:
            return self._send_json(404, {"error": "No Journey share is configured"})
        log_event(logging.INFO, "photos.date_request", journey_share=_token_ref(token))
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

        def resolve(item: dict[str, str]) -> tuple[str, str | None, str]:
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
        log_event(logging.INFO, "photos.date_response", journey_share=_token_ref(token), checked=len(targets), resolved=len(dates), immich_checked=immich_checked, immich_resolved=immich_resolved, fallback_checked=fallback_checked)
        return self._send_json(200, {"dates": dates})

    def do_HEAD(self):
        self._dispatch_get()

    def do_GET(self):
        self._dispatch_get()

    def do_POST(self):
        self._request_started = time.monotonic()
        path = urlsplit(self.path).path
        if path == "/api/session":
            return self._create_guest_session()
        if path == "/api/logout":
            if not _origin_allowed(self.headers.get("Origin")):
                return self._send_json(403, {"error": "Invalid request origin"})
            return self._logout()
        return self._send_json(404, {"error": "Not found"})

    def do_OPTIONS(self):
        self._send_json(405, {"error": "Method not allowed"}, {"Allow": "GET, HEAD, POST"})

    def _dispatch_get(self):
        self._request_started = time.monotonic()
        path = urlsplit(self.path).path
        if path == "/health":
            return self._send_json(200, {"ok": True, "version": VERSION})
        if path.startswith("/api/"):
            auth = self._require_session()
            if auth is None:
                return
            _sid, session = auth
            if path == "/api/trip":
                return self._trip_json(session)
            if path == "/api/journey":
                return self._journey_json(session)
            if path == "/api/photo-dates":
                return self._photo_dates(session)
            if path.startswith("/api/flights/"):
                return self._flight_status(unquote(path[len("/api/flights/"):]), session)
            m = re.fullmatch(r"/api/photos/([A-Za-z0-9_-]{1,64})/(thumbnail|original)", path)
            if m:
                return self._proxy_journey_media(m.group(1), m.group(2), session)
            return self._send_json(404, {"error": "Not found"})
        return self._serve_static(path)


def main():
    if not PUBLIC_ROOT.is_dir():
        raise SystemExit(f"PUBLIC_ROOT does not exist: {PUBLIC_ROOT}")
    if not _public_origin_valid():
        raise SystemExit("PUBLIC_ORIGIN must be an https:// origin without a path, query, or fragment")
    if not COOKIE_PATH.startswith("/") or not COOKIE_PATH.endswith("/"):
        raise SystemExit("COOKIE_PATH must start and end with /")
    init_guest_cache_db()
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    db = find_tracker_db()
    log_event(logging.INFO, "startup", version=VERSION, listen=f"{LISTEN_HOST}:{LISTEN_PORT}", public_root=PUBLIC_ROOT, log_level=LOG_LEVEL, public_origin=PUBLIC_ORIGIN, cookie_path=COOKIE_PATH)
    log_event(logging.INFO, "integration.flight_tracker", found=bool(db), db=db or "not-found")
    log_event(logging.INFO, "integration.aerodatabox", configured=aerodatabox_configured(), key_source=aerodatabox_key_source())
    log_event(logging.INFO, "integration.immich", configured=immich_configured(), verify_tls=IMMICH_VERIFY_TLS)
    log_event(logging.INFO, "integration.persistent_cache", enabled=_guest_cache_ready, db=GUEST_CACHE_DB, error=_guest_cache_error or "none")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log_event(logging.INFO, "shutdown", reason="keyboard-interrupt")
    except Exception as exc:
        log_event(logging.CRITICAL, "server.crash", error=str(exc), exc_type=type(exc).__name__)
        raise
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
