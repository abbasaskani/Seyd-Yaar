/* Seyd‑Yaar Service Worker — cache static assets, but ALWAYS refresh dynamic data (latest/ + runs/) */

const CACHE = "seydyaar-v0.3.1"; // bump this when you change SW

// Only STATIC assets here. ❗Do NOT pre-cache latest/* or runs/*
const CORE = [
  "./",
  "./index.html",
  "./app.html",
  "./styles.css",
  "./home.js",
  "./app.js",
  "./manifest.json",
  "./assets/logo.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(CORE)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => (k === CACHE ? null : caches.delete(k))));
    await self.clients.claim();
  })());
});

function isDynamic(url) {
  // GitHub Pages: app is served under /<repo>/
  // We must keep latest/* and runs/* always fresh.
  return (
    url.pathname.includes("/latest/") ||
    url.pathname.includes("/runs/")
  );
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Only handle same-origin requests.
  if (url.origin !== self.location.origin) return;

  // ✅ Dynamic data: network-first (no-store)
  if (isDynamic(url)) {
    event.respondWith(
      fetch(req, { cache: "no-store" })
        .then((res) => {
          // Only cache successful responses
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // ✅ Static assets: cache-first
  event.respondWith(
    caches.match(req).then((hit) => {
      if (hit) return hit;
      return fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      });
    })
  );
});
