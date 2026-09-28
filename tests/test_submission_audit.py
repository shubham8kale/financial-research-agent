# tests/test_submission_audit.py
#
# The EDGAR envelope parser (ingestion/submission.py), the cleaner's use of it
# (only the 10-K document is cleaned; the inline XBRL header is dropped) and
# the index audit's classifier (ingestion/audit.py).  Synthetic submissions,
# no filings, no index.

from pathlib import Path

import pytest

from ingestion import audit, submission
from ingestion.cleaner import clean_filing

TEN_K = (
    "<html><body>"
    '<div style="display:none;"><ix:header><ix:hidden>'
    '<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="C_1">0000789019</ix:nonNumeric>'
    "</ix:hidden><ix:resources>"
    '<xbrli:context id="C_1"><xbrli:period><xbrli:instant>2025-06-30</xbrli:instant></xbrli:period></xbrli:context>'
    "<xbrldi:explicitMember>us-gaap:CommonStockMember</xbrldi:explicitMember>"
    "</ix:resources></ix:header></div>"
    "<p>SECURITIES AND EXCHANGE COMMISSION</p>"
    "<p>Microsoft reported revenue of $281,724 million for the fiscal year ended June 30, 2025.</p>"
    "</body></html>"
)
INSTANCE_XML = (
    "<xbrl><us-gaap:RevenueTextBlock>&lt;table&gt;&lt;tr&gt;&lt;td style=\"width:10%\"&gt;Escaped copy of the note"
    " with revenue of $281,724 million&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;</us-gaap:RevenueTextBlock></xbrl>"
)


def _submission(*docs: tuple[str, str]) -> str:
    return "<SEC-HEADER>\nCONFORMED NAME: MICROSOFT\n</SEC-HEADER>\n" + "".join(
        f"<DOCUMENT>\n<TYPE>{t}\n<SEQUENCE>1\n<FILENAME>f.htm\n<TEXT>\n{body}\n</TEXT>\n</DOCUMENT>\n" for t, body in docs
    )


def test_documents_and_primary_document():
    text = _submission(("EX-21", "<p>subsidiaries</p>"), ("10-K", TEN_K), ("XML", INSTANCE_XML))
    docs = submission.documents(text)
    assert [t for t, _ in docs] == ["EX-21", "10-K", "XML"]
    assert "SECURITIES AND EXCHANGE COMMISSION" in submission.primary_document(text)
    assert "subsidiaries" not in submission.primary_document(text)
    assert submission.primary_document_or_all(text) == (submission.primary_document(text), 2)
    with pytest.raises(ValueError):
        submission.primary_document(_submission(("EX-21", "<p>x</p>")))


def test_a_file_without_an_envelope_is_used_whole():
    assert submission.primary_document_or_all("<html><p>plain</p></html>") == ("<html><p>plain</p></html>", 0)


def test_clean_filing_keeps_only_the_10k_document(tmp_path: Path):
    f = tmp_path / "full-submission.txt"
    f.write_text(_submission(("10-K", TEN_K), ("EX-21", "<p>List of subsidiaries of the registrant</p>"), ("XML", INSTANCE_XML)),
                 encoding="latin-1")
    out = clean_filing(f)
    assert "Microsoft reported revenue of $281,724 million" in out
    assert "Escaped copy" not in out and "<td" not in out and "&lt;" not in out     # the XBRL instance is gone
    assert "subsidiaries" not in out                                                  # so are the exhibits
    assert "CONFORMED NAME" not in out


def test_clean_filing_drops_the_inline_xbrl_header(tmp_path: Path):
    f = tmp_path / "full-submission.txt"
    f.write_text(_submission(("10-K", TEN_K)), encoding="latin-1")
    out = clean_filing(f)
    assert out.startswith("SECURITIES AND EXCHANGE COMMISSION")
    for leaked in ("0000789019", "2025-06-30", "CommonStockMember", "C_1"):
        assert leaked not in out, leaked


# ── the audit's classifier ───────────────────────────────────────────────────

PROSE = ("Total net sales increased 6% or $25.1 billion during 2025 compared to 2024 due primarily to higher net sales of "
         "iPhone, Services and Mac, partially offset by lower net sales of iPad and Wearables, Home and Accessories.")
TABLE = ("Net sales by category: iPhone $ 209,586 $ 201,183 $ 200,583 Mac 33,708 29,984 29,357 iPad 28,015 26,694 28,300 "
         "Wearables, Home and Accessories 35,896 37,005 39,845 Services 109,158 96,169 85,200 Total net sales $ 416,161")


def test_classify_names_each_kind():
    assert audit.classify(PROSE) == "prose"
    assert audit.classify(TABLE) in ("prose", "numeric table"), "a financial table is content, not junk"
    assert audit.classify("Revenue $ 106,265 $ 87,464 21% Cost of revenue 40,171 29,611 36% Gross margin 66,094 57,853 14% "
                          "Operating income 41,391 33,455 24% " * 2) == "numeric table"
    assert audit.classify('<td style="width:10%"> Escaped copy of the note with revenue ' * 4) == "escaped markup"
    assert audit.classify("Details 70 false false All Reports Book aapl-20250927.htm aapl-20250927.xsd aapl-20250927_cal.xml "
                          "aapl-20250927_def.xml aapl-20250927_lab.xml") == "json / metadata"
    assert audit.classify("2004 2005 2006 2007 http://fasb.org/srt/2024#ChiefExecutiveOfficerMember 0000789019 "
                          "srt:MaximumMember us-gaap:ComputerEquipmentMember us-gaap:CommonStockMember 2025-06-30 "
                          "C_7102029d-8d6c-44c5-b166-04cf41443b1e") == "identifiers"
    assert audit.classify("Markets and Distribution") == "short fragment"


def test_audit_counts_overall_and_per_ticker():
    report = audit.audit([("AAPL", PROSE), ("AAPL", "Markets and Distribution"), ("MSFT", '<td style="x">' + PROSE)])
    assert report["n_chunks"] == 3
    assert report["overall"]["prose"] == 1 and report["overall"]["escaped markup"] == 1
    assert report["per_ticker"]["AAPL"] == {
        "n_chunks": 2, "prose": 1, "numeric table": 0, "escaped markup": 0,
        "identifiers": 0, "json / metadata": 0, "short fragment": 1,
    }
    text = audit.render(report)
    assert "| AAPL | 2 |" in text and "| escaped markup | 1 |" in text
