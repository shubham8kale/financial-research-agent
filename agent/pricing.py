# agent/pricing.py
#
# PURPOSE
# -------
# Turn token counts into dollars, from a price table this repository owns.
# A vendor dashboard can change its cost table under a stable model alias;
# a number in a results file must not move when that happens, so the price
# used is recorded here, dated, and applied by our code.
#
# Prices are USD per one million tokens, paid tier, text input, as published
# at https://ai.google.dev/gemini-api/docs/pricing and read on 2026-09-27.
# The Gemini 3.6/3.7/3.8 Flash prices are the introductory rate valid
# through 2026-12-31; the table must be re-read when it lapses.

PRICE_TABLE_DATE = "2026-09-27"

# model id -> (input $/M tokens, output $/M tokens)
PRICES_USD_PER_M: dict[str, tuple[float, float]] = {
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.8-flash": (0.75, 3.75),
}


def price(model: str | None) -> tuple[float, float] | None:
    """(input, output) $/M tokens for *model*, tolerant of provider prefixes; None when unknown."""
    if not model:
        return None
    key = model.split("/")[-1].lower()
    if key in PRICES_USD_PER_M:
        return PRICES_USD_PER_M[key]
    for name, p in PRICES_USD_PER_M.items():   # "gemini-3.1-flash-lite-001" style suffixes
        if key.startswith(name):
            return p
    return None


def cost_usd(model: str | None, input_tokens: int, output_tokens: int) -> float | None:
    """Dollar cost of a call, or None when the model's price is not in the table (never a guess)."""
    p = price(model)
    if p is None:
        return None
    return round((input_tokens * p[0] + output_tokens * p[1]) / 1_000_000, 6)
