# eval/ci_corpus.py
#
# PURPOSE
# -------
# A fixed slice of the index, committed as eval/ci_corpus.jsonl.gz, so that
# CI can score retrieval on every pull request without the full index.
# Embedding all 67,521 chunks takes about 27 minutes on a 12-core laptop and
# longer on a hosted runner; embedding the slice takes a few minutes once,
# and GitHub Actions caches the result on the fixture's hash.
#
# WHY A SLICE REPRODUCES THE FULL-INDEX NUMBERS
# ---------------------------------------------
# A chunk's embedding depends only on its own text, so its similarity to a
# query is the same in any index that holds it.  For every benchmark question
# the slice holds the 50 nearest chunks over the full index with no filter
# and the 50 nearest under the inferred ticker filter.  Every other chunk in
# the slice scored below those on the full index, so the ranking of the
# benchmark question over the slice at depth <= 50 is the full-index ranking
# — and neither gated configuration (dense top 25; dense 50 → cross-encoder →
# 25 under the inferred filter) looks deeper than 50.  eval/ci_gate.py prints
# the committed full-index value next to every number it measures here, so
# the claim is checked on every run rather than assumed.
#
# The slice also carries every labelled chunk, every chunk the agent itself
# retrieved in the committed schema-3 generation runs (its own reworded
# queries), and a seeded random sample of distractors, so the judged smoke
# run (.github/workflows/eval-judged.yml) meets a haystack rather than an
# answer key.  BM25 is NOT reproducible on a slice — its IDF is corpus-wide —
# and is not gated.
#
# USAGE
# -----
#   python -m eval.ci_corpus build     # local: needs the full index; writes the fixture
#   python -m eval.ci_corpus index     # CI: embeds the fixture into CHROMA_PERSIST_DIR
#   python -m eval.ci_corpus verify    # fixture, labels and (if present) index agree

import argparse
import gzip
import io
import json
import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.observations import chunk_id  # noqa: E402
from eval.chunk_labels import LABELS_FILE, load_labels, scorable_groups  # noqa: E402
from eval.experiment import benchmark_version  # noqa: E402
from eval.run_eval import BENCHMARK_FILE, RESULTS_DIR, load_benchmark  # noqa: E402

logger = logging.getLogger(__name__)

CORPUS_FILE = Path(__file__).resolve().parent / "ci_corpus.jsonl.gz"
SCHEMA_VERSION = 1
DEPTH = 50            # the deepest any gated configuration looks: fetch_k of the shipped one
N_DISTRACTORS = 2000
SEED = 42


# ── Fixture format ──────────────────────────────────────────────────────────
# Line 1 is a header object; every following line is one chunk:
#   {"id": "AAPL_10K_chunk_12", "ticker": "AAPL", "chunk_idx": 12,
#    "source": "data/sec_filings/...", "text": "..."}

def relative_source(source: str) -> str:
    """Strip the machine-specific prefix from a filing path so the fixture is portable."""
    s = (source or "").replace("\\", "/")
    marker = "/data/sec_filings/"
    i = s.find(marker)
    return s[i + 1:] if i >= 0 else s


def write_corpus(path: Path, header: dict, chunks: list[dict]) -> None:
    """Byte-stable gzip (mtime 0, sorted keys) so an unchanged slice does not churn git."""
    path = Path(path)
    with open(path, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        with io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(header, sort_keys=True) + "\n")
            for c in chunks:
                f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")


def read_header(path: Path = CORPUS_FILE) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.loads(f.readline())


def read_corpus(path: Path = CORPUS_FILE) -> tuple[dict, list[dict]]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        header = json.loads(f.readline())
        chunks = [json.loads(line) for line in f if line.strip()]
    return header, chunks


# ── Reading the full index ──────────────────────────────────────────────────

def load_full_index(persist_dir: Path, collection: str, page: int = 5000) -> dict[str, dict]:
    """Every chunk in the index keyed by chunk id, text verbatim (not normalised)."""
    import chromadb

    col = chromadb.PersistentClient(path=str(persist_dir)).get_collection(collection)
    n = col.count()
    out: dict[str, dict] = {}
    offset = 0
    while offset < n:
        res = col.get(include=["documents", "metadatas"], limit=page, offset=offset)
        for doc, meta in zip(res["documents"], res["metadatas"]):
            cid = chunk_id(meta["ticker"], meta["chunk_idx"])
            out[cid] = {
                "id": cid,
                "ticker": str(meta["ticker"]).upper(),
                "chunk_idx": int(meta["chunk_idx"]),
                "source": relative_source(str(meta.get("source") or "")),
                "text": doc,
            }
        offset += page
    logger.info("Loaded %d chunks from %s/%s", len(out), persist_dir, collection)
    return out


def index_chunk_count(persist_dir: Path, collection: str) -> int:
    """Chunks in the collection at *persist_dir*, 0 when there is no collection yet."""
    import chromadb

    try:
        return chromadb.PersistentClient(path=str(persist_dir)).get_collection(collection).count()
    except Exception:  # noqa: BLE001 — no collection, or no directory
        return 0


def agent_retrieved_ids(results_dir: Path = RESULTS_DIR) -> set[str]:
    """Chunk ids the agent retrieved with its own queries, from every committed schema-3 generation run."""
    ids: set[str] = set()
    for path in sorted(Path(results_dir).glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("schema_version") != 3 or (payload.get("config") or {}).get("result_kind") != "generation":
            continue
        for r in payload.get("results") or []:
            ids.update(r.get("retrieved_chunk_ids") or [])
    return ids


# ── Selection ───────────────────────────────────────────────────────────────

def select_chunks(rows: list[dict], labels: dict, retrieve_fn, all_ids: set[str], agent_ids: set[str],
                  depth: int = DEPTH, n_distractors: int = N_DISTRACTORS, seed: int = SEED) -> tuple[set[str], dict]:
    """Which chunk ids make up the slice, and how many each source contributed.

    retrieve_fn(question, depth, ticker) returns ranked chunk ids; *ticker* is None
    for the unfiltered pass and the one company the question names for the
    filtered pass (the shipped configuration's inferred filter).
    """
    from retrieval.tickers import infer_single_ticker

    nearest: set[str] = set()
    for row in rows:
        nearest.update(retrieve_fn(row["question"], depth, None))
        ticker = infer_single_ticker(row["question"])
        if ticker:
            nearest.update(retrieve_fn(row["question"], depth, ticker))
    chosen = set(nearest)
    counts = {"nearest_to_benchmark_questions": len(nearest)}

    labelled = {cid for row in rows for group in scorable_groups(labels, row["id"]) for cid in group}
    counts["labelled"] = len(labelled)
    counts["labelled_not_in_index"] = len(labelled - all_ids)
    counts["labelled_added"] = len((labelled & all_ids) - chosen)
    chosen |= labelled & all_ids

    agent = agent_ids & all_ids
    counts["agent_retrieved"] = len(agent)
    counts["agent_retrieved_added"] = len(agent - chosen)
    chosen |= agent

    pool = sorted(all_ids - chosen)
    distractors = set(random.Random(seed).sample(pool, min(n_distractors, len(pool))))
    counts["distractors"] = len(distractors)
    chosen |= distractors
    counts["total"] = len(chosen)
    return chosen, counts


# ── Commands ────────────────────────────────────────────────────────────────

def build(args) -> int:
    from ingestion.embedder import CHROMA_PERSIST_DIR, COLLECTION_NAME
    from retrieval.retriever import RetrievalConfig, Retriever

    rows = load_benchmark(BENCHMARK_FILE)
    labels = load_labels(LABELS_FILE)
    index = load_full_index(CHROMA_PERSIST_DIR, COLLECTION_NAME)
    if len(index) != labels.get("index_chunk_count"):
        print(f"The index at {CHROMA_PERSIST_DIR} has {len(index)} chunks but the labels were made on "
              f"{labels.get('index_chunk_count')}; the slice must be cut from the labelled index.")
        return 2

    retriever = Retriever(RetrievalConfig(mode="dense", k=args.depth))

    def retrieve_fn(question: str, k: int, ticker: str | None) -> list[str]:
        return [c.chunk_id for c in retriever.retrieve(question, k, ticker)]

    t0 = time.perf_counter()
    ids, counts = select_chunks(rows, labels, retrieve_fn, set(index), agent_retrieved_ids(),
                                args.depth, args.distractors, args.seed)
    chunks = sorted((index[cid] for cid in ids), key=lambda c: (c["ticker"], c["chunk_idx"]))
    header = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ci_corpus",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark_version": benchmark_version(BENCHMARK_FILE, LABELS_FILE),
        "index_collection": COLLECTION_NAME,
        "index_chunk_count": len(index),
        "depth": args.depth,
        "n_distractors": args.distractors,
        "seed": args.seed,
        "sources": counts,
        "n_chunks": len(chunks),
    }
    write_corpus(args.out, header, chunks)
    print(f"Wrote {len(chunks)} of {len(index)} chunks to {args.out} in {time.perf_counter() - t0:.1f}s")
    print(json.dumps(counts, indent=1))
    return 0


def index(args) -> int:
    from ingestion.embedder import CHROMA_PERSIST_DIR, COLLECTION_NAME, build_vectorstore, embed_chunks

    header, chunks = read_corpus(args.corpus)
    existing = index_chunk_count(CHROMA_PERSIST_DIR, COLLECTION_NAME)
    if existing:
        print(f"{CHROMA_PERSIST_DIR} already holds {existing} chunks. Point CHROMA_PERSIST_DIR at an "
              "empty directory; the slice is never written into an existing index.")
        return 2
    print(f"Embedding {len(chunks)} chunks from {args.corpus.name} into {CHROMA_PERSIST_DIR} ...")
    t0 = time.perf_counter()
    vectorstore = build_vectorstore()
    embed_chunks(
        [c["text"] for c in chunks],
        metadatas=[{"ticker": c["ticker"], "source": c["source"], "chunk_idx": c["chunk_idx"]} for c in chunks],
        vectorstore=vectorstore,
    )
    n = index_chunk_count(CHROMA_PERSIST_DIR, COLLECTION_NAME)
    print(f"Indexed {n} chunks in {time.perf_counter() - t0:.1f}s")
    return 0 if n == len(chunks) else 1


def verify(args) -> int:
    from ingestion.embedder import CHROMA_PERSIST_DIR, COLLECTION_NAME

    header, chunks = read_corpus(args.corpus)
    labels = load_labels(LABELS_FILE)
    problems = verify_against_labels(header, chunks, labels)
    live = index_chunk_count(CHROMA_PERSIST_DIR, COLLECTION_NAME)
    if live and live not in (len(chunks), labels.get("index_chunk_count")):
        problems.append(f"the index at {CHROMA_PERSIST_DIR} has {live} chunks: neither the slice "
                        f"({len(chunks)}) nor the labelled full index ({labels.get('index_chunk_count')})")
    print(f"{args.corpus.name}: {len(chunks)} chunks cut from a {header['index_chunk_count']}-chunk index "
          f"(benchmark {header['benchmark_version']}); live index: {live or 'none'} chunks")
    for p in problems:
        print(f"  PROBLEM: {p}")
    return 1 if problems else 0


def verify_against_labels(header: dict, chunks: list[dict], labels: dict) -> list[str]:
    """Why the slice cannot be trusted for these labels, as messages; empty when it can."""
    problems: list[str] = []
    if header.get("index_chunk_count") != labels.get("index_chunk_count"):
        problems.append(f"slice was cut from a {header.get('index_chunk_count')}-chunk index; the labels "
                        f"were made on {labels.get('index_chunk_count')} — rebuild with `python -m eval.ci_corpus build`")
    ids = {c["id"] for c in chunks}
    if len(ids) != len(chunks) or header.get("n_chunks") != len(chunks):
        problems.append(f"header says {header.get('n_chunks')} chunks, file holds {len(chunks)} ({len(ids)} distinct)")
    missing = sorted({cid for item in (labels.get("items") or {}).values()
                      for group in item.get("groups") or [] for cid in group} - ids)
    if missing:
        problems.append(f"{len(missing)} labelled chunk(s) are not in the slice: {', '.join(missing[:8])}")
    return problems


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="The committed index slice CI measures retrieval on.")
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="Cut the slice from the full local index (needs data/chroma_db).")
    b.add_argument("--out", type=Path, default=CORPUS_FILE)
    b.add_argument("--depth", type=int, default=DEPTH, help=f"Nearest chunks kept per question and pass (default {DEPTH}).")
    b.add_argument("--distractors", type=int, default=N_DISTRACTORS)
    b.add_argument("--seed", type=int, default=SEED)
    i = sub.add_parser("index", help="Embed the slice into an empty Chroma index at CHROMA_PERSIST_DIR.")
    i.add_argument("--corpus", type=Path, default=CORPUS_FILE)
    v = sub.add_parser("verify", help="Check the slice against the labels and the live index.")
    v.add_argument("--corpus", type=Path, default=CORPUS_FILE)
    return p


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = build_parser().parse_args()
    return {"build": build, "index": index, "verify": verify}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
