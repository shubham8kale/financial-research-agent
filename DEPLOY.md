# Deploy (free): Vercel frontend + Hugging Face Spaces backend

This deploys the whole stack on free tiers. The frontend goes to **Vercel
(Hobby)**; the FastAPI backend goes to **Hugging Face Spaces (Docker SDK, CPU
basic — 16 GB RAM)**, which comfortably runs torch + sentence-transformers + the
Chroma index.

The image **rebuilds the Chroma index at build time** from the committed filings
under `data/sec_filings/` (local MiniLM embeddings, no API key, no SEC download),
so the 245 MB index never needs to live in git.

## Prerequisites

- A GitHub account (public repo for the code).
- A free Gemini API key: <https://aistudio.google.com/app/apikey>.
- A Vercel account and a Hugging Face account.
- `git` and (for the HF push) `git lfs` installed locally.

---

## Step 0 — Push the repo to GitHub

From the repo root (`financial-research-agent/`):

```bash
git remote add origin https://github.com/<you>/financial-research-agent.git
git push -u origin feat/web-frontend   # or merge to main first, then push main
```

`.env` is gitignored, so your Gemini key is **not** pushed. The raw filings
(~84 MB, largest file 33 MB) are committed so the backend can re-index from them.

---

## Step 1 — Backend on Hugging Face Spaces

1. **Create a Space:** huggingface.co → New Space → SDK **Docker** → *Blank* →
   hardware **CPU basic (free)**.

2. **Add the Space front-matter.** HF reads Docker config from the Space's
   `README.md`. Prepend this block to the top of the README **in the Space repo**
   (not required in the GitHub repo):

   ```yaml
   ---
   title: Financial Research Agent API
   emoji: 📈
   colorFrom: indigo
   colorTo: blue
   sdk: docker
   app_port: 7860
   pinned: false
   ---
   ```

3. **Push the code to the Space.** Clone the Space and copy this repo's contents
   in (or add the Space as a second remote and push). Because `data/sec_filings/`
   contains files larger than 10 MB, track them with LFS before committing to HF:

   ```bash
   git lfs install
   git lfs track "data/sec_filings/**/*.txt"
   git add .gitattributes data/sec_filings
   git commit -m "deploy: filings via lfs for HF Space"
   git push space main
   ```

4. **Set secrets** (Space → Settings → *Variables and secrets*):
   - `GEMINI_API_KEY` = your key (secret). *Required — the app builds the agent
     at startup and won't boot without it.*
   - `FRONTEND_ORIGINS` = `http://localhost:3000` for now (you'll add the Vercel
     domain in Step 3).

5. **Wait for the build.** First build is slow (CPU torch install + model
   download + re-indexing the filings). When it's live, check health:

   ```bash
   curl https://<you>-financial-research-agent.hf.space/health
   # {"status":"healthy","mcp_server":false}
   ```

   That base URL — `https://<you>-<space>.hf.space` — is your backend API URL.

---

## Step 2 — Frontend on Vercel

1. Vercel → **Add New → Project** → import the GitHub repo.
2. **Root Directory:** `web/`.
3. **Environment variable:** `NEXT_PUBLIC_API_BASE_URL` = the HF Space URL from
   Step 1 (e.g. `https://<you>-financial-research-agent.hf.space`).
4. **Deploy.** Note the resulting Vercel URL (e.g. `https://<app>.vercel.app`).

`NEXT_PUBLIC_*` vars are inlined at build time, so if you change the backend URL
later you must **redeploy** the frontend.

---

## Step 3 — Wire CORS

Back in the HF Space settings, set `FRONTEND_ORIGINS` to include the Vercel domain
(comma-separated, no trailing slash), then **Restart** the Space:

```
FRONTEND_ORIGINS=https://<app>.vercel.app,http://localhost:3000
```

Open the Vercel URL and send a question — you should see tokens stream in with
source citations.

---

## Step 4 — Finish

- Update the `<!-- LIVE_URL -->` placeholder in [README.md](README.md) with the
  Vercel URL and commit.
- Record a 60–90 s screen capture as a backup demo (cold-start note: the first
  request after the Space sleeps takes ~30–60 s to wake).

---

## Notes & troubleshooting

- **Cold start:** free Spaces sleep after inactivity; the first request wakes the
  container (~30–60 s), then answers stream normally.
- **Don't load-test the live link** — the Gemini free tier has per-minute limits.
- **Image size / RAM:** HF CPU basic (16 GB RAM) handles this stack. Render's
  free tier (512 MB RAM) will likely OOM loading torch + the index; if you must
  use Render, reduce the corpus (fewer tickers in `ingestion/downloader.py:
  TARGET_TICKERS`) to shrink both the image and memory footprint.
- **Vercel Hobby is non-commercial** — fine for a personal portfolio project.
