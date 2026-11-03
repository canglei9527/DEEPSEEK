const CACHE = 'jingxiao-ipad-shell-v3';
const FILES = [
  './',
  './index.html',
  './styles.css',
  './backup.js',
  './online-video.js',
  './app.js',
  './manifest.webmanifest',
  './icon-192.png',
  './icon-512.png',
  './apple-touch-icon.png'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key.startsWith('jingxiao-ipad-shell-') && key !== CACHE).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;

  event.respondWith((async () => {
    try {
      const response = await fetch(request);
      if (response.ok && url.pathname.startsWith(new URL(self.registration.scope).pathname)) {
        const cache = await caches.open(CACHE);
        cache.put(request, response.clone()).catch(() => {});
      }
      return response;
    } catch {
      const cache = await caches.open(CACHE);
      const cached = await cache.match(request, { ignoreSearch: true });
      if (cached) return cached;
      if (request.mode === 'navigate') return cache.match('./index.html');
      return Response.error();
    }
  })());
});
