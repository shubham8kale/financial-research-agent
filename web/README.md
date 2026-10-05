# Financial Research Agent — web client

A Next.js (App Router) chat interface for the backend in the parent
directory. It POSTs to `/query/stream` and renders the Server-Sent Events
the API emits, in order:

| event | rendered as |
|---|---|
| `token` (repeated) | the answer, appended word by word and rendered as markdown (headings, bold, lists, tables) |
| `sources` | a list of cited chunks and tagged facts; those with `cited: true` carry a check mark |
| `verification` | one line above the sources: verified, unverified, or withheld (see `agent/contract.py`) |
| `meta` | one line under the answer: seconds, tokens, dollars, tools called, trace id |
| `done` / `error` | end of stream, or the error copy |

The event contract is typed in [`lib/api.ts`](lib/api.ts) and mirrors
`api/main.py`; the message component is [`components/Message.tsx`](components/Message.tsx).

Assistant answers are rendered as markdown with `react-markdown` and `remark-gfm`
(pinned to exact versions). Headings, paragraphs, bold, lists, tables and links
are styled to chat-bubble scale; code blocks, quotes and rules are not mapped
and keep the page's reset styling. Nothing the model writes is rendered as HTML:
there is deliberately no `rehype-raw`, so a tag in an answer is shown as text;
a `javascript:` link is shown as plain text; an image written in markdown syntax
is replaced by its alt text instead of being fetched; and a single `~` is not
strikethrough, so "about $5B" is never struck out. Links open in a new tab with
`rel="noopener noreferrer"`. The user's own messages and the error copy stay
plain text.

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
npm test           # 30 Vitest tests: streamed tokens + citations + verdict + meter, a withheld answer, an error, the conversation thread and New chat, markdown rendering and no raw HTML
npm run build
```

CI runs the same three commands on every push and pull request
(`.github/workflows/ci.yml`, job `frontend`, Node 22).

## Deploy

Vercel (Hobby): import the repository, set **Root Directory** to `web/`, set
`NEXT_PUBLIC_API_BASE_URL` to the backend URL.
