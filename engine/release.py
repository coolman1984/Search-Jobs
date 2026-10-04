"""After approval only: prepare what the owner sends himself.

There is no function in this repository that sends anything. release() produces a packet:
  email    -> outbox/<id>.json with to/subject/body; the agent turns it into a Gmail DRAFT (never sends);
              Gmail send/reply/forward tools are denied in .claude/settings.json.
  platform -> the final text plus the opportunity link; the owner submits by hand on the platform.
  apply    -> the cover text plus the official application link; the owner applies by hand.
Cold outreach also needs: the opt-out line (claims checker), not on the do-not-contact list, and under the
daily ceiling."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .approval import ApprovalError, check_and_record
from .cards import draft_hash
from .config import Config, normalise
from .store import Store, now, today


class ReleaseRefused(Exception):
    pass


def contact_hash(contact: str) -> str:
    return hashlib.sha256(normalise(contact).replace(" ", "").encode("utf-8")).hexdigest()


def direct_sent_today(store: Store) -> int:
    return sum(1 for e in store.events(kind="released")
               if e["ts"][:10] == today() and json.loads(e["data"] or "{}").get("track") == "direct")


def release(cfg: Config, store: Store, opp_id: str, outbox: Path, **verify_kw) -> dict:
    opp = store.get(opp_id)
    if not opp:
        raise ReleaseRefused(f"no opportunity {opp_id}")
    if not opp.get("draft"):
        raise ReleaseRefused(f"{opp_id} has no draft")
    draft = json.loads(opp["draft"])
    # The gate: re-verify the owner's approval of this exact text every time, even if the status says approved.
    try:
        check_and_record(cfg, store, opp_id, **verify_kw)
    except ApprovalError as exc:
        store.event(opp_id, "release_refused", {"reason": str(exc)})
        store.commit()
        raise ReleaseRefused(f"not approved: {exc}") from exc
    if draft_hash(draft) != store.get(opp_id)["draft_hash"]:
        raise ReleaseRefused("draft changed after approval")
    if opp["track"] == "direct":
        if direct_sent_today(store) >= cfg.settings["limits"]["direct_outreach_per_day"]:
            raise ReleaseRefused("daily ceiling for cold messages reached; try tomorrow")
        if draft.get("to") and store.is_dnc(contact_hash(draft["to"])):
            raise ReleaseRefused("this contact asked not to be contacted again")
    packet = {
        "opportunity": opp_id,
        "channel": draft["channel"],
        "to": draft.get("to"),
        "subject": draft.get("subject"),
        "body": draft["body"],
        "link": opp.get("url"),
        "approved_commit": json.loads(store.get(opp_id)["approval"])["commit"],
        "prepared_at": now(),
        "instruction": {
            "email": "Create a Gmail DRAFT with exactly this to/subject/body. Never send it.",
            "platform": "Show the owner this text and the link; he submits it himself.",
            "apply": "Show the owner this text and the official link; he applies himself.",
        }[draft["channel"]],
    }
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / f"{opp_id}.json").write_text(json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8")
    store.set_status(opp_id, "ready", f"packet for {draft['channel']}")
    store.event(opp_id, "released", {"channel": draft["channel"], "track": opp["track"]})
    store.commit()
    return packet


def do_not_contact(store: Store, contact: str, reason: str = "asked not to be contacted") -> str:
    """Record an opt-out immediately. Stored as a hash: the list itself holds no addresses."""
    h = contact_hash(contact)
    store.add_dnc(h, reason)
    for opp in store.all("status IN ('awaiting_approval','approved','ready')"):
        d = json.loads(opp["draft"]) if opp.get("draft") else {}
        if d.get("to") and contact_hash(d["to"]) == h:
            store.set_status(opp["id"], "archived", "contact opted out")
    store.commit()
    return h
