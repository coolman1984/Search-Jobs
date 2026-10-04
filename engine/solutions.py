"""Solution factory: one folder per opportunity the owner decides to pursue, so he walks into the first call
with something the client can see."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .cards import PROTOTYPE_AR, SERVICE_AR, pain_sentence, pick_evidence, price_for
from .config import Config
from .store import Store, today

# Which of the owner's own projects to start from, per service (all on the safe-evidence list).
TEMPLATES = {
    "business_system": [("Business-Template", "نواة + وصفة نشاط + إعدادات عميل: أسرع طريق لنظام كامل"),
                        ("Delivery-Manager", "لو فيه طلبات وتوصيل وتحصيل"),
                        ("Teachers", "لو فيه حضور وتحصيل على الباب ولينك لولي الأمر/العميل")],
    "excel_automation": [("Perfect-Project-Template", "محرك الإكسل: ZIP + START للموظف"),
                         ("Office-Automation", "xl2ai: لو الملفات كبيرة أو محتاجة تحقق وتسوية")],
    "ai_agents": [("win-agent-desktop", "لو الوكيل لازم يشغّل برامج ويندوز"),
                  ("Performance", "نموذج خادم MCP")],
    "dashboards": [("Office-Automation", "طبقة بيانات موثوقة تحت اللوحة"),
                   ("Accounting-sys", "لو اللوحة مالية/محاسبية")],
    "motion_video": [("Animation", "استوديو الأفلام بالكود: فيلم 15–30 ثانية بمقاسات المنصات")],
    "training": [("Personal-Web", "الكورسات الـ11 كمنهج جاهز")],
}

SECTIONS = ["1. فهم المتطلبات", "2. الحلول الجاهزة اللي لقيناها", "3. المعمارية المقترحة",
            "4. تقدير الوقت والتكلفة", "5. النموذج الأولي أو الفيديو", "6. أسئلة لأول مكالمة"]


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:40] or "opportunity"


def create(cfg: Config, store: Store, opp_id: str, base: Path | None = None) -> Path:
    opp = store.get(opp_id)
    if not opp:
        raise ValueError(f"no opportunity {opp_id}")
    base = base or (cfg.root / "solutions")
    folder = base / f"{opp_id}-{slug(opp.get('org') or opp['title'])}"
    folder.mkdir(parents=True, exist_ok=True)
    enr = json.loads(opp["enrichment"]) if opp.get("enrichment") else {}
    ev = pick_evidence(cfg, opp)
    svc = cfg.evidence["services"].get(opp.get("service") or "business_system")
    templates = TEMPLATES.get(opp.get("service") or "business_system", [])
    readme = folder / "README.md"
    if not readme.exists():
        lines = [f"# {opp['title']}" + (f" · {opp['org']}" if opp.get("org") else ""), "",
                 f"_اتعمل {today()} · فرصة {opp_id} · الدرجة {opp.get('score') or 0:.0f}/100 · {opp.get('url') or ''}_", "",
                 f"## {SECTIONS[0]}",
                 f"- **الوجع بكلامهم:** {enr.get('pain') or pain_sentence(opp)}",
                 f"- **الخدمة:** {SERVICE_AR.get(opp.get('service'), opp.get('service'))}",
                 "- **المطلوب بالظبط (يتكمّل من الإعلان والمكالمة):**", "  - …", "",
                 f"## {SECTIONS[1]}",
                 "| الأداة / المشروع | الرابط | الرخصة | النضج (نجوم، آخر تحديث) | هنستخدمها في إيه |",
                 "|---|---|---|---|---|"]
        lines += [f"| {s} | | | | |" for s in enr.get("solutions", [])] or ["| (الوكيل بيملاها من GitHub والويب) | | | | |"]
        lines += ["", "**نبدأ من مشاريعك:**"] + [f"- `{repo}`: {why}" for repo, why in templates] + ["",
                  f"## {SECTIONS[2]}", "```", "[مصدر البيانات] → [المعالجة/القواعد] → [الواجهة/التقرير] → [الموافقة البشرية]", "```", "",
                  f"## {SECTIONS[3]}",
                  f"- المدة: {svc['duration']} (بنظام {cfg.settings['owner']['hours_per_week']} ساعة في الأسبوع)",
                  f"- السعر المقترح: {price_for(cfg, opp)}",
                  "- اللي داخل في السعر: نسخة أولى، وجولتين تعديل، والتسليم مع دليل.", "",
                  f"## {SECTIONS[4]}", f"- {enr.get('prototype') or PROTOTYPE_AR.get(opp.get('service'), '')}",
                  f"- الدليل اللي هنوريه: {ev['name_ar']} (github.com/coolman1984/{ev['repo']})", "",
                  f"## {SECTIONS[5]}",
                  "- إيه أكتر خطوة بتاخد وقت النهارده؟ وبتتكرر كام مرة؟",
                  "- مين هيستخدم الحل كل يوم؟ ومين بيقرر؟",
                  "- الحل هيشتغل على أجهزتهم ولا أونلاين؟ وفين البيانات؟",
                  "- إمتى عايزين أول نسخة؟ وفيه ميزانية متحددة؟"]
        readme.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store.event(opp_id, "solution_folder", {"path": str(folder.relative_to(cfg.root)) if folder.is_relative_to(cfg.root) else str(folder)})
    store.commit()
    return folder
