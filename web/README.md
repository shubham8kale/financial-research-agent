# Financial Research Agent — web client

A Next.js (App Router) chat interface for the backend in the parent
directory. It POSTs to `/query/stream` and renders the Server-Sent Events
the API emits, in order:

| event | rendered as |
|---|---|
| `token` (repeated) | the answer, appended word by word |
| `sources` | a list of cited chunks and tagged facts; those with `cited: true` carry a check mark |
| `verification` | one line above the sources: verified, unverified, or withheld (see `agent/contract.py`) |
| `meta` | one line under the answer: seconds, tokens, dollars, tools called, trace id |
| `done` / `error` | end of stream, or the error copy |

The event contract is typed in [`lib/api.ts`](lib/api.ts) and mirrors
`api/main.py`; the message component is [`components/Message.tsx`](components/Message.tsx).

## Run it

```bash
npm ci
npm run dev        # http://localhost:3000
```

The backend URL is `NEXT_PUBLIC_API_BASE_URL`, inlined at build time; unset,
the code defaults to `http://localhost:8080`. Copy [`.env.example`](.env.example)
to `.env.local` to change it locally; Vercel sets it per environment. CORS on
the backend is controlled by its `FRONTEND_ORIGINS` variable.

## Check it

```bash
npm run lint
npm test           # 16 Vitest tests: streamed tokens + citations + verdict + meter, a withheld answer, an error, the conversation thread and New chat
npm run build
```

CI runs the same three commands on every push and pull request
(`.github/workflows/ci.yml`, job `frontend`, Node 22).

## Deploy

Vercel (Hobby): import the repository, set **Root Directory** to `web/`, set
`NEXT_PUBLIC_API_BASE_URL` to the backend URL.
