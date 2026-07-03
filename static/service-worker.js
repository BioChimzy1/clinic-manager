const CACHE_NAME = 'clinicmanager-v5';   // was v4

const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/js/outbox.js'
];

// External CDN assets the app depends on for styling and interactive
// components (collapses, dropdowns, dismissible alerts). Cached
// separately from STATIC_ASSETS via individual cache.add() calls
// wrapped in Promise.allSettled (see install handler below) -- if a
// CDN hiccups during install, that failure is isolated and won't also
// block caching of our own same-origin assets above.
const CDN_ASSETS = [
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

// Pages safe to cache: their HTML no longer embeds live data that
// matters once cached (queue/inventory/dashboard data all comes from
// their own /api/... endpoint via JS, and any numbers baked into the
// cached HTML shell get immediately overwritten on load), so a stale
// copy of the shell itself is never wrong -- only the data inside it
// can be stale, and that's clearly labeled by each page's own JS.
const SAFE_TO_CACHE_PAGES = [
  '/register',
  '/queue',
  '/price_list',
  '/inventory',
  '/dashboard'
];

importScripts('/static/js/outbox.js');

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll(STATIC_ASSETS).then(() =>
        Promise.allSettled(CDN_ASSETS.map((url) => cache.add(url)))
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);
  const isSafePage = SAFE_TO_CACHE_PAGES.includes(url.pathname);

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (isSafePage && response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(() =>
        caches.match(event.request).then((cached) => {
          return cached || new Response(
            'You are offline and this page was not cached.',
            { status: 503, headers: { 'Content-Type': 'text/plain' } }
          );
        })
      )
  );
});

self.addEventListener('sync', (event) => {
  if (event.tag === 'sync-queue-registrations') {
    event.waitUntil(cmSyncAllRegistrations());
  }
});