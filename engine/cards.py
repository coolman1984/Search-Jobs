"""Opportunity card (one plain-language file per opportunity) and the baseline proposal draft.

The engine fills every field it can decide from data; the agent adds research (enrichment) and rewrites the
draft in its own words. Every draft, from the engine or the agent, passes the claims checker before it can
wait for approval."""
from __future__ import annotations

import hashlib
import json
import re

from . import claims
from .config import Config
from .polite import host_of, is_red_url
from .store import STATUSES, Store, now

GULF = ["saudi", "riyadh", "jeddah", "uae", "dubai", "abu dhabi", "qatar", "doha", "kuwait", "bahrain", "oman",
        "السعودية", "الرياض", "الإمارات", "دبي", "قطر", "الكويت"]
EGYPT = ["egypt", "cairo", "giza", "alexandria", "مصر", "القاهرة", "الجيزة", "الإسكندرية"]

SERVICE_AR = {"business_system": "نظام تشغيل وإدارة", "excel_automation": "أتمتة إكسل وتقارير",
              "ai_agents": "وكيل ذكاء اصطناعي وأتمتة سير عمل", "dashboards": "لوحة متابعة وتحليل بيانات",
              "motion_video": "إعلان موشن جرافيك بالكود", "training": "تدريب عملي على الذكاء الاصطناعي"}
SERVICE_EN = {"business_system": "a management system", "excel_automation": "Excel and report automation",
              "ai_agents": "an AI agent / workflow automation", "dashboards": "a dashboard and data analysis",
              "motion_video": "a motion-graphics video made in code", "training": "hands-on AI training"}
PROTOTYPE_AR = {
    "business_system": "نسخة تجريبية من قالب «مصنع أنظمة الأعمال» بأسماء وألوان العميل، فيها شاشة واحدة لوجعه الأساسي",
    "excel_automation": "عيّنة من ملفه (أو ملف شبهه) بتتحول لتقرير بضغطة START، وبيان بالوقت اللي اتوفر",
    "ai_agents": "فيديو 30 ثانية لوكيل بيعمل مهمته ويقف يطلب الموافقة قبل الخطوة الحساسة",
    "dashboards": "لوحة واحدة بمؤشرات عينة من نشاطه، ومكتوب عليها «بيانات توضيحية»",
    "motion_video": "مشهد افتتاحي 5–7 ثواني بألوان البراند بتاعه",
    "training": "مخطط ورشة نص يوم، وتمرين واحد من شغل فريقه الحقيقي",
}


def region_of(opp: dict) -> str:
    text = f"{opp.get('location') or ''} {opp.get('description') or ''}"[:2000].lower()
    if any(w in text for w in GULF):
        return "GULF"
    if any(w in text for w in EGYPT) or opp.get("language") == "ar":
        return "EG"
    return "GLOBAL"


def channel_of(opp: dict, cfg: Config) -> str:
    contact = json.loads(opp["contact"]) if opp.get("contact") else {}
    if contact.get("email"):
        return "email"
    if opp.get("url") and is_red_url(cfg, opp["url"]):
        return "platform"
    return "apply"


def pick_evidence(cfg: Config, opp: dict) -> dict:
    service, segment = opp.get("service") or "business_system", opp.get("segment") or "other"
    items = list(cfg.evidence["evidence"].items())
    ranked = sorted(items, key=lambda kv: (service in kv[1]["services"], segment in kv[1]["segments"]), reverse=True)
    key, ev = ranked[0]
    return {"id": key, **ev}


def pain_sentence(opp: dict) -> str:
    desc = re.sub(r"\s+", " ", opp.get("description") or "").strip()
    sentences = re.split(r"(?<=[.!?؟])\s+", desc)
    need = re.compile(r"\b(need|looking for|want|automate|struggl|manual|help|require)|نحتاج|محتاج|مطلوب|نبحث", re.I)
    hit = next((s for s in sentences if need.search(s) and 30 <= len(s) <= 260), None)
    return hit or (sentences[0][:240] if sentences and sentences[0] else opp["title"])


def price_for(cfg: Config, opp: dict) -> str:
    if opp["track"] == "job":
        if opp.get("budget_min") or opp.get("budget_max"):
            lo, hi, cur = opp.get("budget_min"), opp.get("budget_max"), opp.get("currency") or ""
            if lo and hi:
                return f"الراتب المنشور: {lo:,.0f}–{hi:,.0f} {cur} في الشهر".strip()
            return (f"الراتب المنشور: من {lo:,.0f} {cur}" if lo else f"الراتب المنشور: حتى {hi:,.0f} {cur}").strip()
        return "الراتب مش منشور: اسأل عن النطاق في أول مكالمة"
    svc = cfg.evidence["services"].get(opp.get("service") or "business_system")
    return svc["price"][region_of(opp)]


def risks(cfg: Config, opp: dict, store: Store) -> list[str]:
    out = []
    srcs = [s["source"] for s in store.sightings(opp["id"])]
    if not (opp.get("budget_min") or opp.get("budget_max")) and opp["track"] != "job":
        out.append("الميزانية مش منشورة")
    if any(s in ("remotive", "remoteok", "weworkremotely") for s in srcs):
        out.append("منصة عالمية عليها منافسة عالية جدًا")
    if opp.get("url") and is_red_url(cfg, opp["url"]):
        out.append(f"{host_of(opp['url'])}: التقديم لازم يبقى يدوي منك (منصة ممنوع عليها الأتمتة)")
    if not opp.get("org"):
        out.append("اسم الجهة مش واضح")
    if opp.get("language") == "en" and region_of(opp) == "GLOBAL":
        out.append("فرق التوقيت والدفع الدولي (Payoneer/Wise)")
    if not opp.get("description") or len(opp["description"]) < 200:
        out.append("الوصف قصير، فاسأل أسئلة توضيحية قبل ما تحدد السعر")
    return out or ["مفيش مخاطر واضحة من البيانات"]


def render_card(cfg: Config, store: Store, opp_id: str) -> str:
    opp = store.get(opp_id)
    enr = json.loads(opp["enrichment"]) if opp.get("enrichment") else {}
    ev = pick_evidence(cfg, opp)
    srcs = store.sightings(opp_id)
    attributions = sorted({cfg.sources.get(s["source"], {}).get("attribution", s["source"]) for s in srcs})
    where = opp.get("location") or ("عن بُعد" if opp.get("remote") else "مش محدد")
    lines = [
        f"# {opp['title']}" + (f" · {opp['org']}" if opp.get("org") else ""),
        "",
        f"**الدرجة:** {opp.get('score') or 0:.0f}/100 · {opp.get('score_reason') or ''}",
        f"**المسار:** {opp['track']} · **الحالة:** {STATUSES.get(opp['status'], opp['status'])} · **الرابط:** {opp.get('url') or '—'}",
        "",
        "| | |",
        "|---|---|",
        f"| **هم مين** | {enr.get('who') or (opp.get('org') or 'غير معروف')} |",
        f"| **فين** | {where} |",
        f"| **الوجع** | {enr.get('pain') or pain_sentence(opp)} |",
        f"| **الحل المقترح** | {SERVICE_AR.get(opp.get('service'), opp.get('service'))}" + (f": {enr['solution']}" if enr.get('solution') else "") + " |",
        f"| **إزاي نوصلهم** | {_reach(cfg, opp, enr)} |",
        f"| **إزاي تقدّم** | الزاوية: {enr.get('angle') or 'محاسب تكاليف ومخطط مالي بيبني الحل بنفسه'} · الدليل: {ev['name_ar']} |",
        f"| **السعر المقترح** | {price_for(cfg, opp)} |",
        f"| **نموذج/فيديو يبهرهم** | {enr.get('prototype') or PROTOTYPE_AR.get(opp.get('service'), '')} |",
        f"| **المخاطر** | {' · '.join(enr.get('risks') or risks(cfg, opp, store))} |",
        "",
    ]
    if enr.get("facts"):
        lines += ["**دراسة الجهة (من مصادر عامة):**"] + [f"- {f}" for f in enr["facts"]] + [""]
    if enr.get("solutions"):
        lines += ["**حلول جاهزة ممكن نستخدمها:**"] + [f"- {s}" for s in enr["solutions"]] + [""]
    lines.append("**المصادر:** " + " · ".join(attributions) +
                 (" · " + " · ".join(enr.get("sources", [])) if enr.get("sources") else ""))
    return "\n".join(lines) + "\n"


def _reach(cfg: Config, opp: dict, enr: dict) -> str:
    ch = channel_of(opp, cfg)
    if ch == "email":
        return "إيميل الأعمال المنشور (مسودة في Gmail بعد موافقتك)"
    if ch == "platform":
        return "تقدّم بنفسك من رابط المنصة بالنص الجاهز"
    return enr.get("channel") or "رابط التقديم الرسمي، والنص جاهز تنسخه"


# ------------------------------------------------------------------------------------------------ drafts
def draft_hash(draft: dict) -> str:
    canon = json.dumps({k: draft.get(k) for k in ("channel", "to", "subject", "body")}, ensure_ascii=False,
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def baseline_draft(cfg: Config, opp: dict) -> dict:
    ev = pick_evidence(cfg, opp)
    lang = "ar" if opp.get("language") == "ar" else "en"
    svc = cfg.evidence["services"].get(opp.get("service") or "business_system")
    pain = pain_sentence(opp)
    if lang == "ar":
        body = "\n\n".join(filter(None, [
            f"قريت طلبكم: «{pain[:200]}»." if opp["track"] != "direct" else f"لاحظت إن {opp.get('org') or 'نشاطكم'} ممكن يستفيد من {SERVICE_AR[opp.get('service') or 'business_system']}.",
            ev["line_ar"],
            f"لحالتكم أقترح {SERVICE_AR[opp.get('service') or 'business_system']}، ونسخة أولى تجربوها خلال {svc['duration']}.",
            "ينفع مكالمة 20 دقيقة الأسبوع ده؟ أقدر أوريكم مثال شغال الأول.",
            "لو الموضوع مش مناسب، رد بكلمة «لا» ومش هبعت تاني." if opp["track"] == "direct" else "",
            f"محمد فوزي لبيب\ngithub.com/coolman1984/{ev['repo']}",
        ]))
        subject = f"بخصوص: {opp['title'][:80]}"
    else:
        opener = (f"You mentioned: \"{pain[:200]}\"" if opp["track"] != "direct"
                  else f"{opp.get('org') or 'Your team'} could save hours with {SERVICE_EN[opp.get('service') or 'business_system']}.")
        body = "\n\n".join(filter(None, [
            opener,
            ev["line_en"],
            f"For your case I would build {SERVICE_EN[opp.get('service') or 'business_system']}, with a first version you can try within {svc['duration']}.",
            "Would a 20-minute call this week work? I can show a working example first.",
            "If this is not relevant, reply \"stop\" and I won't contact you again." if opp["track"] == "direct" else "",
            f"Mohamed Fawzy Labib\ngithub.com/coolman1984/{ev['repo']}",
        ]))
        subject = f"Re: {opp['title'][:80]}"
    contact = json.loads(opp["contact"]) if opp.get("contact") else {}
    return {"channel": channel_of(opp, cfg), "to": contact.get("email"), "subject": subject, "body": body,
            "lang": lang, "evidence": ev["id"], "author": "engine", "created": now()}


class DraftRefused(Exception):
    pass


def set_draft(cfg: Config, store: Store, opp_id: str, draft: dict) -> dict:
    """Store a draft after the claims check and move the opportunity to 'awaiting approval'.
    Any later change produces a new hash, so an earlier approval no longer matches."""
    opp = store.get(opp_id)
    if not opp:
        raise DraftRefused(f"no opportunity {opp_id}")
    if opp["status"] in ("rejected", "archived", "won"):
        raise DraftRefused(f"{opp_id} is {opp['status']}")
    svc = cfg.evidence["services"].get(opp.get("service") or "business_system")
    extra = [svc["duration"], svc["price"].get(region_of(opp), "")]
    problems = claims.check(cfg, opp, f"{draft.get('subject', '')}\n{draft['body']}", extra_allowed=extra)
    if problems:
        store.event(opp_id, "draft_refused", {"problems": problems})
        store.commit()
        raise DraftRefused("; ".join(problems))
    if draft.get("to"):
        from .release import contact_hash
        if store.is_dnc(contact_hash(draft["to"])):
            raise DraftRefused("this contact asked not to be contacted again")
    h = draft_hash(draft)
    store.update(opp_id, draft=json.dumps(draft, ensure_ascii=False), draft_hash=h, approval=None)
    if opp["status"] == "new":
        store.set_status(opp_id, "studied", "draft written")
    current = store.get(opp_id)["status"]
    if current != "awaiting_approval":
        store.set_status(opp_id, "awaiting_approval", f"draft {h[:12]}")
    else:
        store.event(opp_id, "draft_replaced", {"hash": h})
    store.commit()
    return {"hash": h}
