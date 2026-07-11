const CACHE_NAME = 'clinicmanager-v10';   // Bumped to force update after role-snapshot fix (was v9)

const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/js/outbox.js',
  '/static/js/shell.js',
  '/static/js/pages/public-nav.js',
  '/static/css/app.css'
];

const CDN_ASSETS = [
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js'
];

// Map short friendly URLs to their actual static file paths
// This allows the service worker to cache and serve the correct file
// when the user visits the short URL.
const STATIC_ROUTE_MAP = {
  '/': '/static/pages/home.html',
  '/home': '/static/pages/home.html',
  '/login': '/static/pages/login.html',
  '/register': '/static/pages/register.html',
  '/queue': '/static/pages/queue.html',
  '/dashboard': '/static/pages/dashboard.html',
  '/inventory': '/static/pages/inventory.html',
  '/price_list': '/static/pages/price_list.html',
  '/cashier': '/static/pages/cashier.html',
  '/loans': '/static/pages/loans.html',
  '/retail': '/static/pages/retail.html',
  '/appointments': '/static/pages/appointments.html',
  '/finance': '/static/pages/finance.html',
  '/staff': '/static/pages/staff.html',
  '/about': '/static/pages/about.html',
  '/contact': '/static/pages/contact.html',
  '/select_clinic': '/static/pages/select_clinic.html',
  '/setup_clinic': '/static/pages/setup_clinic.html'
};

// NEW: precache every mapped page shell (dedup via Set, since '/' and
// '/home' both point at home.html) so offline viewing works from the
// very first install -- not only after the user has visited that page
// once while online. This is the fix for pages not being available
// offline until manually opened first.
const PAGE_SHELLS = [...new Set(Object.values(STATIC_ROUTE_MAP))];

importScripts('/static/js/outbox.js');

// --- INSTALL ---
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll([...STATIC_ASSETS, ...PAGE_SHELLS]).then(() =>
        Promise.allSettled(CDN_ASSETS.map((url) => cache.add(url)))
      )
    )
  );
  self.skipWaiting();
});

// --- ACTIVATE ---
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

// --- FETCH ---
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  const url = new URL(event.request.url);

  // 1. Skip cross-origin requests
  if (url.origin !== self.location.origin) return;

  // 2. API requests — network only, pages handle offline via IndexedDB
  if (url.pathname.startsWith('/api/')) {
    return;
  }

  // 3. Handle mapped static pages (short URLs like /register)
  if (STATIC_ROUTE_MAP[url.pathname]) {
    const staticPath = STATIC_ROUTE_MAP[url.pathname];
    event.respondWith(
      caches.match(staticPath).then((cachedResponse) => {
        if (cachedResponse) {
          return cachedResponse;
        }
        // If not cached, fetch it, cache it, and return it
        return fetch(staticPath).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(staticPath, clone));
          }
          return response;
        }).catch(() => {
          // Offline fallback if the static file isn't cached yet
          return caches.match('/static/pages/offline.html') || new Response(
            '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Offline — ClinicManager</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><style>body{background:#FAF7F2;font-family:Inter,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}.offline-card{background:#fff;border-radius:16px;padding:2rem;max-width:360px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,0.08)}.dot{width:48px;height:48px;background:#0A3530;border-radius:50%;margin:0 auto 1rem;display:flex;align-items:center;justify-content:center;color:#fff;font-size:1.5rem}</style></head><body><div class="offline-card"><div class="dot">📡</div><h4 class="mb-2">You are offline</h4><p class="text-muted mb-3">This page was not visited while online. Go online once to cache it.</p><button onclick="location.reload()" class="btn btn-success w-100">🔄 Retry</button></div></body></html>',
            { status: 503, headers: { 'Content-Type': 'text/html' } }
          );
        });
      })
    );
    return;
  }

  // 4. Static assets (JS, CSS, images) — Cache-first strategy
  if (url.pathname.startsWith('/static/') || CDN_ASSETS.includes(url.href)) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        }).catch(() => {
          return new Response('Asset not available offline', { status: 503 });
        });
      })
    );
    return;
  }

  // 5. Everything else — try network, fallback to cache
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});

// --- SYNC ---
self.addEventListener('sync', (event) => {
  if (event.tag === 'sync-queue-registrations') {
    event.waitUntil(cmSyncAllRegistrations());
  }
});