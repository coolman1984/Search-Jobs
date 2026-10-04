"""Daily and weekly reports, written for a phone: conclusion first, short lines, one action per item."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import followup
from .approval import approval_link
from .cards import price_for, render_card
from .config import Config
from .store import STATUSES, Store

TRACK_AR = {"job": "وظيفة", "freelance": "عمل حر", "direct": "عميل مباشر"}
CHANNEL_AR = {"email": "إيميل (بعد موافقتك هيبقى مسودة في Gmail وإنت تدوس Send)",
              "platform": "تقديم يدوي منك على المنصة (ممنوع عليها الأتمتة)",
              "apply": "تقديم يدوي منك من رابط التقديم الرسمي"}


def top(cfg: Config, store: Store, n: int | None = None) -> list[dict]:
    n = n or cfg.settings["limits"]["top_per_day"]
    rows = store.all("status IN ('new','studied','awaiting_approval') AND score IS NOT NULL")
    rows.sort(key=lambda o: -(o["score"] or 0))
    return rows[:n]


def daily(cfg: Config, store: Store, *, mode: str, stats: dict, part: str = "public") -> str:
    """part='public' is safe to post in a public GitHub issue (no drafts, no contacts);
    part='full' carries cards, drafts and approval links (issue when the repo is private, else a Gmail draft to
    the owner himself)."""
    day = datetime.now(timezone.utc).date().isoformat()
    best = top(cfg, store)
    waiting = store.all("status='awaiting_approval'")
    ready = store.all("status IN ('approved','ready')")
    due = followup.due(cfg, store)
    lines = [f"# تقرير الفرص · {day}", ""]
    lines += ["## الخلاصة",
              f"- اتجمع النهارده: **{stats.get('created', 0)}** فرصة جديدة، واتدمج **{stats.get('merged', 0)}** مكرر، "
              f"واتستبعد **{stats.get('rejected', 0)}**.",
              f"- أعلى الفرص: **{len(best)}** · مستنية موافقتك: **{len(waiting)}** · جاهزة تبعتها: **{len(ready)}** · "
              f"متابعات النهارده: **{len(due)}**."]
    if mode == "public":
        lines.append("- ⚠️ المستودع لسه **عام** ومفيش مفتاح تشفير، فالمسودات مش بتتحفظ لبكرة. "
                     "حوّله Private (أو ضيف `SEARCHJOBS_KEY`) عشان الموافقة تشتغل يوم بيوم.")
    if stats.get("source_errors"):
        blocked = [e.split(":")[0] for e in stats["source_errors"] if "403" in e or "unreachable" in e]
        other = [e for e in stats["source_errors"] if not ("403" in e or "unreachable" in e)]
        if blocked:
            lines.append(f"- مصادر مقفولة من شبكة السحابة ({len(blocked)}): {', '.join(blocked)}. "
                         "الحل في `docs/SETUP.md` خطوة 2.")
        if other:
            lines.append("- مصادر فيها مشكلة: " + " · ".join(e[:120] for e in other[:4]))
    lines.append("")

    lines.append("## أفضل الفرص")
    for i, o in enumerate(best, 1):
        lines.append(f"{i}. **{o['title']}**" + (f" · {o['org']}" if o.get("org") else "") +
                     f" · {TRACK_AR[o['track']]} · **{o['score']:.0f}/100**")
        lines.append(f"   - {o.get('score_reason') or ''}")
        lines.append(f"   - السعر: {price_for(cfg, o)} · الرابط: {o.get('url') or '—'}")
        if part == "full":
            lines += ["", "<details><summary>البطاقة</summary>", "", render_card(cfg, store, o["id"]), "</details>", ""]
    if not best:
        lines.append("- مفيش فرص فوق الحد النهارده. الحد ده بيتظبط في `config/scoring.toml`.")
    lines.append("")

    lines.append("## مستنية موافقتك")
    if not waiting:
        lines.append("- مفيش.")
    for o in waiting:
        d = json.loads(o["draft"]) if o.get("draft") else None
        if part == "full" and d:
            lines += [f"### {o['title']}" + (f" · {o['org']}" if o.get("org") else ""),
                      f"القناة: {CHANNEL_AR.get(d['channel'], d['channel'])} · اللغة: {d.get('lang')}",
                      *(["التقديم ده إنت اللي بتعمله: انسخ النص وقدّم من الرابط "
                         f"({o.get('url') or '—'}). الموافقة هنا بتسجّل قرارك بس، وبتخلّي المنظومة تتابع."]
                        if d["channel"] != "email" else []),
                      "", "```", (f"Subject: {d['subject']}\n\n" if d.get("subject") else "") + d["body"], "```", "",
                      f"✅ **[موافق على النص ده بالظبط]({approval_link(cfg, o['id'], o['draft_hash'])})** "
                      "← اضغط، وبعدين اضغط Commit changes.",
                      f"<sub>رقم الفرصة `{o['id']}` · البصمة `{o['draft_hash']}`</sub>", ""]
        else:
            lines.append(f"- {o['title']} · المسودة جاهزة: في Gmail ← المسودات ← «تفاصيل فرص اليوم»")
    lines.append("")

    lines.append("## جاهزة تبعتها")
    lines += [f"- {o['title']} · {STATUSES[o['status']]}" for o in ready] or ["- مفيش."]
    lines.append("")
    lines.append("## متابعات النهارده")
    lines += [f"- {d['title']} ({d['days']} يوم) · {d['action']}" for d in due] or ["- مفيش."]
    lines.append("")
    if stats.get("idea"):
        lines += ["## فكرة النهارده", f"- {stats['idea']}", ""]
    lines.append("_المصادر: كل فرصة مكتوب جنبها مصدرها في البطاقة. مفيش أي رسالة بتتبعت غير بعد موافقتك وإنت اللي بتبعتها._")
    return "\n".join(lines) + "\n"


def weekly(cfg: Config, store: Store, learned: dict) -> str:
    m = followup.market_stats(store)
    lines = [f"# تقرير الأسبوع · {datetime.now(timezone.utc).date().isoformat()}", "", "## الخلاصة",
             f"- فرص جديدة الأسبوع ده: **{m['new']}** (الأسبوع اللي فات: {m['previous']}).",
             f"- اتبعت: **{learned['sent']}** · نسبة الرد: **{learned['base_rate']:.0%}**.", ""]
    lines.append("## الشرائح (بتسخن ولا بتبرد؟)")
    for seg, v in sorted(m["segments"].items(), key=lambda kv: -kv[1]["now"]):
        arrow = "↑" if v["now"] > v["before"] else ("↓" if v["now"] < v["before"] else "→")
        lines.append(f"- {seg}: {v['now']} {arrow} (قبلها {v['before']})")
    lines += ["", "## الخدمات المطلوبة"]
    lines += [f"- {k}: {v}" for k, v in m["services"].items()] or ["- مفيش بيانات كفاية."]
    lines += ["", "## ميزانيات ظهرت"]
    lines += [f"- {b['title']}: {b['min'] or '?'}–{b['max'] or '?'} {b['currency'] or ''}" for b in m["budgets"]] or ["- مفيش ميزانيات منشورة."]
    lines += ["", "## اللي اتعلمناه"]
    if learned["changes"]:
        lines += [f"- {seg}: الوزن {c['from']} → {c['to']} ({c['positive']} رد من {c['sent']})"
                  for seg, c in learned["changes"].items()]
    else:
        lines.append("- لسه مفيش بيانات كفاية نغيّر بيها الأوزان (لازم 3 رسايل متبعتة على الأقل في الشريحة).")
    lines += ["", "## أسباب الاستبعاد"]
    lines += [f"- {k or 'other'}: {v}" for k, v in m["rejected"].items()] or ["- مفيش."]
    return "\n".join(lines) + "\n"
