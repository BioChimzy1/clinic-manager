const CACHE_NAME = 'clinicmanager-v4';   // was v3

const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/js/outbox.js'
];

// Pages safe to cache: their HTML no longer embeds live data (queue
// data now comes from /api/queue via JS), so a stale copy of the shell
// itself is never wrong — only the data inside it can be stale, and
// that's clearly labeled by queue.html itself.
// Do NOT add /cashier, /dashboard, /finance, /inventory here until
// they've been converted to the same shell + JSON pattern.
const SAFE_TO_CACHE_PAGES = [
  '/register',
  '/queue',
  '/price_list',
  '/inventory'
];

importScripts('/static/js/outbox.js');

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
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