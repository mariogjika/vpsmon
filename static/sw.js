// VPSMon Service Worker
// - Precaches shell
// - Network-first for API, cache-first for static, offline fallback for HTML
// - Handles push events and notification clicks

const VERSION = 'vpsmon-v3.2';
const SHELL_CACHE = `shell-${VERSION}`;
const RUNTIME_CACHE = `runtime-${VERSION}`;

const SHELL = [
  '/',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/js/chart-advanced.js',
  '/static/js/push.js',
  '/static/manifest.json',
  '/static/icon.svg',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((c) => c.addAll(SHELL).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => !k.endsWith(VERSION))
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // WebSocket — never cache
  if (url.pathname.startsWith('/ws')) return;

  // API — network-first, no cache (live data)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request).catch(() =>
        new Response(JSON.stringify({ error: 'offline', offline: true }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' }
        })
      )
    );
    return;
  }

  // Static — cache-first, then network, update cache
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((cached) => {
        const fetchP = fetch(request).then((resp) => {
          if (resp.ok) {
            const copy = resp.clone();
            caches.open(RUNTIME_CACHE).then((c) => c.put(request, copy));
          }
          return resp;
        }).catch(() => cached);
        return cached || fetchP;
      })
    );
    return;
  }

  // HTML shell — network-first with offline fallback
  event.respondWith(
    fetch(request)
      .then((resp) => {
        const copy = resp.clone();
        caches.open(SHELL_CACHE).then((c) => c.put('/', copy));
        return resp;
      })
      .catch(() => caches.match('/'))
  );
});

// --- Web Push ---
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { title: 'VPSMon', body: event.data ? event.data.text() : '' }; }
  const title = data.title || 'VPSMon Alert';
  const opts = {
    body: data.body || '',
    icon: '/static/icon.svg',
    badge: '/static/icon.svg',
    tag: data.tag || 'vpsmon-alert',
    renotify: !!data.renotify,
    requireInteraction: data.severity === 'critical',
    data: data,
    actions: data.actions || [
      { action: 'view',    title: 'Open Dashboard' },
      { action: 'dismiss', title: 'Dismiss' }
    ]
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.action === 'dismiss') return;
  const url = (event.notification.data && event.notification.data.url) || '/#alerts';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.includes(self.location.origin)) {
          client.focus();
          client.postMessage({ type: 'notification-click', data: event.notification.data });
          return;
        }
      }
      return clients.openWindow(url);
    })
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'skip-waiting') self.skipWaiting();
});
