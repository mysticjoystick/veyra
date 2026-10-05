/* Veyra service worker: installable shell + notification taps.
 *
 * Caching is deliberately conservative:
 *  - App shell (/, icons, manifest) is cached on install for instant startup.
 *  - /api/* and /ws/* are NEVER cached here (freshness matters for trading).
 *  - push events render a notification so a future VAPID sender can reach
 *    installed users even with the tab closed.
 */
const SHELL_CACHE = "veyra-shell-v1";
const SHELL_ASSETS = ["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS)).then(() => self.skipWaiting()).catch(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;
  // Never intercept trading data or sockets.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws/")) return;
  // Shell + icons: stale-while-revalidate for instant paint on mobile.
  event.respondWith(
    caches.match(event.request).then((hit) => {
      const net = fetch(event.request).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(event.request, copy)).catch(() => {});
        }
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});

// Future server push (VAPID) lands here. Payload: {title, body, url}.
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data ? event.data.text() : "" }; }
  const title = data.title || "Veyra alert";
  const body = data.body || "A setup changed state.";
  const url = data.url || "/";
  event.waitUntil(self.registration.showNotification(title, { body, icon: "/icons/icon-192.png", badge: "/icons/icon-192.png", data: { url } }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if (w.url === new URL(url, self.location.origin).href || w.url.startsWith(self.location.origin)) {
          return w.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
