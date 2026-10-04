"""Bring in what the agent collected through official connectors (Indeed, Gmail alerts, GitHub, web search).

The agent writes one JSON file per connector call into inbox/ (git-ignored):
  {"source": "indeed", "query": "automation Cairo", "items": [{"track": "job", "title": "...", "org": "...",
   "location": "...", "url": "...", "description": "...", "posted_at": "...", "budget_min": 0, "currency": "USD"}]}
Email bodies are never stored: parse_alert() keeps only title, company, location and link.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Config
from .normalize import make_record, upsert
from .store import Store

ITEM_FIELDS = {"track", "title", "org", "location", "remote", "url", "description", "posted_at", "budget_min",
               "budget_max", "currency", "contact"}


class IngestError(Exception):
    pass


def ingest_payload(cfg: Config, store: Store, payload: dict) -> dict:
    source = payload.get("source")
    spec = cfg.sources.get(source or "")
    if not spec:
        raise IngestError(f"unknown source '{source}'")
    if spec["color"] == "red":
        raise IngestError(f"'{source}' is red: nothing from it may be ingested automatically")
    if spec.get("kind") != "connector":
        raise IngestError(f"'{source}' is collected by the engine itself, not through inbox/")
    created = merged = skipped = 0
    for raw in payload.get("items", []):
        item = {k: v for k, v in raw.items() if k in ITEM_FIELDS}
        item.setdefault("track", spec.get("tracks", ["job"])[0])
        if item["track"] not in spec.get("tracks", []):
            skipped += 1
            continue
        try:
            record = make_record(**item)
        except (ValueError, TypeError):
            skipped += 1
            continue
        _, new = upsert(store, record, source)
        created += new
        merged += not new
    store.event(None, "ingest", {"source": source, "query": payload.get("query"), "created": created,
                                 "merged": merged, "skipped": skipped})
    store.commit()
    return {"created": created, "merged": merged, "skipped": skipped}


def ingest_dir(cfg: Config, store: Store, inbox: Path) -> dict:
    totals = {"files": 0, "created": 0, "merged": 0, "skipped": 0, "errors": []}
    done = inbox / "done"
    for path in sorted(inbox.glob("*.json")):
        try:
            res = ingest_payload(cfg, store, json.loads(path.read_text(encoding="utf-8")))
        except (IngestError, json.JSONDecodeError) as exc:
            totals["errors"].append(f"{path.name}: {exc}")
            continue
        totals["files"] += 1
        for k in ("created", "merged", "skipped"):
            totals[k] += res[k]
        done.mkdir(parents=True, exist_ok=True)
        path.rename(done / path.name)
    return totals


# ---------------------------------------------------------------------------------------------- email alerts
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_JOB_URL = {
    "linkedin": re.compile(r"linkedin\.com/(comm/)?jobs/view/\d+"),
    "upwork": re.compile(r"upwork\.com/(jobs|freelance-jobs/apply)/"),
    "mostaql": re.compile(r"mostaql\.com/project/\d+"),
    "khamsat": re.compile(r"khamsat\.com/community/requests/\d+"),
    "wuzzuf": re.compile(r"wuzzuf\.net/jobs/p/"),
    "bayt": re.compile(r"bayt\.com/[a-z]{2}/[^/]+/jobs/"),
    "freelancer": re.compile(r"freelancer\.com/projects/"),
    "indeed": re.compile(r"indeed\.com/(viewjob|rc/clk|pagead)"),
}
_TRACK = {"linkedin": "job", "wuzzuf": "job", "bayt": "job", "indeed": "job",
          "upwork": "freelance", "mostaql": "freelance", "khamsat": "freelance", "freelancer": "freelance"}
_NOISE = re.compile(
    r"^(view job|apply|see all|unsubscribe|manage|edit alert|عرض|تقديم|actively recruiting|promoted|easy apply|new jobs?"
    r"|this company is actively hiring|your job alert|job search smarter|-{3,}|\d+ (company alumni|connections?|school alums?|applicants?)"
    r"|<strong)", re.I)
_TAGS = re.compile(r"<[^>]+>")


def platform_of(sender: str) -> str | None:
    s = (sender or "").lower()
    return next((p for p in _JOB_URL if p in s), None)


def parse_alert(sender: str, subject: str, body: str) -> list[dict]:
    """Best-effort extraction of (title, company, location, url) from a job-alert email body (plain text).
    Only those four fields leave this function; the body itself is discarded."""
    platform = platform_of(sender)
    if not platform:
        return []
    pattern = _JOB_URL[platform]
    lines = [_TAGS.sub("", ln).strip() for ln in (body or "").splitlines()]
    items, seen = [], set()
    freelance = _TRACK[platform] == "freelance"
    for i, line in enumerate(lines):
        for url in _URL.findall(line):
            if not pattern.search(url):
                continue
            key = url.split("?")[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            context = []
            for prev in reversed(lines[max(0, i - 6):i]):  # walk back to the previous link only
                if _URL.search(prev):
                    break
                if prev and not _NOISE.match(prev):
                    context.insert(0, prev)
            if not context:
                text_before = _URL.sub("", line).strip(" :-–|")
                context = [text_before] if text_before else []
            if not context:
                continue
            context = context[-3:] if not freelance else context[:2]
            item = {"track": _TRACK[platform], "title": context[0],
                    "url": key if platform != "indeed" else url,
                    "description": f"From a {platform} alert email: {subject}"[:300]}
            if freelance:
                meta = " ".join(context[1:])
                budget = re.search(r"\$\s?([\d,]+)", meta)
                if budget:
                    item.update(budget_max=budget.group(1).replace(",", ""), currency="USD")
                if meta:
                    item["description"] = f"{meta} · {item['description']}"[:300]
            else:
                item["org"] = context[1] if len(context) > 1 else None
                item["location"] = context[2] if len(context) > 2 else None
            items.append(item)
    return items
