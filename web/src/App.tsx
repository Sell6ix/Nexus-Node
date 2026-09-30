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
  speakHqBlob,
  stopSpeaking,
  recordAudioBlob,
  stopActiveRecording,
  type ListenHandle,
  type VoicePrefs,
} from './voice'

type Msg = { role: 'user' | 'assistant' | 'system'; text: string }
type ConfirmReq = { id: string; prompt: string; timeout: number }
type Tab = 'chat' | 'devices' | 'settings'
type Conversation = { id: string; title: string; created_at?: number; updated_at?: number }

const CONV_KEY = 'nexus_active_conversation'

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
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversationId, setConversationId] = useState(
    () => localStorage.getItem(CONV_KEY) || 'main',
  )
  const conversationIdRef = useRef(conversationId)
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
  const [setup, setSetup] = useState<{
    has_api_key: boolean
    briefing_enabled: boolean
    google_connected: boolean
    push_configured: boolean
  } | null>(null)
  const [setupDismissed, setSetupDismissed] = useState(
    () => localStorage.getItem('nexus_setup_dismissed') === '1',
  )
  const fileInputRef = useRef<HTMLInputElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const listenRef = useRef<ListenHandle | null>(null)
  const busyRef = useRef(false)
  const deepLinkDone = useRef(false)

  useEffect(() => {
    conversationIdRef.current = conversationId
    localStorage.setItem(CONV_KEY, conversationId)
  }, [conversationId])

  async function loadConversations() {
    try {
      const res = await api.listConversations()
      const rows = res.conversations || []
      setConversations(rows)
      if (rows.length && !rows.some((c) => c.id === conversationIdRef.current)) {
        setConversationId(rows[0].id)
      }
    } catch {
      setConversations([{ id: 'main', title: 'General' }])
    }
  }

  async function loadChat(cid?: string) {
    const id = cid || conversationIdRef.current || 'main'
    try {
      const res = await api.getChat(id)
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

  async function speakReply(text: string) {
    if (!voicePrefs.replies) return
    if (voicePrefs.hq && user?.has_api_key) {
      try {
        const blob = await api.speak(text, voicePrefs.lang)
        await speakHqBlob(blob, text, voicePrefs.lang)
        return
      } catch {
        /* fall through */
      }
    }
    speakText(text, voicePrefs.lang)
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
          await loadConversations()
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
    void api
      .assistant()
      .then((a) => {
        if (a.setup) setSetup(a.setup)
      })
      .catch(() => undefined)
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
    if (boot !== 'app' || !user) return
    setSetup((s) =>
      s
        ? { ...s, has_api_key: Boolean(user.has_api_key) }
        : {
            has_api_key: Boolean(user.has_api_key),
            briefing_enabled: false,
            google_connected: false,
            push_configured: false,
          },
    )
  }, [boot, user?.has_api_key])

  useEffect(() => {
    if (boot !== 'app' || !chatReady || deepLinkDone.current) return
    const q = new URLSearchParams(location.search)
    const tabQ = q.get('tab')
    if (tabQ === 'chat' || tabQ === 'devices' || tabQ === 'settings') setTab(tabQ)
    if (q.get('briefing') === '1') {
      deepLinkDone.current = true
      history.replaceState({}, '', location.pathname)
      void sendMessage('Give me my daily briefing.')
      return
    }
    if (q.get('voice') === '1') {
      deepLinkDone.current = true
      history.replaceState({}, '', location.pathname)
      void toggleMic()
      return
    }
    if (tabQ) {
      deepLinkDone.current = true
      history.replaceState({}, '', location.pathname)
    }
  }, [boot, chatReady])

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
          if (data.type === 'reminder' || data.type === 'briefing') {
            setToast(data.text || (data.type === 'briefing' ? 'Briefing' : 'Reminder'))
            if (conversationIdRef.current === 'main') {
              setMessages((m) => [...m, { role: 'system', text: data.text }])
            }
            showLocalNotification(
              data.type === 'briefing' ? 'Daily briefing' : 'Nexus Node',
              data.text,
            )
            const prefs = loadVoicePrefs()
            if (prefs.replies && data.text) {
              if (prefs.hq) {
                void api
                  .speak(String(data.text), prefs.lang)
                  .then((blob) => speakHqBlob(blob, String(data.text), prefs.lang))
                  .catch(() => speakText(String(data.text), prefs.lang))
              } else {
                speakText(String(data.text), prefs.lang)
              }
            }
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
      await loadConversations()
      await loadChat()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Auth failed')
    } finally {
      setAuthBusy(false)
    }
  }

  async function switchConversation(id: string) {
    if (id === conversationId || busy) return
    stopMic()
    setConversationId(id)
    setChatReady(false)
    setPendingFiles([])
    await loadChat(id)
  }

  async function newConversation() {
    if (busy) return
    try {
      const res = await api.createConversation('New chat')
      await loadConversations()
      await switchConversation(res.conversation.id)
      setToast('New chat')
    } catch (e) {
      setToast(e instanceof Error ? e.message : 'Could not create chat')
    }
  }

  async function clearChat() {
    if (busy) return
    try {
      await api.clearChat(conversationId)
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
      const res = await api.chat(
        text || 'Please look at the attached file(s).',
        ids,
        conversationIdRef.current,
      )
      const reply = res.reply || '(empty)'
      setMessages((m) => [...m, { role: 'assistant', text: reply }])
      void speakReply(reply)
      void loadConversations()
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

  async function runWhisperCapture() {
    if (!user?.has_api_key) {
      setToast('HQ voice needs an AI key in Settings (or use browser STT with HQ off).')
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

  async function toggleMic() {
    if (listening || recordingFallback) {
      stopMic()
      return
    }
    if (busy) return
    stopSpeaking()
    setToast('')

    // HQ path: Whisper-first when enabled and keyed
    if (voicePrefs.hq && user?.has_api_key) {
      await runWhisperCapture()
      return
    }

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

    await runWhisperCapture()
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

      {(() => {
        const steps = [
          {
            ok: Boolean(setup?.has_api_key ?? user.has_api_key),
            label: 'AI API key',
            hint: 'Settings → paste your key',
          },
          {
            ok: Boolean(setup?.push_configured),
            label: 'Notifications',
            hint: 'Settings → Enable notifications',
          },
          {
            ok: Boolean(setup?.briefing_enabled),
            label: 'Morning briefing',
            hint: 'Settings → local time + city',
          },
          {
            ok: Boolean(setup?.google_connected),
            label: 'Google (optional)',
            hint: 'Gmail + Calendar',
          },
        ]
        const done = steps.filter((s) => s.ok).length
        const requiredDone = steps.slice(0, 3).every((s) => s.ok)
        if (setupDismissed && requiredDone) return null
        if (setupDismissed && (setup?.has_api_key ?? user.has_api_key)) return null
        return (
          <div className="mb-3 rounded-2xl border border-[var(--warn)]/40 bg-[var(--panel)] px-4 py-3 space-y-2">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-sm font-medium">Get ready ({done}/{steps.length})</div>
                <p className="text-xs text-[var(--muted)] mt-0.5">
                  A few steps so reminders, briefing, and chat work on phone + PC.
                </p>
              </div>
              <button
                type="button"
                className="text-xs text-[var(--muted)] hover:text-[var(--text)] shrink-0"
                onClick={() => {
                  localStorage.setItem('nexus_setup_dismissed', '1')
                  setSetupDismissed(true)
                }}
              >
                Dismiss
              </button>
            </div>
            <ul className="space-y-1.5">
              {steps.map((s) => (
                <li key={s.label} className="flex items-center gap-2 text-xs">
                  <span
                    className={
                      s.ok ? 'text-[var(--accent)]' : 'text-[var(--muted)]'
                    }
                    aria-hidden
                  >
                    {s.ok ? '✓' : '○'}
                  </span>
                  <span className={s.ok ? 'text-[var(--muted)] line-through' : ''}>
                    {s.label}
                  </span>
                  {!s.ok && (
                    <span className="text-[var(--muted)]">— {s.hint}</span>
                  )}
                </li>
              ))}
            </ul>
            <button
              type="button"
              onClick={() => setTab('settings')}
              className="text-xs font-semibold text-[var(--accent)]"
            >
              Open Settings →
            </button>
          </div>
        )
      })()}

      {tab === 'chat' && (
        <section className="flex-1 flex flex-col min-h-0">
          <div className="flex flex-wrap items-center gap-2 pb-2">
            <select
              value={conversationId}
              onChange={(e) => void switchConversation(e.target.value)}
              disabled={busy || !chatReady}
              className="flex-1 min-w-[10rem] text-sm bg-[var(--panel)] border border-[var(--line)] rounded-xl px-3 py-2"
              aria-label="Conversation"
            >
              {(conversations.length ? conversations : [{ id: 'main', title: 'General' }]).map(
                (c) => (
                  <option key={c.id} value={c.id}>
                    {c.title || c.id}
                  </option>
                ),
              )}
            </select>
            <button
              type="button"
              onClick={() => void newConversation()}
              disabled={busy}
              className="text-xs px-3 py-2 rounded-xl border border-[var(--line)] text-[var(--muted)] hover:text-[var(--text)] disabled:opacity-40"
            >
              New chat
            </button>
          </div>
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
                onClick={() =>
                  setVoicePrefs((p) => ({ ...p, hq: !p.hq }))
                }
                className={`text-xs px-2 py-1 rounded-lg border ${
                  voicePrefs.hq
                    ? 'border-[var(--accent)] text-[var(--accent)]'
                    : 'border-[var(--line)] text-[var(--muted)]'
                }`}
                title="Whisper + server TTS (needs AI key)"
              >
                {voicePrefs.hq ? 'HQ voice' : 'Browser voice'}
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
              <span className="text-xs text-[var(--muted)]">
                {voicePrefs.hq
                  ? 'HQ: Whisper in, TTS out (AI key)'
                  : speechRecognitionSupported()
                    ? 'Browser speech'
                    : 'Browser STT unavailable — enable HQ voice'}
              </span>
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
    {
      id: string
      name: string
      email?: string | null
      telegram?: string | null
      whatsapp?: string | null
    }[]
  >([])
  const [contactForm, setContactForm] = useState({
    name: '',
    email: '',
    telegram: '',
    whatsapp: '',
  })
  const [briefEnabled, setBriefEnabled] = useState(false)
  const [briefHour, setBriefHour] = useState(8)
  const [briefMinute, setBriefMinute] = useState(0)
  const [briefTz, setBriefTz] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  )
  const [briefLocation, setBriefLocation] = useState('')

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
          a.whatsapp_messaging?.ready ? 'WhatsApp: ready' : 'WhatsApp: not set',
          a.hq_voice?.ready ? 'HQ voice: ready' : 'HQ voice: needs AI key',
        ]
        setAssistantNote(bits.join(' · '))
      })
      .catch(() => undefined)
    void api
      .getBriefingSchedule()
      .then((r) => {
        setBriefEnabled(Boolean(r.schedule?.enabled))
        setBriefHour(Number(r.schedule?.hour ?? 8))
        setBriefMinute(Number(r.schedule?.minute ?? 0))
        setBriefTz(
          r.schedule?.timezone ||
            Intl.DateTimeFormat().resolvedOptions().timeZone ||
            'UTC',
        )
        setBriefLocation(r.schedule?.location || '')
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
          Multiple chats, scheduled briefing, HQ voice (Whisper + TTS), search, memory,
          diary, calendar, Gmail, email / Telegram / WhatsApp. Outbound always asks Allow.
        </p>
        <p className="text-xs text-[var(--text)]">{assistantNote || 'Loading…'}</p>
        <p className="text-xs text-[var(--muted)]">
          Try: «включи брифинг в 8 по Москве» · «что в почте» · «напиши Маше в WhatsApp…»
        </p>
      </div>

      <div className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 space-y-3">
        <div className="font-medium">Morning briefing (auto)</div>
        <p className="text-xs text-[var(--muted)]">
          Daily summary at a local time (your timezone). Weather uses the city below
          (Open-Meteo). Enable phone notifications so it can wake the device.
        </p>
        <label className="text-sm flex items-center gap-2">
          <input
            type="checkbox"
            checked={briefEnabled}
            onChange={(e) => setBriefEnabled(e.target.checked)}
          />
          Enabled
        </label>
        <div className="flex flex-wrap gap-2 items-center">
          <label className="text-xs text-[var(--muted)]">
            Hour
            <input
              type="number"
              min={0}
              max={23}
              value={briefHour}
              onChange={(e) => setBriefHour(Number(e.target.value))}
              className="ml-2 w-16 rounded-lg bg-[var(--ink)]/60 border border-[var(--line)] px-2 py-1"
            />
          </label>
          <label className="text-xs text-[var(--muted)]">
            Minute
            <input
              type="number"
              min={0}
              max={59}
              value={briefMinute}
              onChange={(e) => setBriefMinute(Number(e.target.value))}
              className="ml-2 w-16 rounded-lg bg-[var(--ink)]/60 border border-[var(--line)] px-2 py-1"
            />
          </label>
        </div>
        <input
          value={briefTz}
          onChange={(e) => setBriefTz(e.target.value)}
          placeholder="timezone (e.g. Europe/Moscow)"
          className="w-full rounded-xl bg-[var(--ink)]/60 border border-[var(--line)] px-3 py-2 text-sm"
        />
        <input
          value={briefLocation}
          onChange={(e) => setBriefLocation(e.target.value)}
          placeholder="city for weather (e.g. Moscow)"
          className="w-full rounded-xl bg-[var(--ink)]/60 border border-[var(--line)] px-3 py-2 text-sm"
        />
        <button
          type="button"
          className="rounded-xl bg-[var(--accent)] text-[var(--accent-ink)] font-semibold px-4 py-2"
          onClick={() => {
            void api
              .setBriefingSchedule({
                enabled: briefEnabled,
                hour: briefHour,
                minute: briefMinute,
                timezone: briefTz || 'UTC',
                location: briefLocation,
              })
              .then(() => onToast('Briefing schedule saved'))
              .catch((e) => onToast(e.message))
          }}
        >
          Save schedule
        </button>
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
          Email, Telegram, or WhatsApp (E.164 digits). Outbound always asks Allow.
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
                  {[
                    c.email,
                    c.telegram ? `@${String(c.telegram).replace(/^@/, '')}` : null,
                    c.whatsapp ? `WA ${c.whatsapp}` : null,
                  ]
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
          {(['name', 'email', 'telegram', 'whatsapp'] as const).map((k) => (
            <input
              key={k}
              value={contactForm[k]}
              onChange={(e) => setContactForm({ ...contactForm, [k]: e.target.value })}
              placeholder={
                k === 'telegram'
                  ? 'telegram chat id or @user'
                  : k === 'whatsapp'
                    ? 'whatsapp phone (e.g. 79001234567)'
                    : k
              }
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
                  setContactForm({ name: '', email: '', telegram: '', whatsapp: '' })
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
