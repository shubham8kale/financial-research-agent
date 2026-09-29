# eval/chunk_labels.py
#
# PURPOSE
# -------
# Turn each benchmark item's reference passage(s) into the ids of the chunks
# that actually hold them in the index, and write the result to
# eval/benchmark_chunks.json.  Those ids are what the retrieval metrics score
# against.  Without them "did the retriever find the right page?" cannot be
# asked at all — only "does a blob of five pages contain something the judge
# recognises?", which is what the harness measured before this file existed.
#
# HOW A PASSAGE IS LOCATED
# ------------------------
# Whitespace-normalised, case-folded substring search over every chunk in the
# index.  A reference may match several chunks (overlap neighbours, repeated
# table rows); all of them go into ONE group, because any of them satisfies the
# reference.  A reference that does not match verbatim is retried on its first
# 60 characters — some references were transcribed with a punctuation change
# after the chunk text — and the method is recorded so a prefix match is never
# mistaken for an exact one.
#
# Anything still unlocated is resolved by hand in eval/chunk_labels_overrides.json,
# with a note saying why, and the item's method is recorded as "manual".  An
# override may also declare an item "unlocatable"; such items are excluded from
# retrieval metrics and counted in the summary, never silently dropped.  The
# benchmark CSV itself is never edited by this script.
#
# This script needs the local ChromaDB index.  Everything it does with the
# chunks once loaded is pure and covered by tests without the index.

import argparse
import csv
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.observations import chunk_id  # noqa: E402
from eval.experiment import file_sha256  # noqa: E402

logger = logging.getLogger(__name__)

EVAL_DIR = Path(__file__).resolve().parent
BENCHMARK_FILE = EVAL_DIR / "benchmark.csv"
LABELS_FILE = EVAL_DIR / "benchmark_chunks.json"
OVERRIDES_FILE = EVAL_DIR / "chunk_labels_overrides.json"

PREFIX_CHARS = 60
_REF_SPLIT_RE = re.compile(r"\s*\|\|\s*|\s*;;\s*")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Collapse whitespace and case so transcription differences do not matter."""
    return _WS_RE.sub(" ", text or "").strip().lower()


def split_references(field: str) -> list[str]:
    """The reference_contexts column holds one passage, or several joined by || or ;;."""
    return [r.strip() for r in _REF_SPLIT_RE.split(field or "") if r.strip()]


def locate(reference: str, chunks: list[tuple[str, str]]) -> tuple[list[str], str]:
    """Return (chunk ids containing *reference*, method).

    *chunks* is a list of (chunk_id, normalised_text).  Method is "exact",
    "prefix" or "none".
    """
    needle = normalize(reference)
    if not needle:
        return [], "none"
    hits = [cid for cid, text in chunks if needle in text]
    if hits:
        return hits, "exact"
    prefix = needle[:PREFIX_CHARS]
    if len(prefix) >= 20:
        hits = [cid for cid, text in chunks if prefix in text]
        if hits:
            return hits, "prefix"
    return [], "none"


def label_item(row: dict, chunks: list[tuple[str, str]], override: dict | None = None) -> dict:
    """Label one benchmark row.  An override wins outright when present."""
    if override is not None:
        if override.get("unlocatable"):
            return {
                "groups": [], "method": "unlocatable", "n_references": 0,
                "note": override.get("note", ""),
            }
        groups = [list(g) for g in override["groups"]]
        return {
            "groups": groups, "method": "manual", "n_references": len(groups),
            "note": override.get("note", ""),
        }

    refs = split_references(row.get("reference_contexts", ""))
    groups: list[list[str]] = []
    methods: list[str] = []
    for ref in refs:
        ids, method = locate(ref, chunks)
        methods.append(method)
        if ids:
            groups.append(sorted(set(ids)))
    if not refs:
        method = "none"
    elif all(m == "exact" for m in methods):
        method = "exact"
    elif all(m in ("exact", "prefix") for m in methods):
        method = "prefix"
    else:
        method = "partial" if groups else "none"
    return {"groups": groups, "method": method, "n_references": len(refs), "note": ""}


def label_benchmark(rows: list[dict], chunks: list[tuple[str, str]], overrides: dict) -> dict:
    return {row["id"]: label_item(row, chunks, overrides.get(row["id"])) for row in rows}


def summarize(items: dict) -> dict:
    counts: dict[str, int] = {}
    for it in items.values():
        counts[it["method"]] = counts.get(it["method"], 0) + 1
    return {
        "n_items": len(items),
        "by_method": dict(sorted(counts.items())),
        "n_scorable": sum(1 for it in items.values() if it["groups"]),
        "n_multi_chunk_groups": sum(
            1 for it in items.values() if any(len(g) > 1 for g in it["groups"])
        ),
    }


def load_index_chunks(persist_dir: Path, collection: str, page: int = 5000) -> list[tuple[str, str]]:
    """Every (chunk_id, normalised text) in the index.  Paged: Chroma caps one get()."""
    import chromadb

    col = chromadb.PersistentClient(path=str(persist_dir)).get_collection(collection)
    n = col.count()
    out: list[tuple[str, str]] = []
    offset = 0
    while offset < n:
        res = col.get(include=["documents", "metadatas"], limit=page, offset=offset)
        for doc, meta in zip(res["documents"], res["metadatas"]):
            out.append((chunk_id(meta["ticker"], meta["chunk_idx"]), normalize(doc)))
        offset += page
    logger.info("Loaded %d chunks from %s/%s", len(out), persist_dir, collection)
    return out


def scorable_groups(labels: dict, item_id: str) -> list[list[str]]:
    """Relevant groups for *item_id*, or [] when the item has no usable label."""
    return list((labels.get("items", {}).get(item_id) or {}).get("groups") or [])


def load_labels(path: Path = LABELS_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    p = argparse.ArgumentParser(description="Label benchmark items with the chunk ids that hold their reference passages.")
    p.add_argument("--benchmark", type=Path, default=BENCHMARK_FILE)
    p.add_argument("--out", type=Path, default=LABELS_FILE)
    p.add_argument("--overrides", type=Path, default=OVERRIDES_FILE)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from ingestion.embedder import CHROMA_PERSIST_DIR, COLLECTION_NAME

    with open(args.benchmark, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    overrides = {}
    if args.overrides.exists():
        with open(args.overrides, encoding="utf-8") as f:
            overrides = json.load(f)
    chunks = load_index_chunks(CHROMA_PERSIST_DIR, COLLECTION_NAME)

    items = label_benchmark(rows, chunks, overrides)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "benchmark_file": str(args.benchmark.relative_to(REPO_ROOT)).replace("\\", "/"),
        "benchmark_sha256": file_sha256(args.benchmark),
        "index_collection": COLLECTION_NAME,
        "index_chunk_count": len(chunks),
        "prefix_chars": PREFIX_CHARS,
        "summary": summarize(items),
        "items": items,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(json.dumps(payload["summary"], indent=2))
    unresolved = [i for i, it in items.items() if it["method"] in ("none", "partial")]
    if unresolved:
        print(f"UNRESOLVED ({len(unresolved)}): {', '.join(unresolved)} — add them to {args.overrides.name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
