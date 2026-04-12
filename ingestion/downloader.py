# ingestion/downloader.py
#
# PURPOSE
# -------
# Download annual 10-K filings for a set of publicly traded companies from the
# SEC's EDGAR system and save them to disk so the rest of the pipeline can
# ingest, chunk, and embed them.
#
# WHY 10-K FILINGS?
# -----------------
# A 10-K is the comprehensive annual report that every US-listed company is
# legally required to file with the Securities and Exchange Commission (SEC).
# It contains:
#   • Business overview (products, markets, competitive landscape)
#   • Risk factors (what management thinks could hurt the business)
#   • MD&A – Management's Discussion and Analysis of financial results
#   • Audited financial statements (income statement, balance sheet, cash flow)
#   • Notes to the financial statements (accounting policies, segment detail)
#
# This makes 10-Ks the *most information-dense* public document a company
# produces, and therefore the highest-value source for a financial research
# agent. Unlike earnings call transcripts or press releases, 10-Ks are:
#   - Standardised in structure across all filers (same sections every year)
#   - Audited and legally certified (false statements are federal crimes)
#   - Freely available on EDGAR with no paywall
#
# WHY SEC-EDGAR-DOWNLOADER?
# -------------------------
# The raw EDGAR API is powerful but tedious: you must navigate CIK lookups,
# filing index pages, and individual document URLs. `sec-edgar-downloader`
# abstracts all of that into a two-line call while respecting SEC's rate-limit
# guidelines (10 requests/second, proper User-Agent header required).

import os
import logging
from pathlib import Path

from dotenv import load_dotenv
from sec_edgar_downloader import Downloader

# Load environment variables from .env so secrets stay out of source code.
load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────
# Using the standard library logger (not print) so the calling code can
# control log level, add handlers, or redirect output without editing this file.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# The five companies that represent the largest constituents of the S&P 500
# tech / communication sector.  Their 10-Ks together give the agent a rich
# corpus covering cloud computing, advertising, e-commerce, hardware, and
# social media — enough variety to answer diverse financial questions.
TARGET_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"]

# How many years of 10-Ks to pull per company.
# One year is enough to bootstrap the agent. Increase this if you want
# multi-year trend analysis (e.g. revenue CAGR, margin expansion over time).
NUM_FILINGS = 1

# Where to persist the downloaded filings.  Using a top-level `data/` directory
# (outside the `ingestion/` package) keeps raw data separate from code, which
# is important for:
#   1. .gitignore-ing large binary/text blobs without touching source files
#   2. Allowing the data directory to live on a separate volume in production
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "sec_filings"

# SEC requires a descriptive User-Agent to identify your application and
# contact email.  Omitting it (or using a generic string) risks IP-level
# throttling.  See: https://www.sec.gov/os/webmaster-tips
SEC_USER_AGENT_COMPANY = "FinancialResearchAgent"
SEC_USER_AGENT_EMAIL = os.getenv("SEC_USER_AGENT_EMAIL", "research@example.com")


def download_filings(
    tickers: list[str] = TARGET_TICKERS,
    num_filings: int = NUM_FILINGS,
    output_dir: Path = DATA_DIR,
) -> dict[str, Path]:
    """Download 10-K filings for each ticker and return a mapping of
    ticker -> directory where the filings were saved.

    Parameters
    ----------
    tickers:
        List of stock ticker symbols to download filings for.
    num_filings:
        Number of the most-recent 10-K filings to fetch per ticker.
        The SEC EDGAR downloader returns filings in reverse chronological
        order, so num_filings=1 always gives the latest annual report.
    output_dir:
        Root directory under which per-ticker subdirectories are created.

    Returns
    -------
    dict mapping each ticker symbol to the Path where its filings live.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialise the downloader with a company name and email as required by
    # the SEC's Fair Access policy.  All downloaded files are written to
    # `output_dir` automatically by the library.
    dl = Downloader(
        company_name=SEC_USER_AGENT_COMPANY,
        email_address=SEC_USER_AGENT_EMAIL,
        download_folder=str(output_dir),
    )

    ticker_paths: dict[str, Path] = {}

    for ticker in tickers:
        logger.info("Downloading %d 10-K filing(s) for %s …", num_filings, ticker)
        try:
            # `get` fetches the specified form type ("10-K") for the given
            # ticker.  The library handles:
            #   - Resolving the ticker to a CIK (Central Index Key, SEC's
            #     internal company identifier)
            #   - Paginating through the filing index
            #   - Downloading the primary document and all exhibits
            dl.get("10-K", ticker, limit=num_filings)

            # The library saves files under:
            #   <output_dir>/sec-edgar-filings/<TICKER>/10-K/
            ticker_dir = output_dir / "sec-edgar-filings" / ticker / "10-K"
            ticker_paths[ticker] = ticker_dir
            logger.info("Saved %s filings to %s", ticker, ticker_dir)

        except Exception as exc:
            # Log and continue rather than aborting the whole batch — a single
            # company's filing being unavailable shouldn't block the others.
            logger.error("Failed to download 10-K for %s: %s", ticker, exc)

    return ticker_paths


if __name__ == "__main__":
    # Allow running this module directly for quick manual testing:
    #   python -m ingestion.downloader
    paths = download_filings()
    for ticker, path in paths.items():
        print(f"{ticker}: {path}")
