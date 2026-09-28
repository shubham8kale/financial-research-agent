# agent/contract.py
#
# PURPOSE
# -------
# The output contract, and the fail-closed check that stands between the
# agent's draft and the response.  The agent's final message is prose.  This
# module turns it into a record — one claim per sentence, each with the ids
# of the observations that support it — and then verifies that record
# against what the agent actually retrieved this turn, with no model in the
# loop:
#
#   unknown_source      a cited id is not among this turn's observations
#   uncited_figure      a sentence states a figure and cites nothing
#   unsupported_figure  a figure in a sentence is not in any observation it cites
#   dropped_figure      a figure in the draft appears in no claim (the record lost it)
#   no_contract         the structuring call returned nothing usable
#
# One repair attempt — the structuring model is shown the failures — then
# the answer is refused: in strict mode the API serves a refusal that names
# what it could not verify rather than the draft; in warn mode the draft is
# served with the verdict attached; off skips the whole step and serves the
# draft, which is what the system did before this module existed.
#
# WHAT "SUPPORTED" MEANS
# ----------------------
# A figure counts as present in a cited observation under the rules the
# evaluation's figure check uses (agent/figures.py), read in the direction a
# verifier needs: the claim may round what the source says ("$26.4 billion"
# for "$26,448 million"), but it may not be more precise than the source,
# and a bare integer must match exactly.  Years are exempt — a passage that
# states a figure states its period in prose the check does not parse — so
# this check catches an invented or mis-transcribed figure and a figure done
# in the model's head; it does not catch a right figure attributed to the
# wrong year, which is the figure check's job (finding 2) and the fact tool's.
#
# The structuring call is a second, cheaper model call: question, draft and
# the observations with their ids — no tool schemas, no trajectory — and it
# is metered like the first, so the cost of verifying shows in every meter.

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from pydantic import BaseModel, Field

from agent.figures import content_figures, extract_figures, matches
from agent.observations import parse_observation

logger = logging.getLogger(__name__)

MODES = ("strict", "warn", "off")
MAX_ATTEMPTS = 2   # one structuring call, one repair


def verify_mode() -> str:
    """strict (default): refuse what cannot be verified; warn: serve it flagged; off: skip."""
    mode = (os.getenv("VERIFY_MODE") or "strict").strip().lower()
    if mode not in MODES:
        raise ValueError(f"VERIFY_MODE must be one of {MODES}, got {mode!r}")
    return mode


# ── The contract ────────────────────────────────────────────────────────────

class Claim(BaseModel):
    text: str = Field(description="One sentence of the draft answer, copied verbatim.")
    sources: list[str] = Field(
        default_factory=list,
        description=(
            "Ids of the observations that support this sentence, copied exactly from the observation "
            "list: AAPL_10K_chunk_12 is a passage, AMZN_10K_fact_3 a tagged fact, calc_pct_change a "
            "calculator result. A sentence that states a figure must cite the observation that contains "
            "that figure. Leave empty only for a sentence that states no fact."
        ),
    )


class AnswerContract(BaseModel):
    claims: list[Claim] = Field(description="Every sentence of the draft answer, in order, each with its sources.")
    not_disclosed: bool = Field(
        default=False,
        description="True when the draft says the filings do not contain the information asked for.",
    )


STRUCTURING_PROMPT = (
    "You turn a draft answer about SEC 10-K filings into a verifiable record. Split the draft into "
    "its sentences and copy each one verbatim: do not add, remove, merge or reword a sentence. For "
    "each sentence list the ids of the observations that support it, using only ids from the "
    "observation list. A sentence that states a figure must cite the observation that contains that "
    "figure; a figure the calculator produced cites the calc id. Set not_disclosed to true only when "
    "the draft says the filings do not contain the information."
)

# Hashed into every results file's config: a change to the prompt or the schema
# is a different contract and cannot share a cache entry or a config hash.
CONTRACT_VERSION = hashlib.sha256(
    (STRUCTURING_PROMPT + json.dumps(AnswerContract.model_json_schema(), sort_keys=True)).encode("utf-8")
).hexdigest()[:12]


@dataclass
class Verification:
    status: str                      # verified | unverified | refused | skipped
    mode: str
    n_claims: int = 0
    n_figures: int = 0               # figures across the claims, years excluded
    n_supported: int = 0
    failures: list[dict] = field(default_factory=list)   # {"check", "claim", "detail"}
    cited: list[str] = field(default_factory=list)       # observation ids some claim cites, that exist
    not_disclosed: bool = False
    attempts: int = 0
    repaired: bool = False           # verified only on the second attempt
    error: str | None = None
    contract_version: str = CONTRACT_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


# ── Observations ────────────────────────────────────────────────────────────

def observations_by_id(observations: list[str]) -> dict[str, str]:
    """Every passage, fact and calculator result the agent saw this turn, keyed by id, in order."""
    out: dict[str, str] = {}
    for obs in observations:
        for c in parse_observation(obs):
            out[c.chunk_id] = f"{out[c.chunk_id]}\n{c.text}" if c.chunk_id in out else c.text
    return out


def render_observations(obs: dict[str, str]) -> str:
    return "\n\n".join(f"[{cid}] {text}" for cid, text in obs.items())


def describe(failure: dict) -> str:
    check, detail, claim = failure.get("check"), failure.get("detail"), failure.get("claim") or ""
    where = f' in "{claim[:120]}"' if claim else ""
    return {
        "unsupported_figure": f"the figure {detail}{where} is not in any observation the sentence cites",
        "uncited_figure": f"the figure {detail}{where} cites no source",
        "unknown_source": f"{detail} is cited{where} but is not an observation from this turn",
        "dropped_figure": f"the figure {detail} is in the draft but in no sentence of the record",
        "no_contract": f"no usable record was produced ({detail})",
    }.get(check, f"{check}: {detail}")


def build_messages(question: str, draft: str, obs: dict[str, str], failures: list[dict] | None = None) -> list:
    human = (f"Question: {question}\n\nDraft answer:\n{draft}\n\nObservations:\n{render_observations(obs)}")
    if failures:
        human += ("\n\nYour previous record failed verification:\n"
                  + "\n".join(f"- {describe(f)}" for f in failures)
                  + "\nProduce a corrected record. Every figure must cite an observation that contains it; "
                    "keep every sentence verbatim.")
    return [("system", STRUCTURING_PROMPT), ("human", human)]


# ── The deterministic checks ────────────────────────────────────────────────

def supported(figure, source_figures) -> bool:
    """The claim's figure is what a source states, possibly rounded — never more precise than the source.

    A percentage in a claim may be written without its sign in the source: the
    calculator prints "pct_change ... = 19.68" and a table column headed "%"
    holds bare numbers.  So a percent claim is also supported by a plain,
    unscaled source figure of the same value; a money figure never is.
    """
    if any(matches(figure, g) for g in source_figures):
        return True
    if figure.percent:
        plain = replace(figure, percent=False)
        return any(not g.percent and g.scaled == g.unscaled and matches(plain, g) for g in source_figures)
    return False


def check_contract(contract: AnswerContract, draft: str, obs: dict[str, str]) -> tuple[list[dict], dict]:
    """Every way the record fails to verify against *obs*, and the counts behind the verdict."""
    failures: list[dict] = []
    n_figures = n_supported = 0
    for claim in contract.claims:
        figures = content_figures(claim.text)
        for s in claim.sources:
            if s not in obs:
                failures.append({"check": "unknown_source", "claim": claim.text, "detail": s})
        known = [s for s in claim.sources if s in obs]
        n_figures += len(figures)
        if figures and not claim.sources:
            failures += [{"check": "uncited_figure", "claim": claim.text, "detail": f.raw} for f in figures]
            continue
        source_figures = [g for s in known for g in extract_figures(obs[s])]
        for f in figures:
            if supported(f, source_figures):
                n_supported += 1
            else:
                failures.append({"check": "unsupported_figure", "claim": claim.text, "detail": f.raw})
    claim_figures = extract_figures(" ".join(c.text for c in contract.claims))
    for f in content_figures(draft):
        if not any(matches(f, g) or matches(g, f) for g in claim_figures):
            failures.append({"check": "dropped_figure", "claim": "", "detail": f.raw})
    return failures, {"n_claims": len(contract.claims), "n_figures": n_figures, "n_supported": n_supported}


def figure_grounding(text: str, observations: list[str]) -> dict:
    """Judge-free, citation-free: are the figures in *text* present in ANYTHING the agent saw?

    Weaker than verification (no citation is required) and computable on any
    stored record, so old results files can be scored with it.  A figure that
    is in no observation was invented, mis-transcribed or computed in the
    model's head.
    """
    figures = content_figures(text)
    if not figures:
        return {"applicable": False, "n_figures": 0, "n_grounded": 0, "grounded": None, "ungrounded": []}
    seen = [g for obs in observations for g in extract_figures(obs)]
    ungrounded = [f.raw for f in figures if not supported(f, seen)]
    return {
        "applicable": True,
        "n_figures": len(figures),
        "n_grounded": len(figures) - len(ungrounded),
        "grounded": not ungrounded,
        "ungrounded": ungrounded,
    }


def refusal_text(failures: list[dict]) -> str:
    """What the API serves instead of a draft it could not verify."""
    reasons = [describe(f) for f in failures[:4]]
    more = f"; and {len(failures) - 4} more" if len(failures) > 4 else ""
    return ("I could not verify my draft answer against the filings, so I am not serving it: "
            + "; ".join(reasons) + more
            + ". Try asking for the specific figure, or narrow the question to one company and one fiscal year.")


# ── The structuring call, with one repair ───────────────────────────────────

def get_llm():
    from agent.financial_agent import build_llm
    return build_llm()


def _unpack(result) -> tuple[AnswerContract | None, Any, str | None]:
    """A with_structured_output(include_raw=True) result → (contract, raw AI message, error)."""
    if isinstance(result, dict):
        err = result.get("parsing_error")
        return result.get("parsed"), result.get("raw"), (f"{type(err).__name__}: {err}" if err else None)
    return result, None, None


def verify_answer(question: str, draft: str, observations: list[str], llm=None, mode: str | None = None,
                  callbacks: list | None = None) -> tuple[str, Verification, list]:
    """Structure, verify, repair once, then refuse (strict) or flag (warn).

    Returns (served answer, verification, extra AI messages) — the extra
    messages are the structuring calls' raw responses, carrying their token
    usage, so a caller can meter them alongside the agent's own messages.
    """
    mode = mode or verify_mode()
    if mode == "off":
        return draft, Verification(status="skipped", mode=mode), []

    obs = observations_by_id(observations)
    structured = (llm or get_llm()).with_structured_output(AnswerContract, include_raw=True)
    config = {"callbacks": callbacks} if callbacks else None
    extra: list = []
    contract: AnswerContract | None = None
    failures: list[dict] = []
    stats = {"n_claims": 0, "n_figures": 0, "n_supported": 0}
    error: str | None = None
    attempts = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts = attempt
        messages = build_messages(question, draft, obs, failures if attempt > 1 else None)
        try:
            candidate, raw, error = _unpack(structured.invoke(messages, config=config))
        except Exception as exc:  # noqa: BLE001 — a failed structuring call is a verification failure, not a crash
            candidate, raw, error = None, None, f"{type(exc).__name__}: {exc}"
            logger.warning("Structuring call failed on attempt %d: %s", attempt, error)
        if raw is not None:
            extra.append(raw)
        if candidate is None:
            failures = [{"check": "no_contract", "claim": "", "detail": error or "empty response"}]
            continue
        contract = candidate
        failures, stats = check_contract(contract, draft, obs)
        if not failures:
            break

    verification = Verification(
        status="verified" if contract is not None and not failures else "unverified",
        mode=mode,
        failures=failures,
        cited=sorted({s for c in (contract.claims if contract else []) for s in c.sources if s in obs}),
        not_disclosed=bool(contract and contract.not_disclosed),
        attempts=attempts,
        repaired=(attempts > 1 and not failures and contract is not None),
        error=error,
        **stats,
    )
    served = draft
    if verification.status != "verified" and mode == "strict":
        verification.status = "refused"
        served = refusal_text(verification.failures)
    logger.info("Verification: %s after %d attempt(s); %d/%d figures supported; %d failure(s)",
                verification.status, attempts, verification.n_supported, verification.n_figures, len(failures))
    return served, verification, extra


async def averify_answer(question: str, draft: str, observations: list[str], llm=None, mode: str | None = None,
                         callbacks: list | None = None) -> tuple[str, Verification, list]:
    """verify_answer off the event loop: the structuring call is a blocking HTTP round trip."""
    return await asyncio.to_thread(verify_answer, question, draft, observations, llm, mode, callbacks)
