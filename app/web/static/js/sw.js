// Service worker: makes the app installable and keeps static assets available offline.
// Pages and data always come from the network; offline only a short notice is shown.
const CACHE = "kbh-static-v3";
const OFFLINE_URL = "/static/offline.html";
const PRECACHE = [OFFLINE_URL, "/static/css/offline.css"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch((err) => {
        console.warn("Seite nicht erreichbar, zeige Offline-Hinweis:", err);
        return caches.match(OFFLINE_URL).then((cached) => cached || Response.error());
      }),
    );
    return;
  }

  if (!url.pathname.startsWith("/static/")) return;

  // Versioned assets (?v=<hash>) never change under the same URL: cache first.
  // Everything else under /static (fonts, offline page) goes to the network first.
  if (url.searchParams.has("v")) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetchAndCache(request)),
    );
  } else {
    event.respondWith(
      fetchAndCache(request).catch(() =>
        caches.match(request).then((cached) => cached || Response.error()),
      ),
    );
  }
});

function fetchAndCache(request) {
  return fetch(request).then((response) => {
    if (response.ok) {
      const copy = response.clone();
      caches.open(CACHE).then((cache) => cache.put(request, copy));
    }
    return response;
  });
}
