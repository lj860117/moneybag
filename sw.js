// 钱袋子 Service Worker — 离线缓存 + PWA 支持
const CACHE_NAME = 'moneybag-v970-cache';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/app.js',
  '/manifest.json',
];

// 安装：预缓存核心
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS).catch(()=>{}))
  );
  self.skipWaiting();
});

// 激活：清理旧缓存
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// 拦截请求
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API 请求绝对不缓存
  if (url.pathname.startsWith('/api/')) return;

  // index.html 强制走网络
  if (url.pathname === '/' || url.pathname === '/index.html') {
    event.respondWith(fetch(event.request, {cache: 'no-store'}).catch(() => caches.match(event.request)));
    return;
  }

  // 其他静态资源：网络优先 + 缓存兜底
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone)).catch(()=>{});
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
