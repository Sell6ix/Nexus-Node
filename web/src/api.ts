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
  if (!headers.has('Content-Type') && init.body) {
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
  chat: (message: string) =>
    request<{ reply: string }>('/api/chat', { method: 'POST', body: JSON.stringify({ message }) }),
  confirm: (id: string, allowed: boolean) =>
    request<{ ok: boolean }>('/api/confirm', { method: 'POST', body: JSON.stringify({ id, allowed }) }),
  invite: (label = '') =>
    request<{ code: string; expires_at: number }>('/api/invites', { method: 'POST', body: JSON.stringify({ label }) }),
  tools: () => request<{ tools: string[] }>('/api/tools'),
}

export function wsUrl(token: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/api/ws?token=${encodeURIComponent(token)}`
}
