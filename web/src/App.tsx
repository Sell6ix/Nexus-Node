import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api, getToken, setToken, wsUrl } from './api'
import type { User, Device } from './api'
import { enablePush, registerServiceWorker, showLocalNotification } from './push'
import {
  loadVoicePrefs,
  saveVoicePrefs,
  speechRecognitionSupported,
  speechSynthesisSupported,
  startListening,
  speakText,
  stopSpeaking,
  recordAudioBlob,
  stopActiveRecording,
  type ListenHandle,
  type VoicePrefs,
} from './voice'

type Msg = { role: 'user' | 'assistant' | 'system'; text: string }
type ConfirmReq = { id: string; prompt: string; timeout: number }
type Tab = 'chat' | 'devices' | 'settings'

export default function App() {
  const [boot, setBoot] = useState<'loading' | 'auth' | 'app'>('loading')
  const [health, setHealth] = useState<{ has_owner: boolean; open_signup: boolean; device: string } | null>(null)
  const [user, setUser] = useState<User | null>(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<Tab>('chat')
  const welcome: Msg = {
    role: 'system',
    text: 'Ask anything — or tap the mic and talk. Notes, search, calendar, diary…',
  }
  const [messages, setMessages] = useState<Msg[]>([welcome])
  const [chatReady, setChatReady] = useState(false)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [authBusy, setAuthBusy] = useState(false)
  const [confirm, setConfirm] = useState<ConfirmReq | null>(null)
  const [toast, setToast] = useState('')
  const [voicePrefs, setVoicePrefs] = useState<VoicePrefs>(() => loadVoicePrefs())
  const [listening, setListening] = useState(false)
  const [recordingFallback, setRecordingFallback] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<{ id: string; filename: string }[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const listenRef = useRef<ListenHandle | null>(null)
  const busyRef = useRef(false)

  async function loadChat() {
    try {
      const res = await api.getChat()
      const rows = (res.messages || []).map((m) => ({
        role: m.role,
        text: m.text,
      }))
      setMessages(rows.length ? rows : [welcome])
    } catch {
      setMessages([welcome])
    } finally {
      setChatReady(true)
    }
  }

  useEffect(() => {
    ;(async () => {
      try {
        const h = await api.health()
        setHealth(h)
        const token = getToken()
        if (token) {
          const me = await api.me()
          setUser(me.user)
          setBoot('app')
          await loadChat()
        } else {
          setBoot('auth')
        }
      } catch {
        setBoot('auth')
      }
    })()
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  useEffect(() => {
    busyRef.current = busy
  }, [busy])

  useEffect(() => {
    saveVoicePrefs(voicePrefs)
  }, [voicePrefs])

  useEffect(() => {
    if (boot !== 'app') return
    void registerServiceWorker()
    // Warm voices list (Chrome loads async)
    if (speechSynthesisSupported()) {
      window.speechSynthesis.getVoices()
      window.speechSynthesis.onvoiceschanged = () => {
        window.speechSynthesis.getVoices()
      }
    }
    return () => {
      listenRef.current?.stop()
      stopSpeaking()
      stopActiveRecording()
    }
  }, [boot])

  useEffect(() => {
    if (boot !== 'app') return
    const token = getToken()
    if (!token) return
    let alive = true
    let ws: WebSocket | null = null
    let timer: number | undefined

    const connect = () => {
      ws = new WebSocket(wsUrl(token))
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data)
          if (data.type === 'confirm') {
            setConfirm({ id: data.id, prompt: data.prompt, timeout: data.timeout || 120 })
          }
          if (data.type === 'reminder') {
            setToast(data.text || 'Reminder')
            setMessages((m) => [...m, { role: 'system', text: data.text }])
            showLocalNotification('Nexus Node', data.text)
            const prefs = loadVoicePrefs()
            if (prefs.replies && data.text) speakText(String(data.text), prefs.lang)
          }
        } catch {
          /* ignore */
        }
      }
      ws.onclose = () => {
        if (alive) timer = window.setTimeout(connect, 2000)
      }
    }
    connect()
    return () => {
      alive = false
      if (timer) window.clearTimeout(timer)
      ws?.close()
    }
  }, [boot, user?.id])

  async function onAuth(
    mode: 'login' | 'register',
    fields: { username: string; password: string; display_name?: string; invite_code?: string },
  ) {
    setError('')
    if (!fields.username.trim() || !fields.password) {
      setError('Enter username and password.')
      return
    }
    setAuthBusy(true)
    try {
      const res = mode === 'login' ? await api.login(fields) : await api.register(fields)
      setToken(res.token)
      setUser(res.user)
      setBoot('app')
      setChatReady(false)
      await loadChat()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Auth failed')
    } finally {
      setAuthBusy(false)
    }
  }

  async function clearChat() {
    if (busy) return
    try {
      await api.clearChat()
      setMessages([welcome])
      setToast('Chat cleared')
    } catch (e) {
      setToast(e instanceof Error ? e.message : 'Could not clear chat')
    }
  }

  async function sendMessage(textRaw: string) {
    const text = textRaw.trim()
    if ((!text && pendingFiles.length === 0) || busyRef.current) return
    const ids = pendingFiles.map((f) => f.id)
    const label =
      text + (ids.length ? `${text ? ' ' : ''}(+${ids.length} file${ids.length > 1 ? 's' : ''})` : '')
    setInput('')
    setPendingFiles([])
    setMessages((m) => [...m, { role: 'user', text: label || '(file)' }])
    setBusy(true)
    try {
      const res = await api.chat(text || 'Please look at the attached file(s).', ids)
      const reply = res.reply || '(empty)'
      setMessages((m) => [...m, { role: 'assistant', text: reply }])
      if (voicePrefs.replies) {
        speakText(reply, voicePrefs.lang)
      }
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: 'system', text: err instanceof Error ? err.message : 'Chat failed' },
      ])
    } finally {
      setBusy(false)
    }
  }

  async function onPickFile(fileList: FileList | null) {
    const file = fileList?.[0]
    if (!file) return
    try {
      const res = await api.upload(file)
      setPendingFiles((prev) => [...prev, { id: res.file.id, filename: res.file.filename }])
      setToast(`Attached ${res.file.filename}`)
    } catch (e) {
      setToast(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  async function sendChat(e: FormEvent) {
    e.preventDefault()
    await sendMessage(input)
  }

  function stopMic() {
    listenRef.current?.stop()
    listenRef.current = null
    stopActiveRecording()
    setListening(false)
    setRecordingFallback(false)
  }

  async function toggleMic() {
    if (listening || recordingFallback) {
      stopMic()
      return
    }
    if (busy) return
    stopSpeaking()
    setToast('')

    if (speechRecognitionSupported()) {
      setListening(true)
      const handle = startListening({
        lang: voicePrefs.lang,
        onInterim: (t) => setInput(t),
        onFinal: (t) => {
          listenRef.current = null
          setListening(false)
          setInput(t)
          if (voicePrefs.autoSend) void sendMessage(t)
        },
        onError: (msg) => {
          listenRef.current = null
          setListening(false)
          setToast(msg)
        },
        onEnd: () => {
          listenRef.current = null
          setListening(false)
        },
      })
      if (!handle) {
        setListening(false)
        setToast('Could not start voice recognition')
        return
      }
      listenRef.current = handle
      return
    }

    // Whisper fallback: tap once to start, tap again to stop & transcribe
    if (!user?.has_api_key) {
      setToast('Voice needs Chrome/Edge speech, or an AI key for Whisper fallback.')
      return
    }
    setRecordingFallback(true)
    setToast('Listening… tap mic again when done')
    try {
      const blob = await recordAudioBlob(30000)
      setRecordingFallback(false)
      if (blob.size < 1000) {
        setToast('Recording too short')
        return
      }
      setToast('Transcribing…')
      const lang = voicePrefs.lang.split('-')[0] || ''
      const res = await api.transcribe(blob, lang)
      setToast('')
      setInput(res.text)
      if (voicePrefs.autoSend) void sendMessage(res.text)
    } catch (e) {
      setRecordingFallback(false)
      setToast(e instanceof Error ? e.message : 'Voice recording failed')
    }
  }

  async function answerConfirm(allowed: boolean) {
    if (!confirm) return
    const id = confirm.id
    setConfirm(null)
    try {
      await api.confirm(id, allowed)
    } catch {
      setToast('Confirmation expired')
    }
  }

  function logout() {
    setToken(null)
    setUser(null)
    setBoot('auth')
    setChatReady(false)
    setMessages([{ role: 'system', text: 'Signed out.' }])
  }

  if (boot === 'loading') {
    return (
      <div className="min-h-dvh grid place-items-center text-[var(--muted)]">
        Starting Nexus Node…
      </div>
    )
  }

  if (boot === 'auth' || !user) {
    return (
      <AuthScreen
        health={health}
        error={error}
        busy={authBusy}
        onAuth={onAuth}
      />
    )
  }

  return (
    <div className="min-h-dvh flex flex-col max-w-3xl mx-auto px-3 sm:px-4 pb-[env(safe-area-inset-bottom)]">
      <header className="pt-4 pb-3 flex items-center justify-between gap-3">
        <div>
          <div className="brand text-xl sm:text-2xl font-extrabold tracking-tight">
            Nexus <span className="text-[var(--accent)]">Node</span>
          </div>
          <div className="text-xs text-[var(--muted)]">
            {user.display_name} · {user.role}
            {health?.device ? ` · hub ${health.device}` : ''}
          </div>
        </div>
        <button
          type="button"
          onClick={logout}
          className="text-sm text-[var(--muted)] hover:text-[var(--text)] px-2 py-1"
        >
          Sign out
        </button>
      </header>

      <nav className="flex gap-1 p-1 rounded-2xl bg-[var(--panel)] border border-[var(--line)] mb-3">
        {([
          ['chat', 'Chat'],
          ['devices', 'Devices'],
          ['settings', 'Settings'],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`flex-1 rounded-xl py-2 text-sm font-medium transition ${
              tab === id
                ? 'bg-[var(--accent)] text-[var(--accent-ink)]'
                : 'text-[var(--muted)] hover:text-[var(--text)]'
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      {!user.has_api_key && (
        <button
          type="button"
          onClick={() => setTab('settings')}
          className="mb-3 text-left rounded-2xl border border-[var(--warn)]/40 bg-[var(--panel)] px-4 py-3"
        >
          <div className="text-sm font-medium">Finish setup</div>
          <p className="text-xs text-[var(--muted)] mt-1">
            Add your AI API key in Settings — each person uses their own. Then
            Enable notifications if you want closed-app reminders.
          </p>
        </button>
      )}

      {tab === 'chat' && (
        <section className="flex-1 flex flex-col min-h-0">
          <div className="flex flex-wrap items-center justify-between gap-2 pb-2">
            <p className="text-xs text-[var(--muted)]">
              {listening || recordingFallback
                ? 'Listening… tap mic to stop'
                : chatReady
                  ? 'Saved on this hub — survives reload.'
                  : 'Loading chat…'}
            </p>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() =>
                  setVoicePrefs((p) => ({ ...p, replies: !p.replies }))
                }
                className={`text-xs px-2 py-1 rounded-lg border ${
                  voicePrefs.replies
                    ? 'border-[var(--accent)] text-[var(--accent)]'
                    : 'border-[var(--line)] text-[var(--muted)]'
                }`}
                title="Speak assistant replies aloud"
              >
                {voicePrefs.replies ? 'Voice replies on' : 'Voice replies'}
              </button>
              <button
                type="button"
                onClick={() => void clearChat()}
                disabled={busy || !chatReady}
                className="text-xs text-[var(--muted)] hover:text-[var(--text)] disabled:opacity-40 px-2 py-1"
              >
                Clear
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto space-y-3 pb-3">
            {messages.map((m, i) => (
              <Bubble key={i} msg={m} />
            ))}
            {busy && <div className="text-sm text-[var(--muted)] px-1">Working…</div>}
            <div ref={bottomRef} />
          </div>
          <form onSubmit={sendChat} className="sticky bottom-0 pt-2 pb-3 bg-gradient-to-t from-[var(--ink)] via-[var(--ink)] to-transparent">
            <div className="flex gap-2 items-end rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-2">
              <button
                type="button"
                onClick={() => void toggleMic()}
                disabled={busy && !listening && !recordingFallback}
                aria-pressed={listening || recordingFallback}
                aria-label={listening || recordingFallback ? 'Stop listening' : 'Talk'}
                className={`shrink-0 min-w-14 h-12 px-3 rounded-2xl font-semibold text-sm transition disabled:opacity-40 ${
                  listening || recordingFallback
                    ? 'bg-[var(--danger)] text-white animate-pulse'
                    : 'bg-[var(--panel-2)] border border-[var(--line)] text-[var(--text)] hover:border-[var(--accent)]/50'
                }`}
              >
                {listening || recordingFallback ? 'Stop' : 'Mic'}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                onChange={(e) => void onPickFile(e.target.files)}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={busy}
                className="shrink-0 min-w-14 h-12 px-3 rounded-2xl font-semibold text-sm bg-[var(--panel-2)] border border-[var(--line)] disabled:opacity-40"
              >
                File
              </button>
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                rows={2}
                placeholder={
                  listening || recordingFallback ? 'Listening…' : 'Message, mic, or attach a file…'
                }
                className="flex-1 resize-none bg-transparent outline-none px-2 py-1 placeholder:text-[var(--muted)] min-h-[3rem]"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void sendChat(e)
                  }
                }}
              />
              <button
                type="submit"
                disabled={busy || (!input.trim() && pendingFiles.length === 0)}
                className="shrink-0 rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] font-semibold px-4 py-2 disabled:opacity-40 self-end"
              >
                Send
              </button>
            </div>
            {pendingFiles.length > 0 && (
              <div className="flex flex-wrap gap-2 mt-2 px-1">
                {pendingFiles.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    className="text-xs rounded-lg border border-[var(--line)] px-2 py-1 text-[var(--muted)]"
                    onClick={() => setPendingFiles((prev) => prev.filter((x) => x.id !== f.id))}
                    title="Remove attachment"
                  >
                    {f.filename} ×
                  </button>
                ))}
              </div>
            )}
            <div className="flex flex-wrap gap-2 mt-2 px-1">
              <label className="text-xs text-[var(--muted)] flex items-center gap-1.5">
                <input
                  type="checkbox"
                  checked={voicePrefs.autoSend}
                  onChange={(e) =>
                    setVoicePrefs((p) => ({ ...p, autoSend: e.target.checked }))
                  }
                />
                Auto-send after speech
              </label>
              <select
                value={voicePrefs.lang}
                onChange={(e) => setVoicePrefs((p) => ({ ...p, lang: e.target.value }))}
                className="text-xs bg-[var(--panel)] border border-[var(--line)] rounded-lg px-2 py-1 text-[var(--muted)]"
                aria-label="Voice language"
              >
                <option value="ru-RU">Русский</option>
                <option value="en-US">English</option>
                <option value="uk-UA">Українська</option>
                <option value="de-DE">Deutsch</option>
              </select>
              {!speechRecognitionSupported() && (
                <span className="text-xs text-[var(--muted)]">
                  Browser STT unavailable — Whisper fallback if AI key set
                </span>
              )}
            </div>
          </form>
        </section>
      )}

      {tab === 'devices' && <DevicesPanel user={user} onUser={setUser} onToast={setToast} />}
      {tab === 'settings' && <SettingsPanel user={user} onUser={setUser} onToast={setToast} />}

      {confirm && (
        <div className="fixed inset-0 z-50 bg-black/65 backdrop-blur-sm grid place-items-end sm:place-items-center p-4">
          <div className="w-full max-w-md rounded-3xl bg-[var(--panel-2)] border border-[var(--line)] p-5 shadow-2xl">
            <div className="text-xs uppercase tracking-wider text-[var(--warn)] mb-2">Confirmation required</div>
            <pre className="whitespace-pre-wrap text-sm leading-relaxed mb-4 font-[inherit]">{confirm.prompt}</pre>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void answerConfirm(false)}
                className="flex-1 rounded-xl border border-[var(--line)] py-3 font-medium"
              >
                Deny
              </button>
              <button
                type="button"
                onClick={() => void answerConfirm(true)}
                className="flex-1 rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] py-3 font-semibold"
              >
                Allow
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && (
        <button
          type="button"
          className="fixed left-4 right-4 bottom-6 sm:left-auto sm:right-6 sm:w-96 z-40 rounded-2xl bg-[var(--panel-2)] border border-[var(--accent)]/40 px-4 py-3 text-left text-sm shadow-xl"
          onClick={() => setToast('')}
        >
          {toast}
        </button>
      )}
    </div>
  )
}

function Bubble({ msg }: { msg: Msg }) {
  const align =
    msg.role === 'user' ? 'ml-8 bg-[var(--accent)] text-[var(--accent-ink)]' :
    msg.role === 'assistant' ? 'mr-8 bg-[var(--panel)] border border-[var(--line)]' :
    'mx-2 text-[var(--muted)] text-sm bg-transparent border-0 px-1'
  return (
    <div className={`rounded-2xl px-3.5 py-2.5 whitespace-pre-wrap leading-relaxed ${align}`}>
      {msg.text}
    </div>
  )
}

function AuthScreen({
  health,
  error,
  busy,
  onAuth,
}: {
  health: { has_owner: boolean; open_signup: boolean; device: string } | null
  error: string
  busy: boolean
  onAuth: (
    mode: 'login' | 'register',
    fields: { username: string; password: string; display_name?: string; invite_code?: string },
  ) => Promise<void>
}) {
  const firstOwner = health ? !health.has_owner : true
  const [mode, setMode] = useState<'login' | 'register'>(firstOwner ? 'register' : 'login')
  const formRef = useRef<HTMLFormElement>(null)

  useEffect(() => {
    if (firstOwner) setMode('register')
  }, [firstOwner])

  function readFields(): {
    username: string
    password: string
    display_name?: string
    invite_code?: string
  } {
    // Prefer FormData so browser autofill (which may skip React onChange) still works.
    const fd = formRef.current ? new FormData(formRef.current) : null
    const username = String(fd?.get('username') || '').trim()
    const password = String(fd?.get('password') || '')
    const display_name = String(fd?.get('display_name') || '').trim()
    const invite_code = String(fd?.get('invite_code') || '').trim()
    return {
      username,
      password,
      display_name: display_name || undefined,
      invite_code: invite_code || undefined,
    }
  }

  return (
    <div className="min-h-dvh flex flex-col justify-center px-4 py-10">
      <div className="max-w-md w-full mx-auto">
        <div className="mb-8">
          <div className="brand text-5xl sm:text-6xl font-extrabold leading-none mb-3">
            Nexus<br />
            <span className="text-[var(--accent)]">Node</span>
          </div>
          <p className="text-[var(--muted)] text-base max-w-sm">
            Your devices. Your keys. Chat from phone or PC — no Telegram.
          </p>
        </div>

        <form
          ref={formRef}
          className="rounded-3xl border border-[var(--line)] bg-[var(--panel)]/90 p-5 space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            void onAuth(mode, readFields())
          }}
        >
          <div className="flex gap-2 mb-2">
            <button
              type="button"
              onClick={() => setMode('login')}
              className={`flex-1 py-2 rounded-xl text-sm font-medium ${mode === 'login' ? 'bg-[var(--panel-2)]' : 'text-[var(--muted)]'}`}
            >
              Sign in
            </button>
            <button
              type="button"
              onClick={() => setMode('register')}
              className={`flex-1 py-2 rounded-xl text-sm font-medium ${mode === 'register' ? 'bg-[var(--panel-2)]' : 'text-[var(--muted)]'}`}
            >
              {firstOwner ? 'Create owner' : 'Join'}
            </button>
          </div>

          {mode === 'register' && (
            <Field name="display_name" label="Display name" placeholder="Display name" autoComplete="nickname" />
          )}
          <Field name="username" label="Username" placeholder="Username" autoComplete="username" required />
          <Field
            name="password"
            label="Password"
            type="password"
            placeholder="••••••••"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            required
          />
          {mode === 'register' && !firstOwner && !(health?.open_signup) && (
            <Field name="invite_code" label="Invite code" placeholder="from the owner" autoComplete="off" />
          )}
          {error && (
            <div className="text-sm text-[var(--danger)]" role="alert">
              {error}
            </div>
          )}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] font-semibold py-3 mt-1 disabled:opacity-50"
          >
            {busy
              ? 'Please wait…'
              : mode === 'login'
                ? 'Enter'
                : firstOwner
                  ? 'Create workspace'
                  : 'Join workspace'}
          </button>
          {firstOwner && (
            <p className="text-xs text-[var(--muted)]">
              First account becomes the owner of this hub ({health?.device || 'this PC'}).
              Invite friends from Settings.
            </p>
          )}
        </form>
      </div>
    </div>
  )
}

function Field({
  name,
  label,
  type = 'text',
  placeholder,
  autoComplete,
  required,
}: {
  name: string
  label: string
  type?: string
  placeholder?: string
  autoComplete?: string
  required?: boolean
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs text-[var(--muted)]">{label}</span>
      <input
        name={name}
        type={type}
        autoComplete={autoComplete}
        placeholder={placeholder}
        required={required}
        className="w-full rounded-xl bg-[var(--ink)]/60 border border-[var(--line)] px-3 py-2.5 outline-none focus:border-[var(--accent)]/50"
      />
    </label>
  )
}

function DevicesPanel({
  user,
  onUser,
  onToast,
}: {
  user: User
  onUser: (u: User) => void
  onToast: (t: string) => void
}) {
  const [devices, setDevices] = useState<Device[]>([])
  const [active, setActive] = useState('')
  const [home, setHome] = useState<string | null>(null)
  const [form, setForm] = useState({ id: '', name: '', base_url: '', token: '' })

  async function reload() {
    const d = await api.devices()
    setDevices(d.devices)
    setActive(d.active)
    setHome(d.home)
  }

  useEffect(() => {
    void reload().catch((e) => onToast(e.message))
  }, [])

  async function setActiveDevice(id: string) {
    const res = await api.patchMe({ active_device_id: id })
    onUser(res.user)
    await reload()
  }

  async function setHomeDevice(id: string) {
    const res = await api.patchMe({ home_device_id: id })
    onUser(res.user)
    onToast(`Home device → ${id}`)
    await reload()
  }

  return (
    <div className="space-y-4 pb-8">
      <p className="text-sm text-[var(--muted)]">
        Active: <span className="text-[var(--text)]">{active}</span>
        {home ? ` · Home: ${home}` : ''}
      </p>
      <ul className="space-y-2">
        {devices.map((d) => (
          <li
            key={d.id}
            className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] px-4 py-3 flex flex-wrap items-center gap-2 justify-between"
          >
            <div>
              <div className="font-medium">{d.name}</div>
              <div className="text-xs text-[var(--muted)]">{d.id} · {d.mode}</div>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void setActiveDevice(d.id)}
                className="text-xs rounded-lg px-3 py-1.5 border border-[var(--line)]"
              >
                Use
              </button>
              <button
                type="button"
                onClick={() => void setHomeDevice(d.id)}
                className="text-xs rounded-lg px-3 py-1.5 bg-[var(--accent)] text-[var(--accent-ink)] font-medium"
              >
                Home
              </button>
            </div>
          </li>
        ))}
      </ul>

      <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 space-y-2">
        <div className="font-medium">Add remote PC (worker)</div>
        <p className="text-xs text-[var(--muted)]">
          On the other machine: <code className="text-[var(--accent)]">python main.py --mode worker</code>
        </p>
        {(['id', 'name', 'base_url', 'token'] as const).map((k) => (
          <input
            key={k}
            value={form[k]}
            placeholder={k === 'base_url' ? 'http://192.168.1.20:8765' : k}
            onChange={(e) => setForm({ ...form, [k]: e.target.value })}
            className="w-full rounded-xl bg-[var(--ink)]/60 border border-[var(--line)] px-3 py-2 text-sm"
          />
        ))}
        <button
          type="button"
          className="rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] font-semibold px-4 py-2"
          onClick={() => {
            void api
              .addDevice(form)
              .then(() => {
                setForm({ id: '', name: '', base_url: '', token: '' })
                return reload()
              })
              .catch((e) => onToast(e.message))
          }}
        >
          Save device
        </button>
      </div>
      <p className="text-xs text-[var(--muted)]">Signed in as {user.username}</p>
    </div>
  )
}

function SettingsPanel({
  user,
  onUser,
  onToast,
}: {
  user: User
  onUser: (u: User) => void
  onToast: (t: string) => void
}) {
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState(user.model || '')
  const [baseUrl, setBaseUrl] = useState(user.api_base_url || '')
  const [invite, setInvite] = useState<string | null>(null)
  const [tools, setTools] = useState<string[]>([])
  const [pushNote, setPushNote] = useState('')
  const [assistantNote, setAssistantNote] = useState('')
  const [googleNote, setGoogleNote] = useState('Checking Google…')
  const [contacts, setContacts] = useState<
    { id: string; name: string; email?: string | null; telegram?: string | null }[]
  >([])
  const [contactForm, setContactForm] = useState({ name: '', email: '', telegram: '' })

  async function reloadContacts() {
    const res = await api.contacts()
    setContacts(res.contacts || [])
  }

  async function reloadGoogle() {
    const g = await api.googleStatus()
    if (!g.configured) {
      setGoogleNote('Hub needs GOOGLE_CLIENT_ID / SECRET in .env')
    } else if (g.connected) {
      setGoogleNote(`Connected as ${g.email || 'Google account'}`)
    } else {
      setGoogleNote('Not connected')
    }
  }

  useEffect(() => {
    void api.tools().then((t) => setTools(t.tools)).catch(() => undefined)
    void api
      .assistant()
      .then((a) => {
        const bits = [
          `Search: ${a.web_search.preferred}`,
          a.email.ready ? `Email: ${a.email.from}` : 'Email: not configured',
          a.telegram_messaging.ready ? 'Telegram bot: ready' : 'Telegram bot: not set',
        ]
        setAssistantNote(bits.join(' · '))
      })
      .catch(() => undefined)
    void reloadContacts().catch(() => undefined)
    void reloadGoogle().catch(() => setGoogleNote('Google status unavailable'))
    // OAuth return
    const q = new URLSearchParams(location.search)
    if (q.get('google') === 'connected') {
      onToast('Google connected')
      history.replaceState({}, '', location.pathname)
      void reloadGoogle()
    } else if (q.get('google') === 'error') {
      onToast('Google connect failed')
      history.replaceState({}, '', location.pathname)
    }
  }, [])

  const toolPreview = useMemo(() => tools.slice(0, 12).join(', ') + (tools.length > 12 ? '…' : ''), [tools])

  return (
    <div className="space-y-4 pb-8">
      <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 space-y-3">
        <div className="font-medium">Your AI key (BYOK)</div>
        <p className="text-xs text-[var(--muted)]">
          Stored on this hub for your account only. Current: {user.has_api_key ? user.api_key : 'not set'}
        </p>
        <input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="sk-…"
          className="w-full rounded-xl bg-[var(--ink)]/60 border border-[var(--line)] px-3 py-2"
        />
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="model (e.g. gpt-4o-mini)"
          className="w-full rounded-xl bg-[var(--ink)]/60 border border-[var(--line)] px-3 py-2"
        />
        <input
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="base URL (optional, OpenAI-compatible)"
          className="w-full rounded-xl bg-[var(--ink)]/60 border border-[var(--line)] px-3 py-2"
        />
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] font-semibold px-4 py-2"
            onClick={() => {
              void api
                .patchMe({
                  api_key: apiKey || undefined,
                  model: model || null,
                  api_base_url: baseUrl || null,
                })
                .then((r) => {
                  onUser(r.user)
                  setApiKey('')
                  onToast('Saved')
                })
                .catch((e) => onToast(e.message))
            }}
          >
            Save
          </button>
          <button
            type="button"
            className="rounded-xl border border-[var(--line)] px-4 py-2"
            onClick={() => {
              void api
                .patchMe({ clear_api_key: true })
                .then((r) => {
                  onUser(r.user)
                  onToast('API key cleared')
                })
            }}
          >
            Clear key
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 space-y-2">
        <div className="font-medium">Assistant capabilities</div>
        <p className="text-xs text-[var(--muted)] leading-relaxed">
          Ask for a daily briefing, search the web, remember facts, diary, calendar,
          Gmail, or message a contact. Outbound mail always asks Allow. Attach files
          in chat with File.
        </p>
        <p className="text-xs text-[var(--text)]">{assistantNote || 'Loading…'}</p>
        <p className="text-xs text-[var(--muted)]">
          Try: «сделай утренний брифинг» · «что в почте» · «напомни когда открою приложение»
        </p>
      </div>

      <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 space-y-3">
        <div className="font-medium">Google (Gmail + Calendar)</div>
        <p className="text-xs text-[var(--muted)]">{googleNote}</p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] font-semibold px-4 py-2"
            onClick={() => {
              void api
                .googleConnect()
                .then((r) => {
                  location.href = r.url
                })
                .catch((e) => onToast(e.message))
            }}
          >
            Connect Google
          </button>
          <button
            type="button"
            className="rounded-xl border border-[var(--line)] px-4 py-2"
            onClick={() => {
              void api
                .googleDisconnect()
                .then(() => reloadGoogle())
                .then(() => onToast('Google disconnected'))
                .catch((e) => onToast(e.message))
            }}
          >
            Disconnect
          </button>
        </div>
      </div>

      {user.role === 'owner' && (
        <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 space-y-3">
          <div className="font-medium">Backup</div>
          <p className="text-xs text-[var(--muted)]">
            Download a zip of hub data (chats, notes, calendar, contacts…). Keeps{' '}
            <code className="text-[var(--accent)]">secrets.key</code> out of the archive —
            copy that file separately if you need full restore.
          </p>
          <button
            type="button"
            className="rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] font-semibold px-4 py-2"
            onClick={() => {
              void api
                .downloadBackup()
                .then(() => onToast('Backup downloaded'))
                .catch((e) => onToast(e.message))
            }}
          >
            Download backup
          </button>
        </div>
      )}

      <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 space-y-3">
        <div className="font-medium">Contacts</div>
        <p className="text-xs text-[var(--muted)]">
          Save people the assistant can email or Telegram (after your confirmation).
        </p>
        <ul className="space-y-2">
          {contacts.length === 0 && (
            <li className="text-xs text-[var(--muted)]">No contacts yet.</li>
          )}
          {contacts.map((c) => (
            <li
              key={c.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--line)] bg-[var(--ink)]/40 px-3 py-2"
            >
              <div>
                <div className="text-sm font-medium">{c.name}</div>
                <div className="text-xs text-[var(--muted)]">
                  {[c.email, c.telegram ? `@${String(c.telegram).replace(/^@/, '')}` : null]
                    .filter(Boolean)
                    .join(' · ') || 'no channel'}
                </div>
              </div>
              <button
                type="button"
                className="text-xs text-[var(--muted)] hover:text-[var(--danger)]"
                onClick={() => {
                  void api
                    .deleteContact(c.id)
                    .then(() => reloadContacts())
                    .then(() => onToast('Contact removed'))
                    .catch((e) => onToast(e.message))
                }}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
        <div className="space-y-2 pt-1">
          {(['name', 'email', 'telegram'] as const).map((k) => (
            <input
              key={k}
              value={contactForm[k]}
              onChange={(e) => setContactForm({ ...contactForm, [k]: e.target.value })}
              placeholder={k === 'telegram' ? 'telegram chat id or @user' : k}
              className="w-full rounded-xl bg-[var(--ink)]/60 border border-[var(--line)] px-3 py-2 text-sm"
            />
          ))}
          <button
            type="button"
            className="rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] font-semibold px-4 py-2"
            onClick={() => {
              void api
                .upsertContact(contactForm)
                .then(() => {
                  setContactForm({ name: '', email: '', telegram: '' })
                  return reloadContacts()
                })
                .then(() => onToast('Contact saved'))
                .catch((e) => onToast(e.message))
            }}
          >
            Save contact
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 space-y-3">
        <div className="font-medium">Reminders on this phone / PC</div>
        <p className="text-xs text-[var(--muted)]">
          Allow notifications so due reminders can wake the device when the app is closed.
          The hub PC must stay on. Closed-app push works on HTTPS or localhost; on plain
          Wi‑Fi HTTP you still get alerts while the app is open.
        </p>
        <button
          type="button"
          className="rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] font-semibold px-4 py-2"
          onClick={() => {
            void enablePush()
              .then((msg) => {
                setPushNote(msg)
                onToast(msg)
              })
              .catch((e) => onToast(e instanceof Error ? e.message : 'Push failed'))
          }}
        >
          Enable notifications
        </button>
        {pushNote && <p className="text-xs text-[var(--muted)]">{pushNote}</p>}
      </div>

      {user.role === 'owner' && (
        <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 space-y-3">
          <div className="font-medium">Invite others</div>
          <p className="text-xs text-[var(--muted)]">
            Friends create their own account with an invite code and their own AI key.
          </p>
          <button
            type="button"
            className="rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] font-semibold px-4 py-2"
            onClick={() => {
              void api
                .invite()
                .then((r) => setInvite(r.code))
                .catch((e) => onToast(e.message))
            }}
          >
            Create invite
          </button>
          {invite && (
            <div className="rounded-xl bg-[var(--ink)]/70 px-3 py-2 text-sm break-all">
              Invite code: <span className="text-[var(--accent)] font-semibold">{invite}</span>
            </div>
          )}
        </div>
      )}

      <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4">
        <div className="font-medium mb-1">Tools for your role</div>
        <p className="text-xs text-[var(--muted)] leading-relaxed">{toolPreview || '…'}</p>
      </div>

      <p className="text-xs text-[var(--muted)]">
        Install as an app: browser menu → Add to Home Screen / Install.
      </p>
    </div>
  )
}
