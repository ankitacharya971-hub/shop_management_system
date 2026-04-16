const CACHE_NAME = "shop-cache";

self.addEventListener("install", e => {
    e.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            return cache.addAll([
                "/",
                "/dashboard",
                "/stock",
                "/billing"
            ]);
        })
    );
});

self.addEventListener("fetch", e => {
    e.respondWith(
        caches.match(e.request).then(res => {
            return res || fetch(e.request);
        })
    );
});