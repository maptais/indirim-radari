/* İndirim Radarı — iOS PWA Web Push Service Worker */

const CACHE_VERSION = "indirim-radari-v1";
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
          .filter((key) => key !== CACHE_VERSION)
          .map((key) => caches.delete(key))
      );
      await self.clients.claim();
    })()
  );
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
