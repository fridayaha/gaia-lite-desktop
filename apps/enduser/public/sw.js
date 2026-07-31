// UnionAgents 用户门户 Service Worker。
// 关键：SPA 的 chunk 文件名带 hash（不可变），但 index.html 每次发版会引用新 hash。
// 故导航请求（index.html）必须 network-first + 强制向源站再校验（cache:'no-cache'），
// 否则浏览器 HTTP 启发式缓存旧 index.html → 引用已不存在的旧 chunk hash →
// "Failed to fetch dynamically imported module" 404（换 noVNC 库后 chunk 图大变触发）。
// /assets/ 下 chunk 是内容寻址的，可 cache-first 长缓存。
const CACHE_NAME = 'unionagents-v2';  // bump: 清掉 v1 里缓存的旧 index.html

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  if (event.request.url.includes('/api/')) return;  // 不缓存 API

  const url = new URL(event.request.url);

  // 导航请求（index.html）：network-first，强制再校验，避免命中旧 HTML。
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request, { cache: 'no-cache' })
        .then((response) => {
          if (response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
          }
          return response;
        })
        .catch(() => caches.match(event.request).then((r) => r || caches.match('./')))
    );
    return;
  }

  // /assets/ 下的 chunk（hash 寻址，不可变）：cache-first。
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(event.request).then((cached) =>
        cached || fetch(event.request).then((response) => {
          if (response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
          }
          return response;
        })
      )
    );
    return;
  }

  // 其它静态资源：network-first，回退缓存。
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
