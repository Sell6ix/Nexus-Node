export type User = {
  id: string
  username: string
  display_name: string
  role: 'owner' | 'member'
  has_api_key: boolean
  api_key: string | null
  api_base_url: string | null
  model: string | null
  active_device_id: string | null
  home_device_id: string | null
}

export type Device = {
  id: string
  name: string
  mode: 'local' | 'remote'
  enabled: boolean
}

const TOKEN_KEY = 'nexus_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {})
  const isForm = typeof FormData !== 'undefined' && init.body instanceof FormData
  if (!headers.has('Content-Type') && init.body && !isForm) {
    headers.set('Content-Type', 'application/json')
  }
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(path, { ...init, headers })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = (data as { detail?: string }).detail || res.statusText
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return data as T
}

export const api = {
  health: () => request<{ ok: boolean; has_owner: boolean; open_signup: boolean; device: string }>('/api/health'),
  register: (body: { username: string; password: string; display_name?: string; invite_code?: string }) =>
    request<{ token: string; user: User }>('/api/auth/register', { method: 'POST', body: JSON.stringify(body) }),
  login: (body: { username: string; password: string }) =>
    request<{ token: string; user: User }>('/api/auth/login', { method: 'POST', body: JSON.stringify(body) }),
  me: () => request<{ user: User }>('/api/me'),
  patchMe: (body: Record<string, unknown>) =>
    request<{ user: User }>('/api/me', { method: 'PATCH', body: JSON.stringify(body) }),
  devices: () => request<{ active: string; home: string | null; devices: Device[] }>('/api/devices'),
  addDevice: (body: { id: string; name: string; base_url: string; token: string }) =>
    request<{ ok: boolean }>('/api/devices', { method: 'POST', body: JSON.stringify(body) }),
  deleteDevice: (id: string) => request<{ ok: boolean }>(`/api/devices/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  getChat: () =>
    request<{ messages: { role: 'user' | 'assistant' | 'system'; text: string; ts?: number }[] }>(
      '/api/chat',
    ),
  clearChat: () => request<{ ok: boolean }>('/api/chat', { method: 'DELETE' }),
  chat: (message: string, attachmentIds: string[] = []) =>
    request<{ reply: string }>('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ message, attachment_ids: attachmentIds }),
    }),
  upload: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<{
      file: { id: string; filename: string; content_type: string; bytes: number }
    }>('/api/uploads', { method: 'POST', body: fd })
  },
  googleStatus: () =>
    request<{ configured: boolean; connected: boolean; email: string | null; redirect_uri: string }>(
      '/api/google/status',
    ),
  googleConnect: () => request<{ url: string }>('/api/google/connect'),
  googleDisconnect: () => request<{ ok: boolean }>('/api/google/disconnect', { method: 'DELETE' }),
  downloadBackup: async () => {
    const token = getToken()
    const res = await fetch('/api/backup', {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error((data as { detail?: string }).detail || res.statusText)
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `nexus-backup-${new Date().toISOString().slice(0, 10)}.zip`
    a.click()
    URL.revokeObjectURL(url)
  },
  confirm: (id: string, allowed: boolean) =>
    request<{ ok: boolean }>('/api/confirm', { method: 'POST', body: JSON.stringify({ id, allowed }) }),
  invite: (label = '') =>
    request<{ code: string; expires_at: number }>('/api/invites', { method: 'POST', body: JSON.stringify({ label }) }),
  tools: () => request<{ tools: string[] }>('/api/tools'),
  assistant: () =>
    request<{
      web_search: { ready: boolean; preferred: string; providers: Record<string, boolean> }
      fetch_url: { ready: boolean }
      email: { ready: boolean; from: string | null }
      telegram_messaging: { ready: boolean }
      contacts: { ready: boolean }
    }>('/api/assistant'),
  contacts: () =>
    request<{
      contacts: {
        id: string
        name: string
        email?: string | null
        telegram?: string | null
        notes?: string | null
      }[]
    }>('/api/contacts'),
  upsertContact: (body: {
    name: string
    email?: string
    telegram?: string
    notes?: string
    id?: string
  }) =>
    request<{ contact: { id: string; name: string } }>('/api/contacts', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  deleteContact: (id: string) =>
    request<{ ok: boolean }>(`/api/contacts/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  pushVapid: () => request<{ publicKey: string }>('/api/push/vapid'),
  pushSubscribe: (body: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
    request<{ ok: boolean }>('/api/push/subscribe', { method: 'POST', body: JSON.stringify(body) }),
  transcribe: (blob: Blob, lang = '') => {
    const fd = new FormData()
    const ext = blob.type.includes('mp4') ? 'mp4' : blob.type.includes('ogg') ? 'ogg' : 'webm'
    fd.append('file', blob, `voice.${ext}`)
    if (lang) fd.append('language', lang)
    return request<{ text: string }>('/api/voice/transcribe', { method: 'POST', body: fd })
  },
}

export function wsUrl(token: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/ws?token=${encodeURIComponent(token)}`
}
