'use strict';

const { definePlugin } = require('trek-plugin-sdk');
const crypto = require('node:crypto');

function response(status, body, contentType = 'application/json; charset=utf-8') {
  return {
    status,
    headers: {
      'content-type': contentType,
      'cache-control': 'no-store',
    },
    body,
  };
}

function json(status, value) {
  return response(status, JSON.stringify(value));
}

function parseBody(req) {
  if (!req || req.body == null) return {};
  if (typeof req.body === 'object') return req.body;
  if (typeof req.body === 'string') {
    try { return JSON.parse(req.body); } catch (_) { return {}; }
  }
  return {};
}

function scalar(value) {
  return Array.isArray(value) ? value[0] : value;
}

function cleanToken(value, type) {
  let raw = String(value || '').trim();
  if (!raw) return '';

  if (!raw.includes('/') && !raw.includes('?') && !raw.includes('#')) {
    return raw.replace(/[^A-Za-z0-9_-]/g, '');
  }

  let pathname = raw;
  try {
    pathname = new URL(raw, 'https://trek.invalid').pathname;
  } catch (_) {}

  const patterns = type === 'trip'
    ? [/\/shared\/([^/?#]+)/i, /\/api\/shared\/([^/?#]+)/i]
    : [
        /\/api\/public\/journey\/([^/?#]+)/i,
        /\/journey\/(?:shared|share|public)\/([^/?#]+)/i,
        /\/journeys?\/(?:shared|share|public)\/([^/?#]+)/i,
        /\/shared\/journeys?\/([^/?#]+)/i,
      ];

  for (const re of patterns) {
    const m = pathname.match(re);
    if (m && m[1]) return m[1].replace(/[^A-Za-z0-9_-]/g, '');
  }

  if (type === 'journey') {
    const parts = pathname.split('/').filter(Boolean);
    return (parts[parts.length - 1] || '').replace(/[^A-Za-z0-9_-]/g, '');
  }

  return '';
}

function cleanPortalBase(value) {
  const raw = String(value || '').trim();
  if (!raw) return '/guest-portal/';
  if (raw.startsWith('/')) {
    const path = '/' + raw.replace(/^\/+/, '').replace(/[?#].*$/, '');
    return path.endsWith('/') ? path : path + '/';
  }
  try {
    const u = new URL(raw);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
    u.hash = '';
    u.search = '';
    if (!u.pathname.endsWith('/')) u.pathname += '/';
    return u.toString();
  } catch (_) {
    return '';
  }
}

function legacyPortalToken() {
  return crypto.randomBytes(24).toString('base64url');
}

async function requireTripAccess(ctx, tripId) {
  if (!tripId) throw new Error('tripId is required');
  const trip = await ctx.trips.getById(String(tripId));
  if (!trip) throw new Error('Trip not found');
  return trip;
}

async function getPortal(ctx, tripId) {
  const rows = await ctx.db.query(
    `SELECT trip_id, portal_token, share_token, journey_token, title, enabled,
            portal_base, created_at, updated_at
       FROM portals WHERE trip_id = ?`,
    String(tripId),
  );
  return rows && rows[0] ? rows[0] : null;
}

function publicPortal(row) {
  if (!row) return null;
  return {
    shareToken: row.share_token,
    journeyToken: row.journey_token || '',
    title: row.title || '',
    portalBase: row.portal_base || '/guest-portal/',
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

module.exports = definePlugin({
  permissions: ['db:own', 'db:read:trips'],

  async onLoad(ctx) {
    await ctx.db.migrate('001_portals', `
      CREATE TABLE IF NOT EXISTS portals (
        trip_id TEXT PRIMARY KEY,
        portal_token TEXT NOT NULL UNIQUE,
        share_token TEXT NOT NULL,
        journey_token TEXT,
        title TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      )
    `);
    await ctx.db.migrate('002_portal_base', `
      ALTER TABLE portals ADD COLUMN portal_base TEXT
    `);
    ctx.log.info('Guest Portal v1.0.4 loaded');
  },

  routes: [
    {
      method: 'GET',
      path: '/config',
      auth: true,
      async handler(req, ctx) {
        const started = Date.now();
        const tripId = String(scalar(req.query && req.query.tripId) || '');
        ctx.log.info(`Guest Portal config read start trip=${tripId || 'missing'}`);
        try {
          const trip = await requireTripAccess(ctx, tripId);
          const row = await getPortal(ctx, tripId);
          ctx.log.info(`Guest Portal config read complete trip=${tripId} configured=${Boolean(row)} journey=${Boolean(row && row.journey_token)} elapsed_ms=${Date.now()-started}`);
          return json(200, {
            trip: {
              id: String(trip.id ?? tripId),
              title: trip.title || trip.name || '',
            },
            portal: publicPortal(row),
          });
        } catch (err) {
          ctx.log.warn(`Guest Portal config read failed trip=${tripId || 'missing'} error=${String(err && err.message ? err.message : 'unknown').slice(0,180)} elapsed_ms=${Date.now()-started}`);
          return json(403, { error: err && err.message ? err.message : 'Unable to read portal configuration' });
        }
      },
    },
    {
      method: 'POST',
      path: '/config',
      auth: true,
      async handler(req, ctx) {
        const started = Date.now();
        let tripId = '';
        try {
          const body = parseBody(req);
          tripId = String(body.tripId || '');
          ctx.log.info(`Guest Portal config write start trip=${tripId || 'missing'} has_trip_share=${Boolean(body.tripShare)} has_journey_share=${Boolean(body.journeyShare)} has_portal_base=${Boolean(body.portalBase)}`);
          const trip = await requireTripAccess(ctx, tripId);
          const shareToken = cleanToken(body.tripShare, 'trip');
          const journeyToken = cleanToken(body.journeyShare, 'journey');
          const portalBase = cleanPortalBase(body.portalBase);
          const title = String(body.title || '').trim().slice(0, 160);

          if (!shareToken) {
            return json(400, { error: 'A valid TREK Trip Share URL or token is required.' });
          }
          if (!portalBase) {
            return json(400, { error: 'Guest Portal URL must be an http(s) URL or an absolute path such as /guest-portal/.' });
          }

          const existing = await getPortal(ctx, tripId);
          if (existing) {
            await ctx.db.exec(
              `UPDATE portals
                  SET share_token = ?, journey_token = ?, title = ?, portal_base = ?,
                      enabled = 1, updated_at = CURRENT_TIMESTAMP
                WHERE trip_id = ?`,
              shareToken,
              journeyToken || null,
              title || trip.title || trip.name || '',
              portalBase,
              tripId,
            );
          } else {
            await ctx.db.exec(
              `INSERT INTO portals
                 (trip_id, portal_token, share_token, journey_token, title, enabled, portal_base)
               VALUES (?, ?, ?, ?, ?, 1, ?)`,
              tripId,
              legacyPortalToken(),
              shareToken,
              journeyToken || null,
              title || trip.title || trip.name || '',
              portalBase,
            );
          }

          const saved = await getPortal(ctx, tripId);
          ctx.log.info(`Guest Portal config write complete trip=${tripId} journey=${Boolean(saved && saved.journey_token)} portal_base=${saved && saved.portal_base ? saved.portal_base : '/guest-portal/'} elapsed_ms=${Date.now()-started}`);
          return json(200, { ok: true, portal: publicPortal(saved) });
        } catch (err) {
          ctx.log.warn(`Guest Portal config write failed trip=${tripId || 'missing'} error=${String(err && err.message ? err.message : 'unknown').slice(0,180)} elapsed_ms=${Date.now()-started}`);
          return json(403, { error: err && err.message ? err.message : 'Unable to save portal configuration' });
        }
      },
    },
  ],
});
