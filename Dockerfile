# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

# curl is used by the container HEALTHCHECK below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Keep the embedding model cache inside the image so the model downloaded at
# build time is reused at runtime (avoids a cold-start re-download).
ENV HF_HOME=/app/.cache/huggingface \
    PYTHONUNBUFFERED=1

# Install Python deps. --extra-index-url pulls CPU-only PyTorch wheels so we
# don't ship the ~2 GB CUDA build (mirrors the CI install step).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu

# App source + the raw 10-K filings. The Chroma index itself is NOT copied
# (see .dockerignore) — it is rebuilt from these filings in the next step.
COPY . .

# Build the ChromaDB vector index at image-build time using local MiniLM
# embeddings. No GEMINI_API_KEY and no SEC network download are needed here:
# it reads the committed filings under data/sec_filings/ and writes
# data/chroma_db/. This keeps the 245 MB index out of git while making the
# image fully self-contained and reproducible.
RUN python -m ingestion.pipeline

# HF Spaces may run the container under a non-root UID; make the index and model
# cache group/other-writable so Chroma can open its SQLite files at runtime.
RUN chmod -R a+rwX /app/data /app/.cache

# HF Spaces expects the app on port 7860 (declared as app_port in the Space's
# README). PORT is also honoured so the same image runs on Render / locally.
ENV PORT=7860
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-7860}/health" || exit 1

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
