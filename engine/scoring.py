"""Score every opportunity out of 100 with written, editable rules (config/scoring.toml) and explain the
score in one sentence. Hard filters run first: the employer/competitor blocklist and red-flag words."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone

from .config import Config, name_hash, normalise
from .store import Store

USD_RATE = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "SAR": 0.27, "AED": 0.27, "QAR": 0.27, "KWD": 3.25,
            "EGP": 0.02, "CAD": 0.73, "AUD": 0.66}


def _ngrams(text: str, n_max: int = 4) -> set[str]:
    words = normalise(text).split()
    return {" ".join(words[i:i + n]) for n in range(1, n_max + 1) for i in range(len(words) - n + 1)}


def blocked(cfg: Config, opp: dict) -> str | None:
    """Return the reason when the organisation is the employer or (outside the jobs track) a competitor.
    Names are compared as hashes, so the blocklist itself names no one."""
    employer = set(cfg.blocklist["employer"]["hashes"])
    competitor = set(cfg.blocklist["competitor"]["hashes"])
    org_grams = {name_hash(g) for g in _ngrams(opp.get("org") or "")}
    text_grams = {name_hash(g) for g in _ngrams(f"{opp.get('title', '')} {opp.get('description', '')}"[:4000])}
    if org_grams & employer:
        return "blocked: current employer"
    if opp["track"] != "job" and (text_grams & employer):
        return "blocked: mentions the current employer"
    if opp["track"] != "job" and (org_grams & competitor):
        return "blocked: competitor of the current employer"
    return None


def red_flag(cfg: Config, text: str) -> str | None:
    low = text.lower()
    hit = next((w for w in cfg.scoring["red_flags"]["words"] if w.lower() in low), None)
    return f"red flag: '{hit}'" if hit else None


def detect(groups: dict, text: str) -> list[str]:
    low = f" {text.lower()} "
    return [g for g, words in groups.items() if any(w.lower() in low for w in words)]


def segment_of(cfg: Config, opp: dict) -> str:
    text = f"{opp.get('title', '')} {opp.get('org') or ''} {opp.get('location') or ''} {opp.get('description', '')}"
    hits = detect(cfg.scoring["segments"], text)
    order = sorted(cfg.scoring["segment_points"], key=lambda s: -cfg.scoring["segment_points"][s])
    for seg in order:
        if seg in hits:
            return seg
    return "other"


def service_of(cfg: Config, opp: dict) -> str:
    text = f" {opp.get('title', '')} {opp.get('description', '')[:1500]} ".lower()
    best, best_hits = "business_system", 0
    for sid, spec in cfg.evidence["services"].items():
        hits = sum(1 for k in spec["keywords"] if re.search(rf"\b{re.escape(k.lower())}\b", text))
        if hits > best_hits:
            best, best_hits = sid, hits
    return best


_MUST = re.compile(r"[^.;\n]*\b(must|required|requires|requirement|essential|mandatory|years of hands-on|minimum of)\b[^.;\n]*", re.I)


def must_have_gaps(cfg: Config, text: str) -> list[str]:
    words = cfg.scoring.get("jobs", {}).get("gap_words", [])
    sentences = " ".join(m.group(0).lower() for m in _MUST.finditer(text or ""))
    return sorted({w.strip() for w in words if w in f" {sentences} "})


def budget_usd(opp: dict) -> float | None:
    amount = opp.get("budget_max") or opp.get("budget_min")
    if not amount:
        return None
    return amount * USD_RATE.get((opp.get("currency") or "USD").upper(), 1.0)


def score(cfg: Config, opp: dict, today: date | None = None) -> dict:
    sc, w = cfg.scoring, cfg.scoring["weights"]
    today = today or datetime.now(timezone.utc).date()
    text = f"{opp.get('title', '')} {opp.get('org') or ''} {opp.get('description', '')}"
    parts, notes = {}, {}

    strong = detect(sc["skills"]["strong"], text)
    normal = detect(sc["skills"]["normal"], text)
    fit_raw = 2 * len(strong) + len(normal)
    parts["skills_fit"] = round(w["skills_fit"] * min(1.0, fit_raw / 6), 1)
    notes["skills_fit"] = ", ".join(strong + normal) or "no matching skills"
    gaps = must_have_gaps(cfg, opp.get("description") or "")
    if gaps:
        parts["skills_fit"] = max(0.0, parts["skills_fit"] - sc.get("jobs", {}).get("gap_penalty", 12))
        notes["skills_fit"] += f" · must-have gap: {', '.join(gaps)}"

    usd = budget_usd(opp)
    b = sc["budget"]
    if usd is None:
        parts["budget"] = float(b["unknown_points"])
        notes["budget"] = "budget not published"
    else:
        jobs = sc.get("jobs", {})
        if opp["track"] == "job" and jobs:
            monthly = usd / 12 if usd > jobs["yearly_threshold_usd"] else usd
            good, floor, usd_cmp = jobs["good_monthly_usd"], jobs["min_monthly_usd"], monthly
        else:
            good, floor, usd_cmp = b["good_usd"], b["min_usd"], usd
        ratio = 0.0 if usd_cmp < floor else min(1.0, 0.4 + 0.6 * (usd_cmp - floor) / max(1.0, good - floor))
        parts["budget"] = round(w["budget"] * ratio, 1)
        notes["budget"] = f"about USD {usd:,.0f}"

    s = sc["seriousness"]
    serious = 0.0
    if len(opp.get("description") or "") >= s["min_description_chars"]:
        serious += 0.4
    if opp.get("org"):
        serious += 0.3
    if opp.get("posted_at"):
        try:
            age = (today - date.fromisoformat(opp["posted_at"])).days
            serious += 0.3 if age <= s["fresh_days"] else (0.1 if age <= 30 else 0)
        except ValueError:
            pass
    parts["seriousness"] = round(w["seriousness"] * serious, 1)

    comp_src = opp.get("_sources") or []
    comp_pts = max([sc["competition"].get(src, 5) for src in comp_src] or [5])
    if opp["track"] == "direct":
        comp_pts = sc["competition"]["direct"]
    parts["competition"] = round(w["competition"] * comp_pts / 10, 1)

    low = text.lower()
    quick = sum(1 for k in sc["speed"]["quick_words"] if k in low)
    slow = sum(1 for k in sc["speed"]["slow_words"] if k in low)
    speed = 0.6 + 0.2 * min(quick, 2) - 0.3 * min(slow, 2)
    if opp["track"] == "job":
        speed = 0.6
    parts["speed"] = round(w["speed"] * max(0.0, min(1.0, speed)), 1)

    segment = segment_of(cfg, opp)
    seg_pts = sc["segment_points"].get(segment, sc["segment_points"]["other"])
    mult = cfg.learned.get("segments", {}).get(segment, 1.0)
    if opp["track"] == "job" and "jobs" in sc:
        roles = detect(sc["jobs"]["target_roles"], f" {opp.get('title', '')} {opp.get('title', '')} {opp.get('description', '')[:1500]} ")
        senior = any(re.search(rf"\b{re.escape(wd)}\b", opp.get("title", "").lower()) for wd in sc["jobs"]["senior_words"])
        seg_pts = 4 + 4 * min(len(roles), 3) + (4 if senior else 0)
        notes["role_fit"] = ", ".join(roles) + (" · senior" if senior else "")
    parts["evidence_value"] = round(min(w["evidence_value"], seg_pts * mult), 1)

    total = round(sum(parts.values()), 1)
    return {"score": total, "parts": parts, "notes": notes, "segment": segment,
            "service": service_of(cfg, opp), "reason": explain(parts, notes, segment, w)}


LABEL_AR = {"skills_fit": "التوافق مع مهاراتك", "budget": "الميزانية", "seriousness": "الجدية",
            "competition": "قلة المنافسة", "speed": "سرعة الإنجاز", "evidence_value": "قيمة الدليل/ملاءمة الدور"}


SKILL_AR = {"excel_automation": "إكسل", "process_automation": "أتمتة", "ai_agents": "وكلاء ذكاء اصطناعي",
            "finance": "مالية", "business_system": "أنظمة إدارة", "python": "بايثون", "web": "ويب",
            "data": "بيانات وتقارير", "motion": "موشن", "manufacturing": "تصنيع", "training": "تدريب", "arabic": "عربي"}
SEGMENT_AR = {"factory": "مصانع", "ai_training": "تدريب الشركات", "gulf": "الخليج", "logistics": "نقل وتوصيل",
              "accounting": "محاسبة", "education": "تعليم", "clinic": "عيادات", "real_estate": "عقارات",
              "creators": "صناع محتوى", "ecommerce": "متاجر إلكترونية", "restaurants": "مطاعم",
              "remote_global": "عالمي عن بُعد", "other": "عام"}


def explain(parts: dict, notes: dict, segment: str, weights: dict) -> str:
    share = {k: parts[k] / weights[k] for k in parts if weights.get(k)}
    best = sorted(share, key=share.get, reverse=True)[:2]
    worst = min(share, key=share.get)
    strong = " و".join(LABEL_AR[k] for k in best)
    skills = notes.get("skills_fit", "")
    words = [SKILL_AR.get(x.strip(), x.strip()) for x in skills.split("·")[0].split(",") if x.strip()]
    gap = (" · " + skills.split("·", 1)[1].strip().replace("must-have gap:", "ناقصك شرط أساسي:")) if "·" in skills else ""
    detail = f" ({'، '.join(words[:5])}{gap})" if "skills_fit" in best and words else (f" ({gap.strip(' ·')})" if gap else "")
    return f"قوية في {strong}{detail}، وأضعف نقطة {LABEL_AR[worst]}؛ الشريحة: {SEGMENT_AR.get(segment, segment)}."


def score_all(cfg: Config, store: Store, only_new: bool = False) -> dict:
    stats = {"scored": 0, "rejected": 0}
    where = "status IN ('new')" if only_new else "status NOT IN ('rejected','archived','won','lost')"
    for opp in store.all(where):
        opp["_sources"] = [s["source"] for s in store.sightings(opp["id"])]
        reason = blocked(cfg, opp) or red_flag(cfg, f"{opp['title']} {opp.get('description', '')}")
        result = score(cfg, opp)
        store.update(opp["id"], score=result["score"], score_reason=result["reason"],
                     score_breakdown=json.dumps(result["parts"]), segment=result["segment"],
                     service=result["service"])
        if not reason and opp["track"] == "job":
            reason = job_filter(cfg, opp)
        if not reason and result["score"] < cfg.scoring["thresholds"]["reject_below"]:
            reason = f"score {result['score']} below {cfg.scoring['thresholds']['reject_below']}"
        if reason and opp["status"] == "new":
            store.update(opp["id"], reject_reason=reason)
            store.set_status(opp["id"], "rejected", reason)
            stats["rejected"] += 1
        stats["scored"] += 1
    store.commit()
    return stats


COUNTRY_WORDS = {
    "EG": ["egypt", "cairo", "giza", "alexandria", "مصر", "القاهرة"],
    "SA": ["saudi", "riyadh", "jeddah", "dammam", "السعودية", "الرياض", "جدة"],
    "AE": ["uae", "united arab emirates", "dubai", "abu dhabi", "sharjah", "الإمارات", "دبي"],
    "QA": ["qatar", "doha", "قطر"], "KW": ["kuwait", "الكويت"], "BH": ["bahrain", "البحرين"], "OM": ["oman", "muscat", "عمان"],
}


def job_filter(cfg: Config, opp: dict) -> str | None:
    if not job_location_ok(cfg, opp):
        return "on-site outside the countries the owner accepts"
    jobs = cfg.scoring.get("jobs", {})
    title = opp.get("title", "").lower()
    text = f"{title} {(opp.get('description') or '')[:3000].lower()}"
    if any(re.search(rf"\b{re.escape(w)}\b", title) for w in jobs.get("junior_words", [])):
        return "junior role (below the owner's level)"
    if any(w in text for w in jobs.get("restricted_words", [])):
        return "restricted to nationals of another country"
    return None


_REMOTE_ELSEWHERE = re.compile(
    r"\b(us|usa|u\.s\.|united states|canada|uk|eu|europe|emea only|latam)\b[^.]{0,20}\bonly\b"
    r"|must (be based|reside|live) in (the )?(us|usa|united states|uk|eu|europe|canada)", re.I)


def job_location_ok(cfg: Config, opp: dict) -> bool:
    loc = f"{opp.get('location') or ''}".lower()
    if opp.get("remote"):
        restricted = _REMOTE_ELSEWHERE.search(f"{loc} {(opp.get('description') or '')[:3000]}")
        return not restricted or any(w in loc for c in cfg.settings["owner"]["job_countries"]
                                     for w in COUNTRY_WORDS.get(c, []))
    if not loc:
        return True
    allowed = cfg.settings["owner"]["job_countries"]
    return any(w in loc for c in allowed for w in COUNTRY_WORDS.get(c, []))
