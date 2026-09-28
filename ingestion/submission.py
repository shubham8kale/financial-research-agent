# ingestion/submission.py
#
# PURPOSE
# -------
# The one place that knows the shape of an EDGAR full-submission.txt: an SGML
# envelope of <DOCUMENT> blocks, each with a <TYPE> (10-K, EX-21.1, EX-101.SCH,
# XML, GRAPHIC, ...) and a <TEXT> body.  The 10-K itself is one of those
# documents; the rest are exhibits, the XBRL taxonomy files, the XBRL
# instance (whose text blocks are HTML-escaped copies of the notes) and
# images.  Both the text cleaner and the XBRL parser need only the 10-K, and
# before this module the cleaner read the whole envelope: 82% of the index it
# built was not 10-K prose (eval/EVALUATION.md, finding 22).

import re

_DOC_RE = re.compile(r"<DOCUMENT>\s*<TYPE>([^\n<]+)\n(.*?)</DOCUMENT>", re.S)
_TEXT_RE = re.compile(r"<TEXT>(.*?)</TEXT>", re.S)


def documents(submission_text: str) -> list[tuple[str, str]]:
    """Every (type, text body) in the submission, in file order."""
    out = []
    for doc_type, body in _DOC_RE.findall(submission_text):
        m = _TEXT_RE.search(body)
        out.append((doc_type.strip(), m.group(1) if m else body))
    return out


def primary_document(submission_text: str) -> str:
    """The text body of the first <DOCUMENT> whose <TYPE> is 10-K (the inline XBRL document).

    Raises ValueError when the submission holds no 10-K document.
    """
    for doc_type, body in documents(submission_text):
        if doc_type.upper().startswith("10-K"):
            return body
    raise ValueError("no 10-K document found in submission")


def primary_document_or_all(text: str) -> tuple[str, int]:
    """(the 10-K body, number of other documents dropped) — or (text, 0) when there is no envelope.

    A plain HTML file without <DOCUMENT> blocks (tests, a saved .htm) is
    cleaned as it is; a real submission is reduced to its 10-K.
    """
    docs = documents(text)
    if not docs:
        return text, 0
    return primary_document(text), len(docs) - 1
