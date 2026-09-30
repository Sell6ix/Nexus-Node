/* Nexus Node service worker — offline shell, push, badge */
const CACHE = 'nexus-shell-v2'
const PRECACHE = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/favicon.svg',
  '/icons.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png',
]

self.addEventListener('install', (event) => {
  self.skipWaiting()
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE).catch(() => undefined)),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys()
      await Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      await self.clients.claim()
    })(),
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api') || url.pathname.startsWith('/ws')) return

  event.respondWith(
    (async () => {
      const cache = await caches.open(CACHE)
      try {
        const fresh = await fetch(req)
        if (fresh && fresh.ok) {
          const path = url.pathname
          if (
            path.startsWith('/assets/') ||
            PRECACHE.includes(path) ||
            path === '/' ||
            path.endsWith('.js') ||
            path.endsWith('.css') ||
            path.endsWith('.svg') ||
            path.endsWith('.png') ||
            path.endsWith('.webmanifest')
          ) {
            cache.put(req, fresh.clone())
          }
        }
        return fresh
      } catch {
        const cached = await cache.match(req)
        if (cached) return cached
        if (req.mode === 'navigate') {
          const shell = await cache.match('/index.html')
          if (shell) return shell
        }
        return Response.error()
      }
    })(),
  )
})

self.addEventListener('push', (event) => {
  let title = 'Nexus Node'
  let body = 'Reminder'
  try {
    const data = event.data ? event.data.json() : {}
    title = data.title || title
    body = data.body || body
  } catch {
    if (event.data) body = event.data.text()
  }
  event.waitUntil(
    (async () => {
      if (self.registration.setAppBadge) {
        try {
          await self.registration.setAppBadge(1)
        } catch {
          /* ignore */
        }
      }
      await self.registration.showNotification(title, {
        body,
        icon: '/icon-192.png',
        badge: '/icon-192.png',
        data: { url: '/?tab=chat' },
      })
    })(),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const target = (event.notification.data && event.notification.data.url) || '/'
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ('focus' in client) {
          client.postMessage({ type: 'clear_badge' })
          return client.focus()
        }
      }
      if (self.clients.openWindow) return self.clients.openWindow(target)
      return undefined
    }),
  )
})

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'clear_badge' && self.registration.clearAppBadge) {
    event.waitUntil(self.registration.clearAppBadge().catch(() => undefined))
  }
})
