"""Turn any raw item (API row, RSS entry, connector result, email alert line) into one standard record,
and merge the same opportunity seen in several sources."""
from __future__ import annotations

import hashlib
import html
import re
import urllib.parse
from datetime import datetime, timezone

from .config import normalise
from .store import Store

TRACK_PREFIX = {"job": "J", "freelance": "F", "direct": "D"}
_TRACKING = re.compile(r"^(utm_|ref$|refid$|trk|trackingid|src$|source$|fbclid|gclid)", re.I)
_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str | None) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def canonical_url(url: str | None) -> str:
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url.strip())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if not _TRACKING.match(k)]
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"),
                                    urllib.parse.urlencode(query), ""))


def detect_language(text: str) -> str:
    arabic = len(re.findall(r"[؀-ۿ]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return "ar" if arabic > latin * 0.3 and arabic > 20 else "en"


def parse_date(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z", "%B %d, %Y"):
        try:
            return datetime.strptime(text.replace("Z", "+0000") if "%z" in fmt else text, fmt).date().isoformat()
        except ValueError:
            continue
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


def fingerprint(org: str | None, title: str) -> str:
    return hashlib.sha1(f"{normalise(org or '')}|{normalise(title)}".encode("utf-8")).hexdigest()


def make_record(*, track: str, title: str, org: str | None = None, location: str | None = None,
                remote: bool = False, url: str | None = None, description: str | None = None,
                posted_at=None, budget_min=None, budget_max=None, currency: str | None = None,
                contact: str | None = None) -> dict:
    if track not in TRACK_PREFIX:
        raise ValueError(f"unknown track '{track}'")
    title = strip_html(title)[:300]
    if not title:
        raise ValueError("an opportunity needs a title")
    desc = strip_html(description)[:8000]
    fp = fingerprint(org, title)
    return {
        "id": f"{TRACK_PREFIX[track]}-{fp[:10]}",
        "track": track,
        "title": title,
        "org": strip_html(org)[:200] or None,
        "location": strip_html(location)[:200] or None,
        "remote": 1 if remote or re.search(r"\bremote\b|عن بعد|عن بُعد", f"{location} {title}", re.I) else 0,
        "url": canonical_url(url) or None,
        "description": desc,
        "posted_at": parse_date(posted_at),
        "budget_min": _num(budget_min),
        "budget_max": _num(budget_max),
        "currency": currency,
        "language": detect_language(f"{title} {desc}"),
        "fingerprint": fp,
        "contact": contact,
    }


def _num(v):
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _tokens(text: str) -> set[str]:
    return {t for t in normalise(text).split() if len(t) > 2}


def similar(a: dict, b: dict) -> bool:
    if a["url"] and b["url"] and a["url"] == b["url"]:
        return True
    if normalise(a.get("org") or "") != normalise(b.get("org") or "") or not a.get("org"):
        return False
    ta, tb = _tokens(a["title"]), _tokens(b["title"])
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.8


def upsert(store: Store, record: dict, source: str, source_url: str | None = None) -> tuple[str, bool]:
    """Insert a record or merge it into the existing one. Returns (id, created)."""
    existing = store.find_by_fingerprint(record["fingerprint"])
    if not existing:
        for cand in store.all("track=? AND lower(coalesce(org,''))=lower(?)", (record["track"], record.get("org") or "")):
            if similar(cand, record):
                existing = cand
                break
    if existing:
        fill = {k: v for k, v in record.items()
                if k in ("description", "budget_min", "budget_max", "currency", "posted_at", "location", "url")
                and v and not existing.get(k)}
        if record.get("description") and len(record["description"]) > len(existing.get("description") or ""):
            fill["description"] = record["description"]
        if fill:
            store.update(existing["id"], **fill)
        store.add_sighting(existing["id"], source, source_url or record.get("url"))
        return existing["id"], False
    rec = {k: v for k, v in record.items() if v is not None or k in ("org",)}
    store.insert(rec)
    store.add_sighting(rec["id"], source, source_url or record.get("url"))
    return rec["id"], True
