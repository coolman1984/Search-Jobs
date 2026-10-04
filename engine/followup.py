"""Follow-up reminders and weekly learning from real outcomes."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .store import Store

NEXT_ACTION = {
    "ready": "ابعت المسودة (أو قدّم بالنص الجاهز)، وبعدين قولّي «اتبعت»",
    "sent": "مفيش رد لسه: حضّر متابعة قصيرة (محتاجة موافقة جديدة)",
    "replied": "رد عليهم: حدد معاد مكالمة",
    "meeting": "جهّز للاجتماع: النموذج الأولي والأسئلة",
    "quoted": "تابع عرض السعر",
}
WAIT_DAYS = {"ready": 1, "sent": None, "replied": 1, "meeting": 0, "quoted": 3}


def _last_status_change(store: Store, opp_id: str) -> date:
    evs = [e for e in store.events(opp_id, "status")]
    ts = evs[-1]["ts"] if evs else store.get(opp_id)["updated_at"]
    return datetime.fromisoformat(ts).date()


def due(cfg: Config, store: Store, on: date | None = None) -> list[dict]:
    on = on or datetime.now(timezone.utc).date()
    out = []
    for opp in store.all("status IN ('ready','sent','replied','meeting','quoted')"):
        wait = WAIT_DAYS[opp["status"]]
        if wait is None:
            wait = cfg.settings["limits"]["followup_after_days"]
        since = _last_status_change(store, opp["id"])
        if (on - since).days >= wait:
            out.append({"id": opp["id"], "title": opp["title"], "org": opp.get("org"), "status": opp["status"],
                        "days": (on - since).days, "action": NEXT_ACTION[opp["status"]]})
    return sorted(out, key=lambda d: -d["days"])


def archive_stale(cfg: Config, store: Store, on: date | None = None) -> int:
    on = on or datetime.now(timezone.utc).date()
    keep = cfg.settings["limits"]["keep_days"]
    n = 0
    for opp in store.all("status IN ('new','studied','rejected')"):
        if (on - datetime.fromisoformat(opp["created_at"]).date()).days > keep:
            store.set_status(opp["id"], "archived", f"no action for {keep} days")
            n += 1
    store.commit()
    return n


# -------------------------------------------------------------------------------------------- learning
POSITIVE = {"replied", "meeting", "quoted", "won"}


def outcomes(store: Store) -> dict:
    """Per segment and per track: how many were actually sent, and how many got a positive answer."""
    sent_ids = {e["opportunity_id"] for e in store.events(kind="status") if json.loads(e["data"]).get("to") == "sent"}
    positive_ids = {e["opportunity_id"] for e in store.events(kind="status")
                    if json.loads(e["data"]).get("to") in POSITIVE}
    by = defaultdict(lambda: {"sent": 0, "positive": 0})
    for oid in sent_ids:
        opp = store.get(oid)
        if not opp:
            continue
        for key in (f"segment:{opp.get('segment') or 'other'}", f"track:{opp['track']}",
                    f"evidence:{json.loads(opp['draft']).get('evidence') if opp.get('draft') else 'unknown'}"):
            by[key]["sent"] += 1
            by[key]["positive"] += oid in positive_ids
    return dict(by)


def learn(cfg: Config, store: Store, min_sent: int = 3) -> dict:
    """Nudge segment multipliers toward what gets answers. Bounded (0.7-1.3) and only with enough data, so
    one lucky reply cannot swing the system. Writes config/learned.toml; the rules file is never touched."""
    stats = outcomes(store)
    total_sent = sum(v["sent"] for k, v in stats.items() if k.startswith("track:"))
    total_pos = sum(v["positive"] for k, v in stats.items() if k.startswith("track:"))
    base = (total_pos / total_sent) if total_sent else 0
    multipliers = dict(cfg.learned.get("segments", {}))
    changes = {}
    for key, v in stats.items():
        if not key.startswith("segment:") or v["sent"] < min_sent or base == 0:
            continue
        seg = key.split(":", 1)[1]
        rate = v["positive"] / v["sent"]
        old = multipliers.get(seg, 1.0)
        new = round(max(0.7, min(1.3, old * (0.8 + 0.2 * (rate / base)))), 3)
        if new != old:
            multipliers[seg] = new
            changes[seg] = {"from": old, "to": new, "sent": v["sent"], "positive": v["positive"]}
    path = cfg.root / "config" / "learned.toml"
    lines = ["# Written by `python3 -m engine learn`. Multipliers on segment evidence points, bounded 0.7-1.3.",
             f"# Base reply rate when written: {base:.2%} over {total_sent} sent.", "", "[segments]"]
    lines += [f'{k} = {v}' for k, v in sorted(multipliers.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"base_rate": base, "sent": total_sent, "changes": changes, "stats": stats}


def market_stats(store: Store, days: int = 7) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    prev_cutoff = (datetime.now(timezone.utc) - timedelta(days=2 * days)).isoformat()
    now_ = store.all("created_at >= ?", (cutoff,))
    prev = store.all("created_at >= ? AND created_at < ?", (prev_cutoff, cutoff))
    seg_now = Counter(o.get("segment") or "other" for o in now_)
    seg_prev = Counter(o.get("segment") or "other" for o in prev)
    svc_now = Counter(o.get("service") or "?" for o in now_)
    budgets = [o for o in now_ if o.get("budget_max") or o.get("budget_min")]
    return {
        "new": len(now_), "previous": len(prev),
        "segments": {s: {"now": seg_now[s], "before": seg_prev.get(s, 0)} for s in seg_now},
        "services": dict(svc_now.most_common()),
        "with_budget": len(budgets),
        "budgets": [{"title": o["title"][:60], "min": o.get("budget_min"), "max": o.get("budget_max"),
                     "currency": o.get("currency")} for o in budgets[:15]],
        "rejected": Counter((o.get("reject_reason") or "").split(":")[0] for o in now_ if o["status"] == "rejected"),
    }
