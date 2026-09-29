import { api } from './api'

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padding = '='.repeat((4 - (base64.length % 4)) % 4)
  const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'))
  const out = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i)
  return out
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) return null
  try {
    return await navigator.serviceWorker.register('/sw.js', { scope: '/' })
  } catch (err) {
    console.warn('service worker register failed', err)
    return null
  }
}

export async function enablePush(): Promise<string> {
  if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) {
    throw new Error('This browser cannot receive closed-app alerts.')
  }
  const perm = await Notification.requestPermission()
  if (perm !== 'granted') {
    throw new Error('Notifications blocked. Allow them in the browser site settings.')
  }
  const reg = await registerServiceWorker()
  if (!reg) throw new Error('Could not register the service worker.')
  await navigator.serviceWorker.ready
  const { publicKey } = await api.pushVapid()
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource,
  })
  const json = sub.toJSON()
  if (!json.endpoint || !json.keys?.p256dh || !json.keys?.auth) {
    throw new Error('Push subscription incomplete.')
  }
  await api.pushSubscribe({
    endpoint: json.endpoint,
    keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
  })
  const secure = location.protocol === 'https:' || location.hostname === 'localhost'
  if (!secure) {
    return 'Alerts allowed while the app is open. Closed-app push needs HTTPS (Tailscale Serve or localhost).'
  }
  return 'Reminders will notify even if the app is closed (hub must stay online).'
}

export function showLocalNotification(title: string, body: string) {
  if (!('Notification' in window) || Notification.permission !== 'granted') return
  try {
    new Notification(title, { body, icon: '/favicon.svg' })
  } catch {
    /* ignore — some browsers only allow SW notifications */
  }
}
