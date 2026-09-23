/* F-20260923-06 PWA Service Worker（RV-49）。
 * 策略：cache 名带版本号；index.html network-first（失败回落缓存，防新旧混杂）；
 * 静态资源/图标 cache-first；导航离线 fallback 到缓存的 index.html。
 * 仅在 secure context（HTTPS/localhost）生效；HTTP 局域网环境注册失败自动降级，不阻塞主流程。
 */
var CACHE_NAME = "invoice-precheck-v1";
var APP_SHELL = [
  "./",
  "./index.html",
  "./manifest.json",
  "./assets/icon-192.png",
  "./assets/icon-512.png"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function (cache) {
      return cache.addAll(APP_SHELL);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(
        names.filter(function (n) { return n !== CACHE_NAME; })
          .map(function (n) { return caches.delete(n); })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== location.origin) return;  // 后端 API 不经 SW（避免缓存发票数据）

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).then(function (resp) {
        var copy = resp.clone();
        caches.open(CACHE_NAME).then(function (c) { c.put(req, copy); });
        return resp;
      }).catch(function () {
        return caches.match("./index.html");
      })
    );
    return;
  }
  // 静态资源：cache-first，失败回退网络
  event.respondWith(
    caches.match(req).then(function (hit) {
      if (hit) return hit;
      return fetch(req).then(function (resp) {
        var copy = resp.clone();
        caches.open(CACHE_NAME).then(function (c) { c.put(req, copy); });
        return resp;
      });
    })
  );
});
