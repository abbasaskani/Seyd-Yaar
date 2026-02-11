const CACHE = "seydyaar-v0.2.1"; // bump to kill old caches

const CORE = [
  "./",
  "./index.html",
  "./app.html",
  "./styles.css",
  "./home.js",
  "./app.js",
  "./manifest.json",
  "./assets/logo.png",
  "./latest/meta_index.json",
  "./latest/preview.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(CORE))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map(k => (k === CACHE) ? null : caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // only handle same-origin
  if (url.origin !== location.origin) return;

  // ✅ These change every run/day → ALWAYS try network first
  const isDynamicData =
    url.pathname.includes("/latest/") ||
    url.pathname.includes("/runs/");

  if (isDynamicData) {
    event.respondWith((async () => {
      try {
        // network-first with no-store so browser HTTP cache doesn't reuse stale files
        const fresh = await fetch(req, { cache: "no-store" });
        if (fresh && fresh.status === 200) {
          const cache = await caches.open(CACHE);
          cache.put(req, fresh.clone());
        }
        return fresh;
      } catch (err) {
        const cache = await caches.open(CACHE);
        const cached = await cache.match(req);
        return cached || new Response("Offline", { status: 503 });
      }
    })());
    return;
  }

  // ✅ Static assets → cache-first
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const cached = await cache.match(req);
    if (cached) return cached;

    try {
      const fresh = await fetch(req);
      if (fresh && fresh.status === 200) cache.put(req, fresh.clone());
      return fresh;
    } catch (err) {
      return cached || new Response("Offline", { status: 503 });
    }
  })());
});
