'use strict';

(() => {
  const app = document.getElementById('app');
  const fragment = new URLSearchParams(location.hash.replace(/^#/, ''));
  const shareToken = fragment.get('trip') || '';
  const journeyToken = fragment.get('journey') || '';
  const portalTitle = fragment.get('title') || '';

  let meta = { title: portalTitle };
  let tripData = null;
  let journeyData = null;
  let activeTab = 'plan';
  let selectedDay = 'all';
  let searchText = '';
  const portalConfig = window.GUEST_PORTAL_CONFIG || {};
  let map = null;
  let mapReady = false;
  let markerByPlaceId = new Map();
  let selectedPlaceId = null;
  let routeAbortController = null;
  let routeRequestSerial = 0;
  let mapTooltip = null;
  let currentMapPlaces = [];
  let currentDayOrderMap = {};
  let currentRouteCoords = [];
  let markerReconcileRaf = null;
  let gallery = [];
  let photoCaptureDates = {};
  let lightboxIndex = 0;
  const flightRefreshTimers = new Map();
  const flightNextRefreshAt = new Map();
  let flightCountdownTimer = null;

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function attr(value) { return esc(value); }

  function safeUrl(value) {
    const s = String(value || '').trim();
    if (!s) return '';
    if (s.startsWith('/')) return s;
    try {
      const u = new URL(s, location.origin);
      if (u.protocol === 'http:' || u.protocol === 'https:') return u.href;
    } catch (_) {}
    return '';
  }

  function text(...values) {
    for (const v of values) if (v !== undefined && v !== null && String(v).trim() !== '') return String(v);
    return '';
  }

  function asArray(value) {
    if (Array.isArray(value)) return value;
    if (value && Array.isArray(value.items)) return value.items;
    if (value && Array.isArray(value.entries)) return value.entries;
    if (value && typeof value === 'object') return Object.values(value).filter(v => v && typeof v === 'object');
    return [];
  }

  function num(...values) {
    for (const v of values) {
      if (v === null || v === undefined || (typeof v === 'string' && v.trim() === '')) continue;
      const n = Number(v);
      if (Number.isFinite(n)) return n;
    }
    return null;
  }

  function dateObject(value) {
    if (value === null || value === undefined || value === '') return null;
    if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
    if (typeof value === 'number' && Number.isFinite(value)) {
      const ms = Math.abs(value) < 1e12 ? value * 1000 : value;
      const d = new Date(ms);
      return Number.isNaN(d.getTime()) ? null : d;
    }
    const raw = String(value).trim();
    if (!raw) return null;
    if (/^\d{10,16}$/.test(raw)) {
      const n = Number(raw);
      if (Number.isFinite(n)) {
        const d = new Date(raw.length <= 10 ? n * 1000 : n);
        if (!Number.isNaN(d.getTime())) return d;
      }
    }
    const dayOnly = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const d = new Date(dayOnly ? `${raw}T12:00:00` : raw);
    return Number.isNaN(d.getTime()) ? null : d;
  }

  function formatDate(value, options) {
    if (!value) return '';
    try {
      const d = dateObject(value);
      if (!d) return String(value);
      return new Intl.DateTimeFormat(undefined, options || { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }).format(d);
    } catch (_) { return String(value); }
  }

  function dateKey(value) {
    const raw = String(value ?? '').trim();
    if (!raw) return '';
    const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (m) return `${m[1]}-${m[2]}-${m[3]}`;
    const d = dateObject(value);
    if (!d) return '';
    const y = d.getFullYear();
    const mo = String(d.getMonth() + 1).padStart(2, '0');
    const da = String(d.getDate()).padStart(2, '0');
    return `${y}-${mo}-${da}`;
  }

  function dateSortValue(value) {
    const key = dateKey(value);
    if (!key) return Number.MAX_SAFE_INTEGER;
    const n = new Date(`${key}T12:00:00`).getTime();
    return Number.isFinite(n) ? n : Number.MAX_SAFE_INTEGER;
  }

  function buildChronologicalGallery(data, captureDates = {}) {
    const entryPhotoMeta = new Map();
    asArray(data?.entries).forEach((entry, entryIndex) => {
      asArray(entry?.photos).forEach((photo, photoIndex) => {
        const photoId = photo?.photo_id ?? photo?.id;
        if (photoId == null) return;
        const key = String(photoId);
        const candidate = {
          date: entry?.entry_date || photo?.created_at || '',
          entrySort: num(entry?.sort_order, entryIndex) ?? entryIndex,
          photoSort: num(photo?.sort_order, photoIndex) ?? photoIndex,
        };
        const existing = entryPhotoMeta.get(key);
        if (!existing || dateSortValue(candidate.date) < dateSortValue(existing.date)) entryPhotoMeta.set(key, candidate);
      });
    });

    return asArray(data?.gallery).map((photo, galleryIndex) => {
      const photoId = photo?.photo_id ?? photo?.id;
      const linked = photoId == null ? null : entryPhotoMeta.get(String(photoId));
      const captureDate = photoId == null ? '' : (captureDates[String(photoId)] || '');
      const displayDate = captureDate || linked?.date || photo?.created_at || '';
      return {
        ...photo,
        _displayDate: displayDate,
        _captureDate: captureDate,
        _dateKey: dateKey(displayDate),
        _entrySort: linked?.entrySort ?? Number.MAX_SAFE_INTEGER,
        _entryPhotoSort: linked?.photoSort ?? Number.MAX_SAFE_INTEGER,
        _gallerySort: num(photo?.sort_order, galleryIndex) ?? galleryIndex,
      };
    }).sort((a, b) => {
      const dateCmp = dateSortValue(a._displayDate) - dateSortValue(b._displayDate);
      if (dateCmp) return dateCmp;
      if (a._entrySort !== b._entrySort) return a._entrySort - b._entrySort;
      if (a._entryPhotoSort !== b._entryPhotoSort) return a._entryPhotoSort - b._entryPhotoSort;
      if (a._gallerySort !== b._gallerySort) return a._gallerySort - b._gallerySort;
      const createdCmp = String(a.created_at || '').localeCompare(String(b.created_at || ''));
      if (createdCmp) return createdCmp;
      return Number(a.id || 0) - Number(b.id || 0);
    });
  }

  function formatTime(value) {
    if (!value) return '';
    const s = String(value);
    const m = s.match(/(?:T|^)(\d{1,2}):(\d{2})/);
    if (!m) return s;
    const d = new Date();
    d.setHours(Number(m[1]), Number(m[2]), 0, 0);
    return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(d);
  }

  function money(value, currency) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '';
    try { return new Intl.NumberFormat(undefined, { style: 'currency', currency: currency || tripData?.baseCurrency || tripData?.trip?.currency || 'USD' }).format(n); }
    catch (_) { return `${n.toFixed(2)} ${currency || ''}`.trim(); }
  }

  async function fetchJson(url) {
    const res = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.json()).error || ''; } catch (_) {}
      throw new Error(detail || `Request failed (${res.status})`);
    }
    return res.json();
  }


async function establishGuestSession() {
  if (!shareToken) return;
  const res = await fetch('api/session', {
    method: 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trip: shareToken, journey: journeyToken, title: portalTitle }),
  });
  if (!res.ok) {
    let detail = '';
    try { detail = (await res.json()).error || ''; } catch (_) {}
    throw new Error(detail || `Session request failed (${res.status})`);
  }
  // The native TREK/Journey bearer tokens are needed only once. After the
  // companion has issued its HttpOnly guest-session cookie, remove them from
  // the visible URL and browser history entry. Refreshes continue using the
  // session cookie; the original owner-generated share URL remains the link to distribute.
  try { history.replaceState(null, document.title, `${location.pathname}${location.search}`); } catch (_) {}
}

async function load() {
  try {
    if (shareToken) await establishGuestSession();
    const tripPromise = fetchJson('api/trip');
    const journeyPromise = fetchJson('api/journey').catch(() => null);
    const data = await Promise.all([tripPromise, journeyPromise]);
    tripData = data[0];
    journeyData = data[1] || null;
    gallery = journeyData?.permissions?.share_gallery ? buildChronologicalGallery(journeyData, photoCaptureDates) : [];
    renderShell();
    selectTab('plan');
    enrichPhotoCaptureDates();
  } catch (err) {
    const missing = !shareToken ? ' Open the original Guest Portal share link again.' : '';
    fatal(`This guest portal could not be loaded. ${err.message || ''}${missing}`);
  }
}

function fatal(message) {
    app.innerHTML = `<div class="fatal"><h2>Guest Portal unavailable</h2><div>${esc(message)}</div><small>The trip owner may need to refresh the underlying TREK share link in Guest Portal settings.</small></div>`;
  }

  async function enrichPhotoCaptureDates() {
    if (!journeyData?.permissions?.share_gallery || !asArray(journeyData?.gallery).length) return;
    try {
      const result = await fetchJson('api/photo-dates');
      if (!result || typeof result.dates !== 'object' || !result.dates) return;
      photoCaptureDates = result.dates;
      gallery = buildChronologicalGallery(journeyData, photoCaptureDates);
      if (activeTab === 'photos') {
        const content = document.getElementById('content');
        if (content) renderPhotos(content);
      }
    } catch (_) {
      // Embedded capture-date enrichment is best-effort. Entry/import dates remain usable.
    }
  }

  function tripTitle() {
    return text(meta?.title, tripData?.trip?.title, journeyData?.journey?.title, 'Shared trip');
  }

  const TRANSPORT_TYPES = new Set(['flight','train','bus','car','taxi','bicycle','cruise','ferry']);

  function reservationType(r) {
    return String(text(r?.type, r?.reservation_type, r?.category, '')).trim().toLowerCase();
  }

  function isTransportReservation(r) {
    return TRANSPORT_TYPES.has(reservationType(r));
  }

  function transportReservations() {
    return asArray(tripData?.reservations).filter(isTransportReservation);
  }

  function nonTransportReservations() {
    const accommodations = asArray(tripData?.accommodations);
    const linkedReservationIds = new Set(accommodations.map(a => a?.reservation_id).filter(v => v != null).map(String));
    return asArray(tripData?.reservations).filter(r => {
      if (isTransportReservation(r)) return false;
      // TREK accommodations auto-create a partner Hotel reservation. Suppress only
      // when the public payload gives us an explicit link, so the lodging card is
      // not duplicated while unrelated Hotel reservations remain visible.
      if (reservationType(r) === 'hotel' && linkedReservationIds.has(String(r.id))) return false;
      return true;
    });
  }

  function tabDefs() {
    const p = tripData?.permissions || {};
    const transports = transportReservations();
    const reservations = nonTransportReservations();
    const accommodations = asArray(tripData?.accommodations);
    const defs = [{ id: 'plan', label: 'Plan', show: true }];
    defs.push({ id: 'flights', label: 'Flights', show: true, count: transports.length });
    defs.push({ id: 'reservations', label: 'Reservations', show: true, count: reservations.length + accommodations.length });
    defs.push({ id: 'photos', label: 'Photos', show: !!journeyData?.permissions?.share_gallery, count: gallery.length });
    return defs.filter(x => x.show);
  }

  function renderShell() {
    const trip = tripData?.trip || {};
    const start = formatDate(trip.start_date, { month:'short', day:'numeric', year:'numeric' });
    const end = formatDate(trip.end_date, { month:'short', day:'numeric', year:'numeric' });
    const dateRange = start && end ? `${start} – ${end}` : (start || end);
    const daysCount = tripData?.days?.length || 0;
    const placesCount = Object.values(tripData?.assignments || {}).reduce((n, a) => n + (Array.isArray(a) ? a.length : 0), 0);
    const cover = safeUrl(trip.cover_image);

    app.innerHTML = `
      <header class="hero${cover ? ' has-cover' : ''}" id="hero">
        <div class="hero-inner">
          <div class="eyebrow">Shared trip</div>
          <h1>${esc(tripTitle())}</h1>
          ${trip.description ? `<div class="hero-description">${esc(trip.description)}</div>` : ''}
          <div class="hero-meta">
            ${dateRange ? `<span class="hero-pill">${esc(dateRange)}</span>` : ''}
            ${daysCount ? `<span class="hero-pill">${daysCount} day${daysCount === 1 ? '' : 's'}</span>` : ''}
            ${placesCount ? `<span class="hero-pill">${placesCount} planned stop${placesCount === 1 ? '' : 's'}</span>` : ''}
            ${gallery.length ? `<span class="hero-pill">${gallery.length} photo${gallery.length === 1 ? '' : 's'}</span>` : ''}
          </div>
        </div>
      </header>
      <div class="nav-wrap"><nav class="nav" id="nav"></nav></div>
      <main class="page" id="content"></main>
      <footer class="footer">Read-only guest view • Shared by the trip owner</footer>
      <div class="lightbox" id="lightbox" hidden aria-modal="true" role="dialog">
        <div class="lb-top"><div id="lbCounter"></div><button class="lb-close" id="lbClose" aria-label="Close">×</button></div>
        <div class="lb-stage"><button class="lb-nav" id="lbPrev" aria-label="Previous">‹</button><div class="lb-media"><img id="lbImage" alt=""><video id="lbVideo" controls playsinline hidden></video></div><button class="lb-nav" id="lbNext" aria-label="Next">›</button></div>
        <div class="lb-caption" id="lbCaption"></div>
      </div>`;

    if (cover) {
      const hero = document.getElementById('hero');
      hero.style.backgroundImage = `linear-gradient(110deg, rgba(3,7,18,.84), rgba(15,23,42,.48)), url("${cover.replace(/"/g, '%22')}")`;
      hero.style.backgroundSize = 'cover';
      hero.style.backgroundPosition = 'center';
    }

    const nav = document.getElementById('nav');
    nav.innerHTML = tabDefs().map(t => `<button data-tab="${t.id}">${esc(t.label)}${t.count ? `<span class="count">${t.count}</span>` : ''}</button>`).join('');
    nav.addEventListener('click', e => {
      const b = e.target.closest('button[data-tab]');
      if (b) selectTab(b.dataset.tab);
    });

    document.getElementById('lbClose').addEventListener('click', closeLightbox);
    document.getElementById('lbPrev').addEventListener('click', () => moveLightbox(-1));
    document.getElementById('lbNext').addEventListener('click', () => moveLightbox(1));
    document.getElementById('lightbox').addEventListener('click', e => { if (e.target.id === 'lightbox') closeLightbox(); });
    let touchStartX = null;
    document.getElementById('lightbox').addEventListener('touchstart', e => { touchStartX = e.touches?.[0]?.clientX ?? null; }, {passive:true});
    document.getElementById('lightbox').addEventListener('touchend', e => {
      if (touchStartX == null) return;
      const endX = e.changedTouches?.[0]?.clientX;
      if (Number.isFinite(endX) && Math.abs(endX - touchStartX) > 55) moveLightbox(endX < touchStartX ? 1 : -1);
      touchStartX = null;
    }, {passive:true});
    document.addEventListener('keydown', e => {
      if (document.getElementById('lightbox')?.hidden === false) {
        if (e.key === 'Escape') closeLightbox();
        if (e.key === 'ArrowLeft') moveLightbox(-1);
        if (e.key === 'ArrowRight') moveLightbox(1);
      }
    });
  }

  function selectTab(id) {
    clearFlightRefreshTimers();
    activeTab = id;
    document.querySelectorAll('#nav button').forEach(b => b.classList.toggle('active', b.dataset.tab === id));
    destroyMap();
    const content = document.getElementById('content');
    if (!content) return;
    if (id === 'plan') renderPlan(content);
    else if (id === 'flights') renderFlights(content);
    else if (id === 'reservations') renderReservations(content);
    else if (id === 'photos') renderPhotos(content);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function dayLabel(day) {
    return text(day.title, day.name, `Day ${day.day_number || ''}`.trim());
  }

  function assignmentListForDay(dayId) {
    return tripData?.assignments?.[dayId] || tripData?.assignments?.[String(dayId)] || [];
  }

  function filteredAssignments(dayId) {
    const q = searchText.toLowerCase().trim();
    const source = assignmentListForDay(dayId);
    if (!q) return source;
    return source.filter(a => {
      const p = a.place || {};
      return [p.name,p.address,p.description,p.category?.name,a.notes].some(v => String(v || '').toLowerCase().includes(q));
    });
  }

  function renderPlan(content) {
    const shared = !!tripData?.permissions?.share_map;
    content.innerHTML = `
      <h2 class="section-title">Trip plan</h2>
      <p class="section-lead">Explore the itinerary and map together. Select a day or a stop to focus the map.</p>
      ${shared ? `
        <div class="plan-toolbar">
          <div class="day-chips" id="dayChips"></div>
          <input class="search" id="placeSearch" type="search" placeholder="Search itinerary…" aria-label="Search itinerary">
        </div>
        <div class="plan-layout">
          <div class="itinerary" id="itinerary"></div>
          <div id="mapParking" class="map-parking" hidden>
            <div class="map-shell card" id="planMapShell" hidden>
              <div id="map"></div>
              <div class="map-tools">
                <button id="fitMap" type="button">Fit map</button>
              </div>
              <div class="map-route-status" id="mapRouteStatus" hidden></div>
              <div class="map-hover-card" id="mapHoverCard" hidden></div>
            </div>
          </div>
        </div>` : `<div class="card empty">The trip owner has not enabled the Plan/Map section for this public share link.</div>`}`;

    if (!shared) return;
    const days = tripData.days || [];
    const chips = document.getElementById('dayChips');
    chips.innerHTML = `<button class="chip ${selectedDay === 'all' ? 'active' : ''}" data-day="all">All days</button>` + days.map(d => `<button class="chip ${String(selectedDay) === String(d.id) ? 'active' : ''}" data-day="${attr(d.id)}">${esc(dayLabel(d))}${d.date ? ` · ${esc(formatDate(d.date,{month:'short',day:'numeric'}))}` : ''}</button>`).join('');
    chips.addEventListener('click', e => {
      const b = e.target.closest('[data-day]');
      if (!b) return;
      selectedDay = b.dataset.day;
      chips.querySelectorAll('.chip').forEach(c => c.classList.toggle('active', c.dataset.day === selectedDay));
      renderItinerary();
      refreshMapData(true);
    });

    document.getElementById('placeSearch').value = searchText;
    document.getElementById('placeSearch').addEventListener('input', e => {
      searchText = e.target.value;
      renderItinerary();
      refreshMapData(true);
    });

    renderItinerary();
    // The map is initialized lazily when a guest selects a stop. This keeps
    // it directly beneath the selected itinerary option instead of occupying
    // a permanent right-hand column.
  }

  function visibleDays() {
    const days = tripData?.days || [];
    return selectedDay === 'all' ? days : days.filter(d => String(d.id) === String(selectedDay));
  }

  function parkInlineMap() {
    const shell = document.getElementById('planMapShell');
    const parking = document.getElementById('mapParking');
    if (!shell || !parking) return;
    if (shell.parentElement !== parking) parking.appendChild(shell);
    shell.hidden = true;
  }

  function placeMapUnderSelectedCard(id, { initialize = true } = {}) {
    const shell = document.getElementById('planMapShell');
    if (!shell) return false;
    const card = [...document.querySelectorAll('.place-card[data-place]')]
      .find(c => String(c.dataset.place) === String(id));
    if (!card) {
      parkInlineMap();
      return false;
    }

    card.insertAdjacentElement('afterend', shell);
    shell.hidden = false;

    requestAnimationFrame(() => {
      if (!map && initialize) {
        initMap();
        return;
      }
      if (map) {
        try { map.resize(); } catch (_) {}
      }
    });
    return true;
  }

  function renderItinerary() {
    const holder = document.getElementById('itinerary');
    if (!holder) return;

    // A selected map lives inside the itinerary list. Park it before replacing
    // the itinerary HTML so Mapbox's canvas is not destroyed by innerHTML.
    parkInlineMap();

    const days = visibleDays();
    if (!days.length) { holder.innerHTML = `<div class="card empty">No itinerary days are shared.</div>`; return; }

    holder.innerHTML = days.map(day => {
      const assignments = filteredAssignments(day.id);
      const rawNotes = tripData?.dayNotes?.[day.id] || tripData?.dayNotes?.[String(day.id)] || [];
      const notes = Array.isArray(rawNotes) ? rawNotes : (rawNotes ? [rawNotes] : []);
      const dayText = text(day.notes, day.description);
      const noteText = notes.map(n => text(n.text,n.content,n.notes,n.note)).filter(Boolean).join('\n');
      return `<section class="card day-block">
        <div class="day-head">
          <div class="day-title-row"><div><h3>${esc(dayLabel(day))}</h3><div class="day-date">${esc(formatDate(day.date))}</div></div><span class="badge">${assignments.length} stop${assignments.length===1?'':'s'}</span></div>
          ${(dayText || noteText) ? `<div class="day-note">${esc([dayText,noteText].filter(Boolean).join('\n'))}</div>` : ''}
        </div>
        <div class="place-list">
          ${assignments.length ? assignments.map((a,i) => placeCard(a,i)).join('') : `<div class="empty" style="padding:20px">${searchText ? 'No stops match your search.' : 'No planned stops.'}</div>`}
        </div>
      </section>`;
    }).join('');

    holder.querySelectorAll('.place-card[data-place]').forEach(card => {
      card.addEventListener('click', () => focusPlace(card.dataset.place));
    });

    if (selectedPlaceId != null) {
      const restored = placeMapUnderSelectedCard(selectedPlaceId, { initialize:false });
      if (restored && map) {
        requestAnimationFrame(() => { try { map.resize(); } catch (_) {} });
      }
    }
  }

  function placeCard(a, index) {
    const p = a.place || {};
    const time = [formatTime(p.place_time), formatTime(p.end_time)].filter(Boolean).join(' – ');
    const category = text(p.category?.name, p.category_name);
    const metaParts = [category, p.address].filter(Boolean);
    const tags = Array.isArray(p.tags) ? p.tags : [];
    return `<article class="place-card" data-place="${attr(p.id)}">
      <div class="place-number">${index + 1}</div>
      <div>
        <div class="place-top"><div class="place-name">${esc(text(p.name,'Unnamed stop'))}</div>${time ? `<div class="place-time">${esc(time)}</div>` : ''}</div>
        ${metaParts.length ? `<div class="place-meta">${metaParts.map(esc).join(' • ')}</div>` : ''}
        ${p.description ? `<div class="place-desc">${esc(p.description)}</div>` : ''}
        ${a.notes ? `<div class="place-desc"><strong>Plan note:</strong> ${esc(a.notes)}</div>` : ''}
        ${tags.length ? `<div class="place-tags">${tags.slice(0,8).map(t => `<span class="tag">${esc(text(t.name,t))}</span>`).join('')}</div>` : ''}
      </div>
    </article>`;
  }

  const PLACE_CLUSTER_SOURCE_ID = 'trip-place-clusters';
  const PLACE_CLUSTER_CIRCLE_LAYER_ID = 'trip-place-clusters-circle';
  const PLACE_CLUSTER_COUNT_LAYER_ID = 'trip-place-clusters-count';
  const PLACE_UNCLUSTERED_LAYER_ID = 'trip-place-unclustered-hit';

  function mapboxToken() {
    return String(portalConfig.mapboxAccessToken || '').trim();
  }

  function mapboxStyle() {
    return String(portalConfig.mapboxStyle || 'mapbox://styles/mapbox/standard').trim() || 'mapbox://styles/mapbox/standard';
  }

  function mapbox3dEnabled() {
    return portalConfig.mapbox3d !== false;
  }

  function mapboxHighQuality() {
    return portalConfig.mapboxHighQuality === true;
  }

  function assignmentMatchesSearch(a) {
    const q = searchText.toLowerCase().trim();
    if (!q) return true;
    const p = a?.place || {};
    return [p.name, p.address, p.description, p.category?.name, p.category_name, a?.notes]
      .some(v => String(v || '').toLowerCase().includes(q));
  }

  function normalizedMapPlace(a) {
    const p = a?.place || {};
    return {
      ...p,
      category_color: text(p.category?.color, p.category_color, '#6b7280'),
      category_icon: text(p.category?.icon, p.category_icon, 'MapPin'),
      category_name: text(p.category?.name, p.category_name, ''),
    };
  }

  function buildMapPlaceState() {
    const byId = new Map();
    const orderMap = {};
    visibleDays().forEach(day => {
      assignmentListForDay(day.id).forEach((a, originalIndex) => {
        if (!assignmentMatchesSearch(a)) return;
        const p = normalizedMapPlace(a);
        const lat = num(p.lat ?? p.latitude);
        const lng = num(p.lng ?? p.longitude ?? p.lon);
        if (p.id == null || lat == null || lng == null) return;
        p.lat = lat;
        p.lng = lng;
        const key = String(p.id);
        if (!byId.has(key)) byId.set(key, p);
        (orderMap[key] ||= []).push(originalIndex + 1);
      });
    });
    return { places: [...byId.values()], orderMap };
  }

  function publicAssetUrl(value) {
    const url = safeUrl(value);
    if (!url) return '';
    if (/\/api\/(?:shared|public\/journey)\//.test(url)) return '';
    return url;
  }

  function categoryIconSvg(iconName, size) {
    const n = String(iconName || '').toLowerCase();
    const common = `fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"`;
    let body = '<circle cx="12" cy="10" r="3"></circle><path d="M12 21s6-5.2 6-11a6 6 0 1 0-12 0c0 5.8 6 11 6 11z"></path>';
    if (/utens|food|restaurant|fork|knife/.test(n)) body = '<path d="M7 3v8M4.5 3v5M9.5 3v5M7 11v10M16 3v18M16 3c3 2.3 3 7 0 9"></path>';
    else if (/bed|hotel|lodg/.test(n)) body = '<path d="M3 18v-7M21 18v-5a3 3 0 0 0-3-3H8a3 3 0 0 0-3 3v5M3 15h18M7 10V7h5a3 3 0 0 1 3 3"></path>';
    else if (/plane|flight/.test(n)) body = '<path d="M22 2 9 15M22 2l-6 20-4-9-9-4 19-7z"></path>';
    else if (/train|tram|rail/.test(n)) body = '<rect x="5" y="3" width="14" height="15" rx="3"></rect><path d="M8 7h8M8 13h.01M16 13h.01M8 21l2-3M16 18l2 3"></path>';
    else if (/car|taxi|drive/.test(n)) body = '<path d="M5 17h14l-1.5-6h-11L5 17zM7 11l1.5-4h7L17 11M7 17v2M17 17v2M8 14h.01M16 14h.01"></path>';
    else if (/camera|photo/.test(n)) body = '<rect x="3" y="6" width="18" height="13" rx="2"></rect><circle cx="12" cy="12.5" r="4"></circle><path d="M8 6l1.5-2h5L16 6"></path>';
    else if (/coffee|cafe/.test(n)) body = '<path d="M5 8h11v6a5 5 0 0 1-5 5H10a5 5 0 0 1-5-5V8zM16 10h2a3 3 0 0 1 0 6h-2M8 4v2M12 3v3"></path>';
    else if (/shop|bag|store/.test(n)) body = '<path d="M5 8h14l-1 13H6L5 8zM9 9V6a3 3 0 0 1 6 0v3"></path>';
    else if (/museum|landmark|building|temple|church/.test(n)) body = '<path d="M3 9h18L12 3 3 9zM5 10v8M9 10v8M15 10v8M19 10v8M3 21h18"></path>';
    else if (/mountain|hike|trail/.test(n)) body = '<path d="m3 20 7-12 4 7 2-3 5 8H3z"></path>';
    else if (/beach|sun|palmtree|park/.test(n)) body = '<circle cx="17" cy="7" r="3"></circle><path d="M3 20c3-4 6-5 9-3s6 2 9-1M8 16l3-8 3 5"></path>';
    else if (/ship|boat|cruise/.test(n)) body = '<path d="M4 15h16l-2 5H6l-2-5zM8 15V7h8v8M10 7V4h4v3M3 21c2 1 4 1 6 0 2 1 4 1 6 0 2 1 4 1 6 0"></path>';
    else if (/walk|foot/.test(n)) body = '<circle cx="13" cy="4" r="2"></circle><path d="m10 21 2-7-3-3 2-4 4 3 3 1M12 14l4 3 1 4M9 11l-4 3"></path>';
    else if (/bike|cycle/.test(n)) body = '<circle cx="6" cy="17" r="4"></circle><circle cx="18" cy="17" r="4"></circle><path d="m6 17 4-8 4 8h4M10 9h4M12 5h3"></path>';
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" ${common} aria-hidden="true">${body}</svg>`;
  }

  function createMapboxMarkerElement(place, orderNumbers, selected) {
    const size = selected ? 44 : 36;
    const borderColor = selected ? '#111827' : (place.category_color || 'white');
    const borderWidth = selected ? 3 : 2.5;
    const shadow = selected
      ? '0 0 0 3px rgba(17,24,39,0.25), 0 4px 14px rgba(0,0,0,0.3)'
      : '0 2px 8px rgba(0,0,0,0.22)';
    const bgColor = place.category_color || '#6b7280';
    const outer = size + borderWidth * 2;
    const wrap = document.createElement('div');
    wrap.className = 'trek-mapbox-marker';
    wrap.style.cssText = `width:${outer}px;height:${outer}px;cursor:pointer;`;

    let badgeHtml = '';
    if (orderNumbers?.length) {
      const label = orderNumbers.join(' · ');
      const multi = orderNumbers.length > 1;
      badgeHtml = `<span class="trek-marker-order" style="height:${multi ? 16 : 18}px;min-width:18px;border-radius:${multi ? 8 : 9}px;padding:0 ${multi ? 4 : 3}px;font-size:${multi ? 7.5 : 9}px">${esc(label)}</span>`;
    }

    const photoUrl = publicAssetUrl(place.image_url);
    const innerStyle = `position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:${size}px;height:${size}px;border-radius:50%;border:${borderWidth}px solid ${borderColor};box-shadow:${shadow};overflow:hidden;background:${bgColor};box-sizing:content-box;display:flex;align-items:center;justify-content:center;`;
    wrap.innerHTML = photoUrl
      ? `<div style="${innerStyle}"><img src="${attr(photoUrl)}" width="${size}" height="${size}" style="display:block;width:${size}px;height:${size}px;border-radius:50%;object-fit:cover" alt=""></div>${badgeHtml}`
      : `<div style="${innerStyle}">${categoryIconSvg(place.category_icon, selected ? 18 : 15)}</div>${badgeHtml}`;
    return wrap;
  }

  function updateMapHover(place, ev) {
    if (!mapTooltip) return;
    const meta = [place.category_name, place.address].filter(Boolean).map(esc).join(' • ');
    mapTooltip.innerHTML = `<strong>${esc(text(place.name, 'Planned stop'))}</strong>${meta ? `<span>${meta}</span>` : ''}`;
    mapTooltip.hidden = false;
    positionMapHover(ev);
  }

  function positionMapHover(ev) {
    if (!mapTooltip || mapTooltip.hidden) return;
    const x = Math.min(window.innerWidth - 270, ev.clientX + 16);
    const y = Math.min(window.innerHeight - 100, ev.clientY + 16);
    mapTooltip.style.left = `${Math.max(8, x)}px`;
    mapTooltip.style.top = `${Math.max(8, y)}px`;
  }

  function clearMapHover() {
    if (mapTooltip) mapTooltip.hidden = true;
  }

  function initMap() {
    const mapEl = document.getElementById('map');
    if (!mapEl) return;
    mapTooltip = document.getElementById('mapHoverCard');
    if (!window.mapboxgl) {
      mapEl.innerHTML = '<div class="map-unavailable"><strong>Mapbox GL could not load.</strong><span>The itinerary is still available. Check guest-browser access to api.mapbox.com.</span></div>';
      return;
    }
    const token = mapboxToken();
    if (!token || !token.startsWith('pk.')) {
      mapEl.innerHTML = '<div class="map-unavailable"><strong>No public Mapbox token is configured.</strong><span>Edit /datastore/trax-guest/public/config.js and paste the same pk… token used by TREK.</span></div>';
      return;
    }

    mapboxgl.accessToken = token;
    const options = {
      container: 'map',
      style: mapboxStyle(),
      center: [0, 20],
      zoom: 1.5,
      pitch: mapbox3dEnabled() ? 45 : 0,
      attributionControl: true,
      antialias: mapboxHighQuality(),
      projection: mapboxHighQuality() ? 'globe' : 'mercator',
    };
    map = new mapboxgl.Map(options);
    map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true, showCompass: true }), 'top-right');

    const clearHoverOnMove = () => clearMapHover();
    map.on('movestart', clearHoverOnMove);

    map.on('load', () => {
      mapReady = true;
      if (mapboxStyle() === 'mapbox://styles/mapbox/standard') {
        try { map.setTerrain(null); } catch (_) {}
      }

      map.addSource('trip-route', { type:'geojson', data:{ type:'FeatureCollection', features:[] } });
      map.addLayer({ id:'trip-route-casing', type:'line', source:'trip-route', paint:{ 'line-color':'#0a5cc2', 'line-width':8 }, layout:{ 'line-cap':'round', 'line-join':'round' } });
      map.addLayer({ id:'trip-route-line', type:'line', source:'trip-route', paint:{ 'line-color':'#0a84ff', 'line-width':5 }, layout:{ 'line-cap':'round', 'line-join':'round' } });

      map.addSource(PLACE_CLUSTER_SOURCE_ID, {
        type:'geojson',
        data:{ type:'FeatureCollection', features:[] },
        cluster:true,
        clusterRadius:30,
        clusterMaxZoom:10,
      });
      map.addLayer({
        id: PLACE_CLUSTER_CIRCLE_LAYER_ID,
        type:'circle', source:PLACE_CLUSTER_SOURCE_ID,
        filter:['has','point_count'],
        paint:{
          'circle-color':'#111827', 'circle-opacity':0.97,
          'circle-radius':['step',['get','point_count'],18,10,21,50,24],
          'circle-stroke-width':2.5, 'circle-stroke-color':'rgba(255,255,255,0.9)',
        },
      });
      map.addLayer({
        id: PLACE_CLUSTER_COUNT_LAYER_ID,
        type:'symbol', source:PLACE_CLUSTER_SOURCE_ID,
        filter:['has','point_count'],
        layout:{ 'text-field':['get','point_count_abbreviated'], 'text-size':12, 'text-allow-overlap':true },
        paint:{ 'text-color':'#ffffff', 'text-halo-color':'rgba(17,24,39,0.35)', 'text-halo-width':1 },
      });
      map.addLayer({
        id: PLACE_UNCLUSTERED_LAYER_ID,
        type:'circle', source:PLACE_CLUSTER_SOURCE_ID,
        filter:['!', ['has','point_count']],
        paint:{ 'circle-radius':24, 'circle-opacity':0, 'circle-stroke-opacity':0 },
      });

      const zoomToCluster = e => {
        const features = map.queryRenderedFeatures(e.point, { layers:[PLACE_CLUSTER_CIRCLE_LAYER_ID, PLACE_CLUSTER_COUNT_LAYER_ID] });
        const f = features?.[0];
        const clusterId = f?.properties?.cluster_id;
        const coords = f?.geometry?.coordinates;
        if (clusterId == null || !Array.isArray(coords)) return;
        const source = map.getSource(PLACE_CLUSTER_SOURCE_ID);
        const go = zoom => { if (typeof zoom === 'number') map.easeTo({ center:coords, zoom, duration:350 }); };
        try {
          const result = source.getClusterExpansionZoom(clusterId, (err, zoom) => { if (!err) go(zoom); });
          if (typeof result === 'number') go(result);
          else if (result?.then) result.then(go).catch(() => {});
        } catch (_) {}
      };
      map.on('click', PLACE_CLUSTER_CIRCLE_LAYER_ID, zoomToCluster);
      map.on('click', PLACE_CLUSTER_COUNT_LAYER_ID, zoomToCluster);
      map.on('mouseenter', PLACE_CLUSTER_CIRCLE_LAYER_ID, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', PLACE_CLUSTER_CIRCLE_LAYER_ID, () => { map.getCanvas().style.cursor = ''; });

      map.on('moveend', scheduleMarkerReconcile);
      map.on('zoomend', scheduleMarkerReconcile);
      map.on('idle', scheduleMarkerReconcile);

      // If Mapbox was created because a stop was selected, immediately frame
      // the same 1 km area used by subsequent selections. Otherwise fit all.
      refreshMapData(selectedPlaceId == null);
      if (selectedPlaceId != null) {
        const place = currentMapPlaces.find(p => String(p.id) === String(selectedPlaceId))
          || findSharedPlaceById(selectedPlaceId);
        requestAnimationFrame(() => {
          try { map.resize(); } catch (_) {}
          enforceSelectedOneKmRadius({ animate:false, settle:true });
        });
      }
    });

    document.getElementById('fitMap')?.addEventListener('click', () => fitMap());
  }

  function clusterFeatureCollection(places) {
    return {
      type:'FeatureCollection',
      features: places.map(place => ({
        type:'Feature',
        properties:{ placeId:String(place.id) },
        geometry:{ type:'Point', coordinates:[place.lng, place.lat] },
      })),
    };
  }

  function scheduleMarkerReconcile() {
    if (markerReconcileRaf != null) return;
    markerReconcileRaf = requestAnimationFrame(() => {
      markerReconcileRaf = null;
      reconcileMapMarkers();
    });
  }

  function reconcileMapMarkers() {
    if (!map || !mapReady) return;
    const source = map.getSource(PLACE_CLUSTER_SOURCE_ID);
    if (!source) return;
    let visible = currentMapPlaces;
    try {
      const features = map.querySourceFeatures(PLACE_CLUSTER_SOURCE_ID, { filter:['!', ['has','point_count']] }) || [];
      if (features.length) {
        const ids = new Set(features.map(f => String(f?.properties?.placeId ?? '')).filter(Boolean));
        visible = currentMapPlaces.filter(p => ids.has(String(p.id)));
      } else if (map.getZoom() <= 10 && currentMapPlaces.length > 1) {
        visible = [];
      }
    } catch (_) {}

    markerByPlaceId.forEach(marker => { try { marker.remove(); } catch (_) {} });
    markerByPlaceId.clear();

    visible.forEach(place => {
      const id = String(place.id);
      const el = createMapboxMarkerElement(place, currentDayOrderMap[id] || null, id === String(selectedPlaceId));
      el.addEventListener('click', ev => {
        ev.stopPropagation();
        focusPlace(id);
      });
      el.addEventListener('mouseenter', ev => updateMapHover(place, ev));
      el.addEventListener('mousemove', positionMapHover);
      el.addEventListener('mouseleave', clearMapHover);
      const marker = new mapboxgl.Marker({ element:el, anchor:'center' }).setLngLat([place.lng, place.lat]).addTo(map);
      markerByPlaceId.set(id, marker);
    });
  }

  function setRouteGeometry(coords) {
    currentRouteCoords = Array.isArray(coords) ? coords : [];
    if (!map || !mapReady) return;
    const source = map.getSource('trip-route');
    if (!source) return;
    const features = currentRouteCoords.length > 1 ? [{
      type:'Feature', properties:{}, geometry:{ type:'LineString', coordinates:currentRouteCoords },
    }] : [];
    source.setData({ type:'FeatureCollection', features });
  }

  function routeProfile(assignments) {
    const modes = assignments.slice(0, -1).map(a => String(a?.place?.transport_mode || a?.transport_mode || '').toLowerCase());
    if (modes.length && modes.every(m => /walk|foot/.test(m))) return 'walking';
    if (modes.length && modes.every(m => /bike|bicycle|cycl/.test(m))) return 'cycling';
    return 'driving';
  }

  function routeBase(profile) {
    if (profile === 'walking') return 'https://routing.openstreetmap.de/routed-foot/route/v1/foot';
    if (profile === 'cycling') return 'https://routing.openstreetmap.de/routed-bike/route/v1/bike';
    return 'https://routing.openstreetmap.de/routed-car/route/v1/driving';
  }

  function setRouteStatus(message, kind) {
    const el = document.getElementById('mapRouteStatus');
    if (!el) return;
    if (!message) { el.hidden = true; el.textContent = ''; el.className = 'map-route-status'; return; }
    el.hidden = false;
    el.textContent = message;
    el.className = `map-route-status${kind ? ` ${kind}` : ''}`;
  }

  async function updateSelectedDayRoute(fitAfter) {
    routeRequestSerial += 1;
    const serial = routeRequestSerial;
    if (routeAbortController) routeAbortController.abort();
    routeAbortController = null;

    if (selectedDay === 'all') {
      setRouteGeometry([]);
      setRouteStatus('');
      if (fitAfter) fitMap();
      return;
    }

    const assignments = assignmentListForDay(selectedDay)
      .filter(assignmentMatchesSearch)
      .filter(a => num(a?.place?.lat) != null && num(a?.place?.lng) != null);
    const straight = assignments.map(a => [num(a.place.lng), num(a.place.lat)]);
    if (straight.length < 2) {
      setRouteGeometry([]);
      setRouteStatus('');
      if (fitAfter) fitMap();
      return;
    }

    // TREK shows the ordered day route immediately, then upgrades it to real
    // road/path geometry when the routing response lands.
    setRouteGeometry(straight);
    setRouteStatus('Loading route…', 'loading');
    if (fitAfter) fitMap();

    const controller = new AbortController();
    routeAbortController = controller;
    const profile = routeProfile(assignments);
    const coordText = straight.map(([lng, lat]) => `${lng},${lat}`).join(';');
    const url = `${routeBase(profile)}/${coordText}?overview=full&geometries=geojson&steps=false`;
    try {
      const res = await fetch(url, { signal:controller.signal, cache:'no-store' });
      if (!res.ok) throw new Error(`routing ${res.status}`);
      const data = await res.json();
      const coords = data?.routes?.[0]?.geometry?.coordinates;
      if (serial !== routeRequestSerial || !Array.isArray(coords) || coords.length < 2) return;
      setRouteGeometry(coords);
      setRouteStatus('');
      if (fitAfter) fitMap();
    } catch (err) {
      if (err?.name === 'AbortError' || serial !== routeRequestSerial) return;
      // Keep the immediate straight-line route rather than hiding the day.
      setRouteStatus('Approximate route', 'approximate');
    }
  }

  function refreshMapData(fit) {
    if (!map || !mapReady) return;
    const state = buildMapPlaceState();
    currentMapPlaces = state.places;
    currentDayOrderMap = state.orderMap;
    if (selectedPlaceId != null && !currentMapPlaces.some(p => String(p.id) === String(selectedPlaceId))) selectedPlaceId = null;

    const source = map.getSource(PLACE_CLUSTER_SOURCE_ID);
    if (source) source.setData(clusterFeatureCollection(currentMapPlaces));
    scheduleMarkerReconcile();
    updateSelectedDayRoute(!!fit);
  }

  function fitMap() {
    if (!map) return;
    const points = [];
    currentMapPlaces.forEach(p => points.push([p.lng, p.lat]));
    currentRouteCoords.forEach(c => { if (Array.isArray(c) && c.length >= 2) points.push(c); });
    if (!points.length) {
      map.easeTo({ center:[0,20], zoom:1.5, pitch:mapbox3dEnabled() ? 45 : 0, duration:400 });
      return;
    }
    if (points.length === 1) {
      map.flyTo({ center:points[0], zoom:14, pitch:mapbox3dEnabled() ? 45 : 0, duration:400 });
      return;
    }
    const bounds = new mapboxgl.LngLatBounds();
    points.forEach(p => bounds.extend(p));
    const mobile = window.innerWidth < 768;
    try {
      map.fitBounds(bounds, {
        padding: mobile ? { top:40, right:20, bottom:40, left:20 } : { top:60, right:40, bottom:60, left:40 },
        maxZoom:15,
        pitch:mapbox3dEnabled() ? 45 : 0,
        duration:400,
      });
    } catch (_) {}
  }

  const SELECTED_STOP_RADIUS_KM = 1;
  let selectedFocusTimers = [];
  let selectedViewportTimer = null;

  function oneKmBounds(lat, lng) {
    // Geographic bounds are always exactly 1 km north/south/east/west from
    // the selected stop. This value is intentionally viewport-independent.
    const radiusKm = SELECTED_STOP_RADIUS_KM;
    const latDelta = radiusKm / 110.574;
    const cosLat = Math.max(0.15, Math.cos((lat * Math.PI) / 180));
    const lngDelta = radiusKm / (111.320 * cosLat);
    return [[lng - lngDelta, lat - latDelta], [lng + lngDelta, lat + latDelta]];
  }

  function focusMapAroundPlace(place, { duration = 500 } = {}) {
    if (!map || !place) return;
    const lat = num(place.lat ?? place.latitude);
    const lng = num(place.lng ?? place.longitude ?? place.lon);
    if (lat == null || lng == null) return;

    // Use the same geographic radius and the same camera padding on every
    // device. Responsive padding previously caused phones to settle at a
    // visibly different extent than desktop after Mapbox resized.
    try {
      map.fitBounds(oneKmBounds(lat, lng), {
        padding: { top:28, right:28, bottom:28, left:28 },
        maxZoom: 16.5,
        pitch: mapbox3dEnabled() ? 45 : 0,
        duration,
      });
    } catch (_) {
      try {
        map.flyTo({ center:[lng, lat], zoom:15.5, pitch:mapbox3dEnabled() ? 45 : 0, duration });
      } catch (_) {}
    }
  }

  function cancelSelectedFocusTimers() {
    selectedFocusTimers.forEach(t => clearTimeout(t));
    selectedFocusTimers = [];
  }

  function enforceSelectedOneKmRadius({ animate = false, settle = true } = {}) {
    if (!map || !mapReady || selectedPlaceId == null) return;
    const place = currentMapPlaces.find(p => String(p.id) === String(selectedPlaceId))
      || findSharedPlaceById(selectedPlaceId);
    if (!place) return;

    cancelSelectedFocusTimers();
    const apply = duration => {
      try { map.resize(); } catch (_) {}
      focusMapAroundPlace(place, { duration });
    };

    requestAnimationFrame(() => apply(animate ? 500 : 0));

    // Mobile browsers often resize again after scroll/address-bar animation.
    // Re-apply the exact same 1 km bounds after those layout changes settle.
    if (settle) {
      selectedFocusTimers.push(setTimeout(() => apply(0), 220));
      selectedFocusTimers.push(setTimeout(() => apply(0), 650));
    }
  }

  function findSharedPlaceById(id) {
    const wanted = String(id);
    for (const day of (tripData?.days || [])) {
      for (const a of assignmentListForDay(day.id)) {
        const p = normalizedMapPlace(a);
        if (String(p?.id) !== wanted) continue;
        const lat = num(p.lat ?? p.latitude);
        const lng = num(p.lng ?? p.longitude ?? p.lon);
        if (lat == null || lng == null) return null;
        return { ...p, lat, lng };
      }
    }
    return null;
  }

  function focusPlace(id) {
    selectedPlaceId = String(id);
    highlightPlace(selectedPlaceId);
    clearMapHover();

    // Move the one live Mapbox canvas directly beneath the selected stop.
    // Moving the existing DOM node preserves map state and is much cheaper than
    // destroying/recreating a Mapbox instance for every click.
    const attached = placeMapUnderSelectedCard(selectedPlaceId);
    if (!attached) return;

    if (!map) return; // initMap() will focus this place after its load event.

    requestAnimationFrame(() => {
      try { map.resize(); } catch (_) {}

      // Rebuild the map state before focusing so a just-rendered itinerary card
      // never points at stale marker data.
      if (mapReady) refreshMapData(false);
      scheduleMarkerReconcile();

      enforceSelectedOneKmRadius({ animate:true, settle:true });
    });
  }

  function highlightPlace(id) {
    document.querySelectorAll('.place-card').forEach(c => c.classList.toggle('map-active', String(c.dataset.place) === String(id)));
    const card = [...document.querySelectorAll('.place-card[data-place]')].find(c => String(c.dataset.place) === String(id));
    if (card && window.innerWidth <= 980) card.scrollIntoView({ behavior:'smooth', block:'center' });
  }

  function scheduleSelectedRadiusAfterViewportChange() {
    if (selectedViewportTimer) clearTimeout(selectedViewportTimer);
    selectedViewportTimer = setTimeout(() => {
      selectedViewportTimer = null;
      enforceSelectedOneKmRadius({ animate:false, settle:false });
    }, 140);
  }

  window.addEventListener('resize', scheduleSelectedRadiusAfterViewportChange, { passive:true });
  window.addEventListener('orientationchange', () => {
    setTimeout(scheduleSelectedRadiusAfterViewportChange, 250);
  }, { passive:true });
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', scheduleSelectedRadiusAfterViewportChange, { passive:true });
  }

  function destroyMap() {
    cancelSelectedFocusTimers();
    if (selectedViewportTimer) { clearTimeout(selectedViewportTimer); selectedViewportTimer = null; }
    if (routeAbortController) routeAbortController.abort();
    routeAbortController = null;
    routeRequestSerial += 1;
    if (markerReconcileRaf != null) cancelAnimationFrame(markerReconcileRaf);
    markerReconcileRaf = null;
    markerByPlaceId.forEach(marker => { try { marker.remove(); } catch (_) {} });
    markerByPlaceId.clear();
    if (map) { try { map.remove(); } catch (_) {} }
    map = null;
    mapReady = false;
    selectedPlaceId = null;
    mapTooltip = null;
    currentMapPlaces = [];
    currentDayOrderMap = {};
    currentRouteCoords = [];
  }


  function parseMeta(r) {
    let m = r?.metadata ?? r?.meta ?? {};
    if (typeof m === 'string') { try { m = JSON.parse(m || '{}'); } catch (_) { m = {}; } }
    return m && typeof m === 'object' ? m : {};
  }

  function orderedEndpoints(r) {
    return asArray(r?.endpoints).slice().sort((a,b) => (Number(a?.sequence) || 0) - (Number(b?.sequence) || 0));
  }

  function getFlightLegs(r) {
    const m = parseMeta(r);
    if (Array.isArray(m.legs) && m.legs.length) {
      return m.legs.map((l, i) => ({
        index:i,
        from:text(l.from), to:text(l.to), airline:text(l.airline), airlineCode:text(l.airline_code),
        flight:text(l.flight_number,l.flightNumber), depTime:text(l.dep_time), arrTime:text(l.arr_time),
        seat:text(l.seat), depDayId:l.dep_day_id, arrDayId:l.arr_day_id,
      }));
    }
    const eps = orderedEndpoints(r);
    const first = eps[0] || {}, last = eps[eps.length - 1] || {};
    return [{
      index:0,
      from:text(first.code,m.departure_airport), to:text(last.code,m.arrival_airport),
      airline:text(m.airline), airlineCode:text(m.airline_code), flight:text(m.flight_number,m.flightNumber),
      depTime:text(first.local_time,r.reservation_time), arrTime:text(last.local_time,r.reservation_end_time),
      seat:text(m.seat), depDayId:r.day_id, arrDayId:r.end_day_id,
    }];
  }

  function formatReservationDateTime(value) {
    if (!value) return '';
    const date = formatDate(value, { weekday:'short', month:'short', day:'numeric', year:'numeric' });
    const time = String(value).match(/\d{1,2}:\d{2}/) ? formatTime(value) : '';
    return [date,time].filter(Boolean).join(' · ');
  }

  function statusBadge(status) {
    const st = String(status || 'pending').toLowerCase();
    const tone = st === 'confirmed' ? 'confirmed' : (st === 'cancelled' || st === 'canceled' ? 'cancelled' : 'pending');
    return `<span class="status-badge ${tone}">${esc(st.charAt(0).toUpperCase()+st.slice(1))}</span>`;
  }

  function transportIcon(type) {
    const icons = {flight:'✈',train:'▰',bus:'▣',car:'◆',taxi:'◆',bicycle:'◇',cruise:'◈',ferry:'◈'};
    return icons[type] || '→';
  }

  function transportCard(r) {
    const type = reservationType(r) || 'transport';
    const m = parseMeta(r);
    const eps = orderedEndpoints(r);
    const first = eps[0] || {}, last = eps[eps.length-1] || {};
    const start = text(r.reservation_time, first.local_time);
    const end = text(r.reservation_end_time, last.local_time);
    const confirmation = text(r.confirmation_code,r.confirmation_number,r.confirmation,r.booking_reference,r.reference);
    const routeFrom = text(first.code, first.name, m.departure_airport, m.pickup_location);
    const routeTo = text(last.code, last.name, m.arrival_airport, m.return_location);
    const notes = text(r.notes,r.description);
    const flightLegs = type === 'flight' ? getFlightLegs(r) : [];
    const flightHeadline = flightLegs.length ? flightLegs.map(l => [l.airline, l.flight].filter(Boolean).join(' ')).filter(Boolean).join(' · ') : '';
    const route = routeFrom || routeTo ? `${routeFrom || '—'} → ${routeTo || '—'}` : '';
    return `<article class="card transport-card" data-reservation-id="${attr(r.id)}" data-transport-type="${attr(type)}">
      <div class="transport-head">
        <div class="transport-icon" aria-hidden="true">${transportIcon(type)}</div>
        <div class="transport-title-wrap"><div class="transport-kicker">${esc(type)}</div><h3>${esc(text(r.title, type === 'flight' ? 'Flight' : 'Transport'))}</h3>${flightHeadline ? `<div class="transport-sub">${esc(flightHeadline)}</div>` : ''}</div>
        ${statusBadge(r.status)}
      </div>
      ${route ? `<div class="transport-route"><strong>${esc(routeFrom || '—')}</strong><span class="route-line"><i></i><b>→</b></span><strong>${esc(routeTo || '—')}</strong></div>` : ''}
      <div class="transport-facts">
        ${start ? `<div><span>Departure</span><strong>${esc(formatReservationDateTime(start))}</strong></div>` : ''}
        ${end ? `<div><span>Arrival</span><strong>${esc(formatReservationDateTime(end))}</strong></div>` : ''}
        ${confirmation ? `<div><span>Confirmation</span><strong class="confirmation">${esc(confirmation)}</strong></div>` : ''}
        ${text(r.location) ? `<div><span>Location</span><strong>${esc(r.location)}</strong></div>` : ''}
      </div>
      ${flightLegs.length > 1 ? `<div class="flight-leg-summary">${flightLegs.map((l,i) => `<span><b>${i+1}</b>${esc([l.flight,[l.from,l.to].filter(Boolean).join(' → ')].filter(Boolean).join(' · '))}</span>`).join('')}</div>` : ''}
      ${notes ? `<div class="transport-notes">${esc(notes)}</div>` : ''}
      ${type === 'flight' ? `<div class="flight-live" id="flight-live-${attr(r.id)}"><div class="live-loading"><span class="mini-spinner"></span>Loading current flight information…</div></div>` : ''}
    </article>`;
  }

  function formatCountdown(totalSeconds) {
    const seconds = Math.max(0, Math.ceil(Number(totalSeconds) || 0));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
    if (minutes > 0) return `${minutes}m ${secs}s`;
    return `${secs}s`;
  }

  function updateFlightRefreshHeading() {
    const el = document.getElementById('flight-next-refresh');
    if (!el || activeTab !== 'flights') return;
    const values = Array.from(flightNextRefreshAt.values());
    if (!values.length) {
      el.textContent = 'No flights to refresh';
      return;
    }
    if (values.some(v => v === 'checking')) {
      el.textContent = 'Checking…';
      return;
    }
    const scheduled = values.filter(v => Number.isFinite(v));
    if (!scheduled.length) {
      el.textContent = 'No further refresh scheduled';
      return;
    }
    const nextAt = Math.min(...scheduled);
    const seconds = Math.max(0, Math.ceil((nextAt - Date.now()) / 1000));
    el.textContent = `in ${formatCountdown(seconds)}`;
  }

  function startFlightCountdown() {
    if (flightCountdownTimer) clearInterval(flightCountdownTimer);
    flightCountdownTimer = setInterval(updateFlightRefreshHeading, 1000);
    updateFlightRefreshHeading();
  }

  function clearFlightRefreshTimers() {
    flightRefreshTimers.forEach(timer => clearTimeout(timer));
    flightRefreshTimers.clear();
    flightNextRefreshAt.clear();
    if (flightCountdownTimer) clearInterval(flightCountdownTimer);
    flightCountdownTimer = null;
  }

  function scheduleLiveFlightRefresh(r, payload) {
    const id = String(r?.id ?? '');
    if (!id || activeTab !== 'flights') return;
    const old = flightRefreshTimers.get(id);
    if (old) clearTimeout(old);
    const raw = Number(payload?._guestLive?.refreshAfterSeconds);
    if (!Number.isFinite(raw) || raw <= 0) {
      flightRefreshTimers.delete(id);
      flightNextRefreshAt.set(id, null);
      updateFlightRefreshHeading();
      return;
    }
    // The server is the quota authority. It returns the remaining TTL, so even
    // if multiple guests are watching the same flight, only the first request
    // after expiry causes an AeroDataBox/adsb.fi refresh.
    const delayMs = Math.max(15000, Math.min(raw * 1000, 2 * 60 * 60 * 1000));
    flightNextRefreshAt.set(id, Date.now() + delayMs);
    updateFlightRefreshHeading();
    const timer = setTimeout(() => {
      flightRefreshTimers.delete(id);
      if (activeTab !== 'flights') return;
      if (document.hidden) {
        const retryDelay = 60000;
        flightNextRefreshAt.set(id, Date.now() + retryDelay);
        updateFlightRefreshHeading();
        const retry = setTimeout(() => {
          flightRefreshTimers.delete(id);
          flightNextRefreshAt.set(id, 'checking');
          updateFlightRefreshHeading();
          if (activeTab === 'flights') loadLiveFlight(r);
        }, retryDelay);
        flightRefreshTimers.set(id, retry);
        return;
      }
      flightNextRefreshAt.set(id, 'checking');
      updateFlightRefreshHeading();
      loadLiveFlight(r);
    }, delayMs);
    flightRefreshTimers.set(id, timer);
  }

  function renderFlights(content) {
    clearFlightRefreshTimers();
    const transports = transportReservations().slice().sort((a,b) => dateSortValue(a.reservation_time) - dateSortValue(b.reservation_time));
    const flights = transports.filter(r => reservationType(r) === 'flight');
    flights.forEach(r => flightNextRefreshAt.set(String(r.id), 'checking'));
    content.innerHTML = `<div class="section-heading-row"><div><h2 class="section-title">Flights & transport</h2></div><div class="flight-refresh-heading"><span>Next auto refresh</span><strong id="flight-next-refresh">${flights.length ? 'Checking…' : 'No flights to refresh'}</strong></div></div>
      ${transports.length ? `<div class="transport-list">${transports.map(transportCard).join('')}</div>` : '<div class="card empty">No transport reservations are shared.</div>'}`;
    startFlightCountdown();
    flights.forEach(r => loadLiveFlight(r));
  }

  async function loadLiveFlight(r) {
    const target = document.getElementById(`flight-live-${r.id}`);
    if (!target) return;
    const id = String(r?.id ?? '');
    if (id) {
      flightNextRefreshAt.set(id, 'checking');
      updateFlightRefreshHeading();
    }
    try {
      const payload = await fetchJson(`api/flights/${encodeURIComponent(r.id)}`);
      if (!document.getElementById(`flight-live-${r.id}`)) return;
      target.innerHTML = renderFlightTrackerPayload(payload, r);
      scheduleLiveFlightRefresh(r, payload);
    } catch (err) {
      const msg = String(err?.message || 'Live flight data unavailable');
      target.innerHTML = `<div class="live-unavailable"><strong>Current flight information unavailable</strong><span>${esc(msg)}</span></div>`;
      scheduleLiveFlightRefresh(r, { _guestLive: { refreshAfterSeconds: 300 } });
    }
  }

  function trackerTime(block, fallback, revisedFirst=true) {
    if (!block) return fallback || '';
    return text(revisedFirst ? block.revised : block.scheduled, block.scheduled, block.revised, fallback);
  }

  function delayText(minutes) {
    const n = Number(minutes);
    if (!Number.isFinite(n) || Math.abs(n) < 1) return '';
    return n > 0 ? `+${Math.round(n)} min` : `${Math.round(n)} min`;
  }

  function liveStatusTone(status) {
    const s=String(status||'').toLowerCase();
    if (/cancel|divert/.test(s)) return 'bad';
    if (/delay/.test(s)) return 'warn';
    if (/arriv|landed/.test(s)) return 'ok';
    if (/enroute|departed|boarding|approaching/.test(s)) return 'info';
    if (/scheduled|unknown/.test(s)) return 'neutral';
    return 'neutral';
  }

  function trackerLegHtml(leg, idx, total) {
    const s = leg?.status || null;
    const live = leg?.live || null;
    const dep = s?.departure || {};
    const arr = s?.arrival || {};
    const from = text(dep.iata,leg.from,'—');
    const to = text(arr.iata,leg.to,'—');
    const number = text(leg.number,s?.number,leg.flight);
    const airline = text(s?.airline,leg.airline);
    const depSched = text(dep.scheduled,leg.depTime);
    const depCurrent = text(dep.revised,dep.scheduled,leg.depTime);
    const arrSched = text(arr.scheduled,leg.arrTime);
    const arrCurrent = text(arr.revised,arr.scheduled,leg.arrTime);
    const depChanged = dep.revised && dep.scheduled && dep.revised !== dep.scheduled;
    const arrChanged = arr.revised && arr.scheduled && arr.revised !== arr.scheduled;
    const aircraft = [text(live?.desc,live?.type), live?.reg].filter(Boolean).join(' · ');
    const altitude = live?.altBaro != null && live.altBaro !== 'ground' ? `${Math.round(Number(live.altBaro)).toLocaleString()} ft` : '';
    const speed = Number.isFinite(Number(live?.groundSpeed)) ? `${Math.round(Number(live.groundSpeed))} kt` : '';
    const seen = Number.isFinite(Number(live?.seenPos)) ? `${Math.round(Number(live.seenPos))}s old` : '';
    const inbound = leg?.inbound || null;
    const weather = leg?.weather || null;
    const weatherText = weather && Number.isFinite(Number(weather.temp)) ? `${Math.round(Number(weather.temp))}° · ${text(weather.description,weather.main)}` : text(weather?.description,weather?.main);
    return `<section class="tracker-leg">
      <div class="tracker-head">
        <div><span class="tracker-seq">${total > 1 ? `Leg ${idx+1}` : 'Flight'}</span><strong>${esc(number || 'Flight')}</strong>${airline ? `<span>${esc(airline)}</span>` : ''}</div>
        ${s?.status ? `<span class="live-status ${liveStatusTone(s.status)}">${esc(s.status)}</span>` : '<span class="live-status neutral">Scheduled</span>'}
      </div>
      <div class="tracker-route"><div><b>${esc(from)}</b><span>${esc(text(dep.name))}</span></div><div class="tracker-plane">✈</div><div class="right"><b>${esc(to)}</b><span>${esc(text(arr.name))}</span></div></div>
      <div class="tracker-times">
        <div><span>Departure</span><strong>${esc(formatTime(depCurrent)) || '—'}</strong>${depChanged ? `<small>Scheduled ${esc(formatTime(depSched))}</small>` : ''}${delayText(s?.depDelayMin) ? `<em>${esc(delayText(s.depDelayMin))}</em>` : ''}</div>
        <div><span>Arrival</span><strong>${esc(formatTime(arrCurrent)) || '—'}</strong>${arrChanged ? `<small>Scheduled ${esc(formatTime(arrSched))}</small>` : ''}${delayText(s?.delayMin) ? `<em>${esc(delayText(s.delayMin))}</em>` : ''}</div>
      </div>
      <div class="tracker-details">
        ${dep.terminal ? `<span><b>Depart terminal</b>${esc(dep.terminal)}</span>` : ''}
        ${dep.gate ? `<span><b>Depart gate</b>${esc(dep.gate)}</span>` : ''}
        ${arr.terminal ? `<span><b>Arrival terminal</b>${esc(arr.terminal)}</span>` : ''}
        ${arr.gate ? `<span><b>Arrival gate</b>${esc(arr.gate)}</span>` : ''}
        ${arr.baggageBelt ? `<span><b>Baggage</b>${esc(arr.baggageBelt)}</span>` : ''}
        ${leg.seat ? `<span class="seat"><b>Seat</b>${esc(leg.seat)}</span>` : ''}
      </div>
      ${live && live.lat != null ? `<div class="aircraft-live"><div><span class="pulse-dot"></span><strong>${live.onGround ? 'Aircraft position' : 'Live aircraft'}</strong></div><div class="aircraft-facts">${aircraft ? `<span>${esc(aircraft)}</span>` : ''}${altitude ? `<span>${esc(altitude)}</span>` : ''}${speed ? `<span>${esc(speed)}</span>` : ''}${seen ? `<span>${esc(seen)}</span>` : ''}</div></div>` : ''}
      ${!live?.lat && inbound?.lat != null ? `<div class="aircraft-live inbound"><div><span class="pulse-dot"></span><strong>Inbound aircraft</strong></div><div class="aircraft-facts">${inbound.reg ? `<span>${esc(inbound.reg)}</span>` : ''}${Number.isFinite(Number(inbound.groundSpeed)) ? `<span>${esc(Math.round(Number(inbound.groundSpeed)))} kt</span>` : ''}</div></div>` : ''}
      ${weatherText ? `<div class="flight-weather"><strong>Arrival weather</strong><span>${esc(weatherText)}</span>${Number.isFinite(Number(weather?.precipProb)) ? `<span>${esc(Math.round(Number(weather.precipProb)))}% precipitation</span>` : ''}</div>` : ''}
    </section>`;
  }

  function renderFlightTrackerPayload(payload, reservation) {
    const legs = asArray(payload?.legs);
    const fetched = payload?._guestCache?.fetchedAt || payload?._guestLive?.fetchedAt || payload?.updatedAt;
    const updated = fetched ? new Date(Number(fetched)).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}) : '';
    const age = Number(payload?._guestCache?.ageSeconds);
    const stale = Number.isFinite(age) && age > Math.max(1800, Number(payload?._guestLive?.ttlSeconds || 0));
    if (payload?.applicable === false) return '<div class="live-unavailable"><strong>Flight Tracker</strong><span>This reservation is not recognized as a flight.</span></div>';
    if (!legs.length) return `<div class="live-unavailable"><strong>Flight Tracker</strong><span>${esc(asArray(payload?.errors)[0] || 'No current flight data is cached yet.')}</span></div>`;
    return `<div class="tracker-wrap"><div class="tracker-bar"><div><strong>Flight Tracker</strong><span>${payload?._guestLive?.configured ? 'Live flight information' : (payload?.source ? esc(payload.source) : 'Flight information')}</span></div><div class="tracker-updated ${stale ? 'stale' : ''}">${updated ? `Updated ${esc(updated)}` : ''}${stale ? ' · cached' : ''}</div></div>${legs.map((l,i)=>trackerLegHtml(l,i,legs.length)).join('')}${asArray(payload?.errors).length ? `<div class="tracker-errors">${asArray(payload.errors).slice(0,2).map(e=>`<span>${esc(e)}</span>`).join('')}</div>` : ''}</div>`;
  }

  function reservationCard(r) {
    const type = reservationType(r) || 'reservation';
    const meta = parseMeta(r);
    const title = text(r.title,r.name,'Reservation');
    const start = text(r.reservation_time,r.start_time,r.start_date,r.date);
    const end = text(r.reservation_end_time,r.end_time,r.end_date);
    const confirmation = text(r.confirmation_code,r.confirmation_number,r.confirmation,r.booking_reference,r.reference);
    const location = text(r.location,r.address,meta.location,meta.address);
    const notes = text(r.notes,r.description);
    const rows=[];
    if(start) rows.push(['When',formatReservationDateTime(start)]);
    if(end) rows.push(['Ends',formatReservationDateTime(end)]);
    if(location) rows.push(['Location',location]);
    if(confirmation) rows.push(['Confirmation',confirmation]);
    const provider=text(meta.provider,r.provider,r.operator,r.company);
    if(provider) rows.push(['Provider',provider]);
    return `<article class="card reservation-card"><div class="reservation-card-head"><span class="badge">${esc(type)}</span>${statusBadge(r.status)}</div><h3>${esc(title)}</h3><div class="reservation-kvs">${rows.map(([k,v])=>`<div class="kv"><div class="k">${esc(k)}</div><div class="${k==='Confirmation'?'confirmation':''}">${esc(v)}</div></div>`).join('')}</div>${notes?`<div class="reservation-notes">${esc(notes)}</div>`:''}</article>`;
  }

  function accommodationCard(a) {
    const title = text(a.place_name,a.title,a.name,'Accommodation');
    const checkIn = text(a.check_in,a.checkin,a.start_time,a.start_date);
    const checkOut = text(a.check_out,a.checkout,a.end_time,a.end_date);
    const confirmation = text(a.confirmation,a.confirmation_number,a.booking_reference,a.reference);
    const address = text(a.place_address,a.address,a.location);
    const notes = text(a.notes,a.description);
    const rows=[];
    if(checkIn) rows.push(['Check-in',formatReservationDateTime(checkIn)]);
    if(checkOut) rows.push(['Check-out',formatReservationDateTime(checkOut)]);
    if(address) rows.push(['Address',address]);
    if(confirmation) rows.push(['Confirmation',confirmation]);
    return `<article class="card reservation-card accommodation-card"><div class="reservation-card-head"><span class="badge accommodation">Accommodation</span>${statusBadge(a.status || 'confirmed')}</div><h3>${esc(title)}</h3><div class="reservation-kvs">${rows.map(([k,v])=>`<div class="kv"><div class="k">${esc(k)}</div><div class="${k==='Confirmation'?'confirmation':''}">${esc(v)}</div></div>`).join('')}</div>${notes?`<div class="reservation-notes">${esc(notes)}</div>`:''}</article>`;
  }

  function renderReservations(content) {
    const reservations = nonTransportReservations().slice().sort((a,b)=>dateSortValue(a.reservation_time||a.start_date)-dateSortValue(b.reservation_time||b.start_date));
    const accommodations = asArray(tripData?.accommodations).slice().sort((a,b)=>dateSortValue(a.check_in||a.start_date)-dateSortValue(b.check_in||b.start_date));
    const total = reservations.length + accommodations.length;
    content.innerHTML = `<h2 class="section-title">Reservations</h2>
      ${total ? `<div class="reservation-sections">${accommodations.length?`<section><div class="subsection-title"><h3>Accommodations</h3><span>${accommodations.length}</span></div><div class="data-grid reservation-grid">${accommodations.map(accommodationCard).join('')}</div></section>`:''}${reservations.length?`<section><div class="subsection-title"><h3>Bookings</h3><span>${reservations.length}</span></div><div class="data-grid reservation-grid">${reservations.map(reservationCard).join('')}</div></section>`:''}</div>` : '<div class="card empty">No non-transport reservations are shared.</div>'}`;
  }

  function photoThumb(photo) {
    return `api/photos/${encodeURIComponent(photo.photo_id ?? photo.id)}/thumbnail`;
  }
  function photoOriginal(photo) {
    return `api/photos/${encodeURIComponent(photo.photo_id ?? photo.id)}/original`;
  }

  function renderPhotos(content) {
    const groups = [];
    const byDate = new Map();
    gallery.forEach((photo, index) => {
      const key = photo._dateKey || 'undated';
      let group = byDate.get(key);
      if (!group) {
        group = { key, date: photo._displayDate || '', items: [] };
        byDate.set(key, group);
        groups.push(group);
      }
      group.items.push({ photo, index });
    });

    const galleryHtml = groups.map(group => {
      const dateLabel = group.key === 'undated'
        ? 'Undated'
        : formatDate(group.key, { weekday:'long', month:'long', day:'numeric', year:'numeric' });
      return `<section class="photo-date-group">
        <div class="photo-date-heading"><h3>${esc(dateLabel)}</h3><span>${group.items.length} photo${group.items.length === 1 ? '' : 's'}</span></div>
        <div class="gallery">${group.items.map(({ photo:p, index:i }) => `<figure class="photo ${String(p.media_type || '').startsWith('video') ? 'video' : ''}" data-photo="${i}"><img loading="lazy" decoding="async" src="${attr(photoThumb(p))}" alt="${attr(text(p.caption,'Trip photo'))}">${p.caption ? `<figcaption class="caption">${esc(p.caption)}</figcaption>` : ''}</figure>`).join('')}</div>
      </section>`;
    }).join('');

    content.innerHTML = `<h2 class="section-title">Photos</h2>${gallery.length ? galleryHtml : '<div class="card empty">No photos are shared.</div>'}`;
    document.querySelectorAll('[data-photo]').forEach(el => el.addEventListener('click', () => openLightbox(Number(el.dataset.photo))));
  }

  function openLightbox(index) {
    if (!gallery.length) return;
    lightboxIndex = Math.max(0, Math.min(index, gallery.length - 1));
    const lb = document.getElementById('lightbox');
    lb.hidden = false;
    document.body.style.overflow = 'hidden';
    updateLightbox();
  }
  function closeLightbox() {
    const lb = document.getElementById('lightbox');
    if (lb) lb.hidden = true;
    const video = document.getElementById('lbVideo');
    if (video) { video.pause(); video.removeAttribute('src'); }
    document.body.style.overflow = '';
  }
  function moveLightbox(delta) {
    if (!gallery.length) return;
    lightboxIndex = (lightboxIndex + delta + gallery.length) % gallery.length;
    updateLightbox();
  }
  function updateLightbox() {
    const p = gallery[lightboxIndex];
    if (!p) return;
    document.getElementById('lbCounter').textContent = `${lightboxIndex + 1} / ${gallery.length}`;
    document.getElementById('lbCaption').textContent = [p._displayDate ? formatDate(p._displayDate, { weekday:'long', month:'long', day:'numeric', year:'numeric' }) : '', p.caption || ''].filter(Boolean).join(' • ');
    const img = document.getElementById('lbImage');
    const video = document.getElementById('lbVideo');
    const isVideo = String(p.media_type || '').toLowerCase().startsWith('video');
    if (isVideo) {
      img.hidden = true;
      video.hidden = false;
      video.src = photoOriginal(p);
      video.load();
    } else {
      video.pause();
      video.removeAttribute('src');
      video.hidden = true;
      img.hidden = false;
      img.alt = p.caption || 'Trip photo';
      img.src = photoOriginal(p);
    }
  }

  load();
})();
