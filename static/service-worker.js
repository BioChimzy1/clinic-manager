const CACHE_NAME = 'clinicmanager-v1';

// Only cache static, non-changing assets. Do NOT cache HTML pages here —
// this app's pages are all dynamic/DB-driven (queue, cashier, finance),
// so caching them would show stale patient/financial data offline.
const STATIC_ASSETS = [
  '/static/manifest.json'
];

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

// Network-first for everything. This is a data-heavy clinical app —
// stale cached HTML showing the wrong queue or wrong stock levels is
// worse than no offline support at all. We only fall back to cache for
// the static assets listed above, and only when the network truly fails.
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(event.request).then((cached) => {
        return cached || new Response(
          'You are offline and this page was not cached.',
          { status: 503, headers: { 'Content-Type': 'text/plain' } }
        );
      })
    )
  );
});