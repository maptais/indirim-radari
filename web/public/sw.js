/* İndirim Radarı — iOS PWA Service Worker (Network-Only API + Network-First HTML + Web Push) */

const CACHE_NAME = "indirim-radari-v2";
const DEFAULT_ICON = "/icons/icon-192.png";
const DEFAULT_BADGE = "/icons/icon-192.png";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") {
    return;
  }

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) {
    return;
  }

  // Never intercept or cache the Service Worker script itself
  if (url.pathname === "/sw.js") {
    return;
  }

  // Always Network-Only for all /api/* endpoints (never cache dynamic data)
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(request, { cache: "no-store" })
    );
    return;
  }

  // Always Network-Only for Next.js App Router RSC payloads
  if (url.searchParams.has("_rsc") || request.headers.get("RSC") === "1") {
    event.respondWith(
      fetch(request, { cache: "no-store" })
    );
    return;
  }

  // Network-First for HTML & page navigations (fresh content online, offline fallback)
  const acceptHeader = request.headers.get("accept") || "";
  const isNavigation =
    request.mode === "navigate" || acceptHeader.includes("text/html");

  if (isNavigation) {
    event.respondWith(
      (async () => {
        try {
          const networkResponse = await fetch(request, { cache: "no-cache" });
          if (
            networkResponse &&
            networkResponse.ok &&
            networkResponse.type === "basic"
          ) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(request, networkResponse.clone()).catch(() => {});
          }
          return networkResponse;
        } catch {
          const cached = await caches.match(request);
          if (cached) {
            return cached;
          }
          const fallbackHome = await caches.match("/");
          if (fallbackHome) {
            return fallbackHome;
          }
          return new Response(
            "Bağlantı yok. İnternet bağlantınız geldiğinde sayfayı yenileyin.",
            {
              status: 503,
              headers: { "Content-Type": "text/plain; charset=utf-8" },
            }
          );
        }
      })()
    );
    return;
  }
});

self.addEventListener("push", (event) => {
  let payload = {
    title: "İndirim Radarı",
    body: "Yeni bir gerçek indirim fırsatı yakalandı!",
    url: "/",
    icon: DEFAULT_ICON,
    badge: DEFAULT_BADGE,
    tag: "indirim-radari-alert",
  };

  if (event.data) {
    try {
      const parsed = event.data.json();
      payload = {
        ...payload,
        ...parsed,
        url:
          (parsed && parsed.url) ||
          (parsed && parsed.data && parsed.data.url) ||
          "/",
      };
    } catch {
      const text = event.data.text();
      if (text) {
        payload.body = text;
      }
    }
  }

  const options = {
    body: payload.body,
    icon: payload.icon || DEFAULT_ICON,
    badge: payload.badge || DEFAULT_BADGE,
    tag: payload.tag || "indirim-radari-alert",
    renotify: true,
    vibrate: [150, 75, 150],
    data: {
      url: payload.url || "/",
      productId: payload.product_id || null,
      brand: payload.brand || null,
    },
  };

  if (payload.image) {
    options.image = payload.image;
  }

  event.waitUntil(self.registration.showNotification(payload.title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const targetUrl =
    (event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    (async () => {
      let resolvedUrl;
      try {
        resolvedUrl = new URL(targetUrl, self.location.origin);
      } catch {
        resolvedUrl = new URL("/", self.location.origin);
      }

      const isSameOrigin = resolvedUrl.origin === self.location.origin;

      if (isSameOrigin) {
        const windowClients = await self.clients.matchAll({
          type: "window",
          includeUncontrolled: true,
        });

        for (const client of windowClients) {
          if (client.url.startsWith(self.location.origin)) {
            if ("navigate" in client && client.url !== resolvedUrl.href) {
              await client.navigate(resolvedUrl.href);
            }
            if ("focus" in client) {
              return client.focus();
            }
          }
        }
      }

      if (self.clients.openWindow) {
        return self.clients.openWindow(resolvedUrl.href);
      }
      return undefined;
    })()
  );
});
