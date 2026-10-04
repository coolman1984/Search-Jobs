"""One collector per green/yellow source. Each returns a list of standard records (see normalize.make_record).
A collector never decides anything: scoring and filtering happen later, in one place."""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from ..config import Config
from ..normalize import make_record, strip_html
from ..polite import Fetcher, Refused

KEYWORDS = ["automation", "excel", "python", "ai", "agent", "finance", "dashboard", "data", "erp",
            "workflow", "power bi", "motion", "analyst", "operations", "n8n", "llm"]


_KW = re.compile(r"\b(" + "|".join(re.escape(k) for k in KEYWORDS) + r")\b", re.I)


def _relevant(*texts: str) -> bool:
    return bool(_KW.search(" ".join(t or "" for t in texts)))


def remotive(cfg: Config, f: Fetcher) -> list[dict]:
    data = f.get_json("remotive", cfg.source("remotive")["url"])
    out = []
    for j in data.get("jobs", []):
        if not _relevant(j.get("title"), j.get("category"), " ".join(j.get("tags") or [])):
            continue
        track = "freelance" if (j.get("job_type") or "").lower() in ("contract", "freelance") else "job"
        out.append(make_record(track=track, title=j.get("title", ""), org=j.get("company_name"),
                               location=j.get("candidate_required_location"), remote=True, url=j.get("url"),
                               description=j.get("description"), posted_at=j.get("publication_date")))
    return out


def remoteok(cfg: Config, f: Fetcher) -> list[dict]:
    data = f.get_json("remoteok", cfg.source("remoteok")["url"])
    out = []
    for j in data:
        if not isinstance(j, dict) or "position" not in j:
            continue  # first element is the legal notice
        if not _relevant(j.get("position"), " ".join(j.get("tags") or [])):
            continue
        out.append(make_record(track="job", title=j["position"], org=j.get("company"), location=j.get("location"),
                               remote=True, url=j.get("url"), description=j.get("description"),
                               posted_at=j.get("date"), budget_min=j.get("salary_min") or None,
                               budget_max=j.get("salary_max") or None, currency="USD"))
    return out


def himalayas(cfg: Config, f: Fetcher) -> list[dict]:
    data = f.get_json("himalayas", cfg.source("himalayas")["url"])
    out = []
    for j in data.get("jobs", []):
        if not _relevant(j.get("title"), " ".join(j.get("categories") or [])):
            continue
        out.append(make_record(track="job", title=j.get("title", ""), org=j.get("companyName"),
                               location=", ".join(j.get("locationRestrictions") or []) or "Remote", remote=True,
                               url=j.get("applicationLink") or j.get("guid"), description=j.get("description"),
                               posted_at=j.get("pubDate"), budget_min=j.get("minSalary"),
                               budget_max=j.get("maxSalary"), currency=j.get("currency")))
    return out


def arbeitnow(cfg: Config, f: Fetcher) -> list[dict]:
    data = f.get_json("arbeitnow", cfg.source("arbeitnow")["url"])
    out = []
    for j in data.get("data", []):
        if not j.get("remote") or not _relevant(j.get("title"), " ".join(j.get("tags") or [])):
            continue
        out.append(make_record(track="job", title=j.get("title", ""), org=j.get("company_name"),
                               location=j.get("location"), remote=True, url=j.get("url"),
                               description=j.get("description"), posted_at=j.get("created_at")))
    return out


def jobicy(cfg: Config, f: Fetcher) -> list[dict]:
    data = f.get_json("jobicy", cfg.source("jobicy")["url"])
    out = []
    for j in data.get("jobs", []):
        if not _relevant(j.get("jobTitle"), " ".join(j.get("jobIndustry") or [])):
            continue
        out.append(make_record(track="job", title=j.get("jobTitle", ""), org=j.get("companyName"),
                               location=j.get("jobGeo"), remote=True, url=j.get("url"),
                               description=j.get("jobDescription") or j.get("jobExcerpt"),
                               posted_at=j.get("pubDate"), budget_min=j.get("annualSalaryMin"),
                               budget_max=j.get("annualSalaryMax"), currency=j.get("salaryCurrency")))
    return out


def weworkremotely(cfg: Config, f: Fetcher) -> list[dict]:
    out = []
    for url in cfg.source("weworkremotely")["urls"]:
        try:
            body = f.get("weworkremotely", url, accept="application/rss+xml")
        except Refused:
            break
        for item in ET.fromstring(body).iter("item"):
            raw_title = item.findtext("title") or ""
            org, _, title = raw_title.partition(":") if ":" in raw_title else ("", "", raw_title)
            if not _relevant(title, item.findtext("description") or ""):
                continue
            out.append(make_record(track="job", title=title.strip(), org=org.strip() or None,
                                   location=item.findtext("region") or "Remote", remote=True,
                                   url=item.findtext("link"), description=item.findtext("description"),
                                   posted_at=item.findtext("pubDate")))
    return out


def hn_whoishiring(cfg: Config, f: Fetcher) -> list[dict]:
    base = cfg.source("hn_whoishiring")["url"]
    stories = f.get_json("hn_whoishiring", f"{base}/search_by_date?tags=story,author_whoishiring&hitsPerPage=5")
    thread = next((h for h in stories.get("hits", []) if "who is hiring" in (h.get("title") or "").lower()), None)
    if not thread:
        return []
    started = datetime.fromtimestamp(thread.get("created_at_i", 0), tz=timezone.utc)
    if datetime.now(timezone.utc) - started > timedelta(days=35):
        return []
    item = f.get_json("hn_whoishiring", f"{base}/items/{thread['objectID']}")
    out = []
    for c in item.get("children", []):
        text = strip_html(c.get("text"))
        if not text or not _relevant(text):
            continue
        head = text.split("|")
        org = head[0].strip()[:120] if len(head) > 1 else None
        title = (head[1].strip() if len(head) > 1 else text[:120])[:200]
        remote = "remote" in text.lower()
        if not remote:
            continue
        out.append(make_record(track="job", title=title, org=org, location="Remote", remote=True,
                               url=f"https://news.ycombinator.com/item?id={c.get('id')}", description=text,
                               posted_at=c.get("created_at")))
    return out


def greenhouse(cfg: Config, f: Fetcher) -> list[dict]:
    spec = cfg.source("greenhouse")
    out = []
    for board in spec.get("boards", []):
        data = f.get_json("greenhouse", spec["url"].format(board=board))
        for j in data.get("jobs", []):
            out.append(make_record(track="job", title=j.get("title", ""), org=board,
                                   location=(j.get("location") or {}).get("name"), url=j.get("absolute_url"),
                                   description=j.get("content"), posted_at=j.get("updated_at")))
    return out


def lever(cfg: Config, f: Fetcher) -> list[dict]:
    spec = cfg.source("lever")
    out = []
    for company in spec.get("companies", []):
        data = f.get_json("lever", spec["url"].format(company=company))
        for j in data:
            out.append(make_record(track="job", title=j.get("text", ""), org=company,
                                   location=(j.get("categories") or {}).get("location"), url=j.get("hostedUrl"),
                                   description=j.get("descriptionPlain"), posted_at=(j.get("createdAt") or 0) / 1000))
    return out


def osm(cfg: Config, f: Fetcher) -> list[dict]:
    """Businesses for the direct track (name, type, public website). Personal data is never requested."""
    spec = cfg.source("osm")
    out = []
    for area in spec.get("areas", []):
        for tag in spec.get("tags", []):
            k, v = tag.split("=", 1)
            query = (f'[out:json][timeout:25];area["name:en"="{area}"]->.a;'
                     f'nwr["{k}"="{v}"]["website"](area.a);out tags 40;')
            data = f.get_json("osm", spec["url"] + "?data=" + urllib.parse.quote(query))
            for el in data.get("elements", []):
                t = el.get("tags", {})
                name = t.get("name:en") or t.get("name")
                if not name:
                    continue
                out.append(make_record(track="direct", title=f"{v.replace('_', ' ')}: {name}", org=name,
                                       location=t.get("addr:city") or area, url=t.get("website"),
                                       description=f"OSM {tag}; website {t.get('website')}"))
    return out


COLLECTORS = {
    "remotive": remotive,
    "remoteok": remoteok,
    "himalayas": himalayas,
    "arbeitnow": arbeitnow,
    "jobicy": jobicy,
    "weworkremotely": weworkremotely,
    "hn_whoishiring": hn_whoishiring,
    "greenhouse": greenhouse,
    "lever": lever,
    "osm": osm,
}
