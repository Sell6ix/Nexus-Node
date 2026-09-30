/** Browser + HQ (Whisper/TTS) voice I/O. */

export type VoicePrefs = {
  replies: boolean
  autoSend: boolean
  lang: string
  /** Prefer Whisper STT + server TTS when an AI key is available. */
  hq: boolean
}

const PREFS_KEY = 'nexus_voice_prefs'

export function loadVoicePrefs(): VoicePrefs {
  try {
    const raw = localStorage.getItem(PREFS_KEY)
    if (raw) {
      const p = JSON.parse(raw) as Partial<VoicePrefs>
      return {
        replies: Boolean(p.replies),
        autoSend: p.autoSend !== false,
        lang: typeof p.lang === 'string' && p.lang ? p.lang : guessLang(),
        hq: p.hq !== false,
      }
    }
  } catch {
    /* ignore */
  }
  return { replies: false, autoSend: true, lang: guessLang(), hq: true }
}

export function saveVoicePrefs(prefs: VoicePrefs) {
  localStorage.setItem(PREFS_KEY, JSON.stringify(prefs))
}

function guessLang(): string {
  const nav = (navigator.language || 'en-US').toLowerCase()
  if (nav.startsWith('ru')) return 'ru-RU'
  if (nav.startsWith('uk')) return 'uk-UA'
  if (nav.startsWith('de')) return 'de-DE'
  return 'en-US'
}

type SpeechRec = {
  lang: string
  continuous: boolean
  interimResults: boolean
  start: () => void
  stop: () => void
  abort: () => void
  onresult: ((ev: SpeechRecognitionEventLike) => void) | null
  onerror: ((ev: { error: string }) => void) | null
  onend: (() => void) | null
}

type SpeechRecognitionEventLike = {
  resultIndex: number
  results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }>
}

function SpeechRecognitionCtor(): (new () => SpeechRec) | null {
  const w = window as unknown as {
    SpeechRecognition?: new () => SpeechRec
    webkitSpeechRecognition?: new () => SpeechRec
  }
  return w.SpeechRecognition || w.webkitSpeechRecognition || null
}

export function speechRecognitionSupported(): boolean {
  return SpeechRecognitionCtor() !== null
}

export function speechSynthesisSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

export type ListenHandle = {
  stop: () => void
}

/** Start browser speech recognition. Returns null if unsupported. */
export function startListening(opts: {
  lang: string
  onInterim: (text: string) => void
  onFinal: (text: string) => void
  onError: (message: string) => void
  onEnd: () => void
}): ListenHandle | null {
  const Ctor = SpeechRecognitionCtor()
  if (!Ctor) return null
  const rec = new Ctor()
  rec.lang = opts.lang
  rec.continuous = false
  rec.interimResults = true
  let finished = false

  rec.onresult = (ev) => {
    let interim = ''
    let final = ''
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const row = ev.results[i]
      const t = row[0]?.transcript || ''
      if (row.isFinal) final += t
      else interim += t
    }
    if (interim) opts.onInterim(interim)
    if (final.trim()) {
      finished = true
      opts.onFinal(final.trim())
    }
  }
  rec.onerror = (ev) => {
    const err = ev.error || 'speech error'
    if (err === 'aborted' || err === 'no-speech') {
      opts.onEnd()
      return
    }
    opts.onError(err === 'not-allowed' ? 'Microphone permission denied.' : `Voice error: ${err}`)
  }
  rec.onend = () => {
    if (!finished) opts.onEnd()
  }
  try {
    rec.start()
  } catch (e) {
    opts.onError(e instanceof Error ? e.message : 'Could not start microphone')
    return null
  }
  return {
    stop: () => {
      try {
        rec.stop()
      } catch {
        try {
          rec.abort()
        } catch {
          /* ignore */
        }
      }
    },
  }
}

/** Record a short audio clip (for Whisper). */
export async function recordAudioBlob(maxMs = 20000): Promise<Blob> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : MediaRecorder.isTypeSupported('audio/webm')
      ? 'audio/webm'
      : ''
  const chunks: BlobPart[] = []
  const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)

  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      if (recorder.state === 'recording') recorder.stop()
    }, maxMs)
    recorder.ondataavailable = (ev) => {
      if (ev.data.size) chunks.push(ev.data)
    }
    recorder.onerror = () => {
      window.clearTimeout(timer)
      stream.getTracks().forEach((t) => t.stop())
      reject(new Error('Recording failed'))
    }
    recorder.onstop = () => {
      window.clearTimeout(timer)
      stream.getTracks().forEach((t) => t.stop())
      resolve(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }))
    }
    recorder.start()
    ;(recordAudioBlob as unknown as { _active?: MediaRecorder })._active = recorder
  })
}

export function stopActiveRecording() {
  const rec = (recordAudioBlob as unknown as { _active?: MediaRecorder })._active
  if (rec && rec.state === 'recording') rec.stop()
}

let activeAudio: HTMLAudioElement | null = null

function cleanSpeakText(text: string): string {
  return (text || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/[`*_#>+-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

export function speakText(text: string, lang: string): void {
  if (!speechSynthesisSupported()) return
  const clean = cleanSpeakText(text)
  if (!clean) return
  window.speechSynthesis.cancel()
  const utter = new SpeechSynthesisUtterance(clean.slice(0, 1200))
  utter.lang = lang
  const voices = window.speechSynthesis.getVoices()
  const match =
    voices.find((v) => v.lang === lang) ||
    voices.find((v) => v.lang.startsWith(lang.split('-')[0] || 'en'))
  if (match) utter.voice = match
  utter.rate = 1.02
  window.speechSynthesis.speak(utter)
}

/** Play HQ TTS from an audio Blob (mp3). Falls back to browser TTS on failure. */
export async function speakHqBlob(blob: Blob, fallbackText: string, lang: string): Promise<void> {
  stopSpeaking()
  try {
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)
    activeAudio = audio
    await new Promise<void>((resolve, reject) => {
      audio.onended = () => {
        URL.revokeObjectURL(url)
        if (activeAudio === audio) activeAudio = null
        resolve()
      }
      audio.onerror = () => {
        URL.revokeObjectURL(url)
        if (activeAudio === audio) activeAudio = null
        reject(new Error('audio play failed'))
      }
      void audio.play().catch(reject)
    })
  } catch {
    speakText(fallbackText, lang)
  }
}

export function stopSpeaking() {
  if (speechSynthesisSupported()) window.speechSynthesis.cancel()
  if (activeAudio) {
    try {
      activeAudio.pause()
      activeAudio.src = ''
    } catch {
      /* ignore */
    }
    activeAudio = null
  }
}
