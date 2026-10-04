"""Claims checker: a draft may only state what the evidence register (config/evidence.toml) allows.

It refuses a draft that
  - contains a number that is not in an allowed claim, the opportunity's own text or the proposed price,
  - links to a repository that is not on the safe-evidence list,
  - names the current employer (compared as hashes),
  - uses invented social proof ("hundreds of clients", "guaranteed", ...),
  - opens with the owner instead of the client's problem,
  - is a cold message without the opt-out line, or is too long to be read on a phone.
"""
from __future__ import annotations

import re

from .config import Config, name_hash
from .scoring import _ngrams

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUM = re.compile(r"\d[\d,.]*")
_LINK = re.compile(r"(https?://|www\.|github\.com/)\S+|\S+@\S+\.\w+", re.I)
_REPO_LINK = re.compile(r"github\.com/coolman1984/([A-Za-z0-9_.\-]+)", re.I)
BOASTS = ["hundreds of clients", "many clients", "dozens of clients", "satisfied clients", "guarantee", "guaranteed",
          "100% success", "top rated", "top-rated", "award-winning", "world-class", "best in the market",
          "عملاء كثير", "مئات العملاء", "نضمن", "مضمون", "الأفضل في السوق", "عملائي الكثيرين"]
SELF_OPENERS = re.compile(r"^\s*(hi[,!]?\s+)?(i am|i'm|my name|i have|we are|أنا|انا|اسمي|معك|معاك)\b", re.I)
OPT_OUT_MARKERS = ["reply \"stop\"", "reply stop", "won't contact you again", "will not contact you again",
                   "لو مش عايز", "لن أتواصل", "مش هتواصل", "مش هبعت تاني", "ابعت \"لا\""]
MAX_CHARS = {"job": 2200, "freelance": 1800, "direct": 1200}


def numbers(text: str) -> set[str]:
    """Numbers stated in prose. Digits inside links and e-mail addresses are not claims (links are checked
    separately against the safe-evidence list)."""
    out = set()
    for raw in _NUM.findall(_LINK.sub(" ", (text or "").translate(_DIGITS))):
        n = raw.rstrip(".,").replace(",", "")
        if n:
            out.add(n.rstrip("0").rstrip(".") if "." in n else n)
    return out


def allowed_numbers(cfg: Config, opp: dict, extra: list[str] = ()) -> set[str]:
    texts = list(cfg.evidence["owner"]["claims"]) + list(extra)
    for ev in cfg.evidence["evidence"].values():
        texts += ev.get("claims", []) + [ev.get("line_en", ""), ev.get("line_ar", ""), ev.get("name_en", ""),
                                         ev.get("name_ar", "")]
    texts += [opp.get("title", ""), opp.get("description") or "", opp.get("org") or ""]
    allowed = set()
    for t in texts:
        allowed |= numbers(t)
    return allowed | {"1", "2", "3", "20", "30"}  # small counts used in plain next steps ("a 20-minute call")


def check(cfg: Config, opp: dict, text: str, *, extra_allowed: list[str] = ()) -> list[str]:
    problems = []
    body = text or ""
    bad_nums = sorted(numbers(body) - allowed_numbers(cfg, opp, extra_allowed))
    if bad_nums:
        problems.append(f"numbers not backed by evidence: {', '.join(bad_nums)}")
    allowed_repos = {r.lower() for r in cfg.evidence["owner"]["allowed_projects"]}
    for repo in _REPO_LINK.findall(body):
        if repo.lower().rstrip(".") not in allowed_repos:
            problems.append(f"repository not on the safe-evidence list: {repo}")
    employer = set(cfg.blocklist["employer"]["hashes"])
    if {name_hash(g) for g in _ngrams(body)} & employer:
        problems.append("mentions the current employer")
    low = body.lower()
    for b in BOASTS:
        if b in low:
            problems.append(f"unsupported claim: '{b}'")
    first = next((ln for ln in body.splitlines() if ln.strip() and not ln.lower().startswith(("subject:", "الموضوع:"))), "")
    first = re.sub(r"^(hi|hello|dear|أهلًا|اهلا|أهلا|مرحبًا|مرحبا|السلام عليكم)[^,،\n]*[,،]?\s*", "", first.strip(), flags=re.I)
    if SELF_OPENERS.match(first):
        problems.append("opens with the owner; open with the client's problem instead")
    if opp["track"] == "direct" and not any(m in low for m in OPT_OUT_MARKERS):
        problems.append("cold message without an opt-out line")
    limit = MAX_CHARS[opp["track"]]
    if len(body) > limit:
        problems.append(f"too long for a phone ({len(body)} > {limit} characters)")
    return problems
