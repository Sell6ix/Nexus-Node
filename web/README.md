# Nexus Node web UI

Vite + React + TypeScript PWA for the hub (`python main.py --mode web`).

## Dev

```bash
# terminal 1 — hub API
python main.py --mode web --host 127.0.0.1 --port 8743

# terminal 2 — UI with HMR (proxies /api to the hub)
npm install
npm run dev
```

Open the Vite URL (default `http://127.0.0.1:5174`).

## Production

```bash
npm run build
```

The hub serves `web/dist` when present.
