# eval/experiment.py
#
# PURPOSE
# -------
# The bookkeeping that makes an evaluation run an EXPERIMENT rather than a
# number: a stable hash of everything that could change the result, a version
# stamp for the benchmark, and a lookup that finds a prior run of the identical
# configuration so it is reported instead of re-bought.
#
# WHAT GOES INTO THE HASH
# -----------------------
# Only inputs that change the outcome: models, embedding model, k, prompt
# version, benchmark version, metric set, retrieval configuration, RAGAS
# version and the results schema.  NOT the run label, timestamp or git commit —
# two runs of the same configuration on different days must hash the same, or
# the "already run" check would never fire.

import hashlib
import json
from pathlib import Path


def canonical_json(obj) -> str:
    """Deterministic JSON: sorted keys, no whitespace, non-ASCII preserved."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def config_hash(config: dict, length: int = 12) -> str:
    """SHA-256 of the canonical JSON of *config*, truncated to *length* hex chars."""
    digest = hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()
    return digest[:length]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def benchmark_version(*paths: Path, length: int = 12) -> str:
    """One version string over the benchmark file(s): the CSV and its chunk labels.

    Any edit to a question, a ground truth or a label changes the version, so a
    results file can never be compared against a benchmark it was not run on
    without the mismatch being visible.
    """
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).name.encode("utf-8"))
        h.update(b"\0")
        h.update(file_sha256(Path(p)).encode("utf-8"))
        h.update(b"\0")
    return f"sha256:{h.hexdigest()[:length]}"


def find_existing_result(results_dir: Path, cfg_hash: str) -> Path | None:
    """Path of a COMPLETE results file whose config hash equals *cfg_hash*, else None.

    Partial runs are ignored on purpose: a stopped run must not block a re-run
    that would finish the job.
    """
    if not results_dir.exists():
        return None
    for path in sorted(results_dir.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        cfg = payload.get("config") or {}
        status = payload.get("run_status") or {}
        if cfg.get("config_hash") == cfg_hash and status.get("complete"):
            return path
    return None
