"""SQLite working store plus a plain-JSON copy in state/ that survives ephemeral cloud containers.

The database is rebuilt from state/ at the start of every run (import_state) and written back at the end
(export_state). state/ is the reviewable record; data/ (the .db) is a cache and is never committed.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

STATUSES = {
    "new": "جديدة",
    "studied": "مدروسة",
    "awaiting_approval": "بانتظار موافقتك",
    "approved": "موافَق عليها",
    "ready": "جاهزة للإرسال (مسودة/نص)",
    "sent": "أُرسلت",
    "replied": "رد",
    "meeting": "اجتماع",
    "quoted": "عرض سعر",
    "won": "تعاقد",
    "lost": "مرفوضة من العميل",
    "rejected": "مستبعدة",
    "archived": "مؤرشفة",
}

# Allowed moves. "approved" can only be entered by approval.mark_approved after a verified approval.
TRANSITIONS = {
    "new": {"studied", "rejected", "archived"},
    "studied": {"awaiting_approval", "rejected", "archived", "studied"},
    "awaiting_approval": {"approved", "studied", "rejected", "archived"},
    "approved": {"ready", "awaiting_approval", "archived"},
    "ready": {"sent", "awaiting_approval", "archived"},
    "sent": {"replied", "lost", "archived", "awaiting_approval"},
    "replied": {"meeting", "quoted", "lost", "won", "awaiting_approval", "archived"},
    "meeting": {"quoted", "won", "lost", "awaiting_approval", "archived"},
    "quoted": {"won", "lost", "awaiting_approval", "archived"},
    "won": {"archived"},
    "lost": {"archived", "studied"},
    "rejected": {"new", "archived"},
    "archived": {"new"},
}

# Fields that hold personal or strategic data. They are kept out of git while the repository is public.
PRIVATE_FIELDS = ("contact", "enrichment", "draft")

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
  id TEXT PRIMARY KEY,
  track TEXT NOT NULL CHECK (track IN ('job','freelance','direct')),
  title TEXT NOT NULL,
  org TEXT,
  location TEXT,
  remote INTEGER DEFAULT 0,
  url TEXT,
  description TEXT,
  posted_at TEXT,
  budget_min REAL,
  budget_max REAL,
  currency TEXT,
  language TEXT,
  segment TEXT,
  service TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  score REAL,
  score_reason TEXT,
  score_breakdown TEXT,
  reject_reason TEXT,
  fingerprint TEXT NOT NULL,
  contact TEXT,
  enrichment TEXT,
  draft TEXT,
  draft_hash TEXT,
  approval TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opp_fp ON opportunities(fingerprint);
CREATE TABLE IF NOT EXISTS sightings (
  opportunity_id TEXT NOT NULL,
  source TEXT NOT NULL,
  source_url TEXT,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (opportunity_id, source, source_url)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id TEXT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  data TEXT
);
CREATE TABLE IF NOT EXISTS fetch_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  ts TEXT NOT NULL,
  url TEXT,
  status TEXT,
  items INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS do_not_contact (
  contact_hash TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  reason TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class TransitionError(Exception):
    pass


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ------------------------------------------------------------------ opportunities
    def get(self, opp_id: str) -> dict | None:
        row = self.db.execute("SELECT * FROM opportunities WHERE id=?", (opp_id,)).fetchone()
        return dict(row) if row else None

    def find_by_fingerprint(self, fp: str) -> dict | None:
        row = self.db.execute("SELECT * FROM opportunities WHERE fingerprint=?", (fp,)).fetchone()
        return dict(row) if row else None

    def all(self, where: str = "1=1", params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self.db.execute(f"SELECT * FROM opportunities WHERE {where}", params)]

    def insert(self, opp: dict) -> None:
        opp = dict(opp)
        opp.setdefault("status", "new")
        opp.setdefault("created_at", now())
        opp["updated_at"] = now()
        cols = ",".join(opp.keys())
        marks = ",".join("?" for _ in opp)
        self.db.execute(f"INSERT INTO opportunities ({cols}) VALUES ({marks})", tuple(opp.values()))
        self.event(opp["id"], "created", {"track": opp["track"]})

    def update(self, opp_id: str, **fields) -> None:
        if "status" in fields:
            raise TransitionError("use set_status() to change a status")
        if not fields:
            return
        fields["updated_at"] = now()
        sets = ",".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE opportunities SET {sets} WHERE id=?", (*fields.values(), opp_id))

    def set_status(self, opp_id: str, new: str, note: str = "", *, _approval_gate: bool = False) -> None:
        if new not in STATUSES:
            raise TransitionError(f"unknown status '{new}'")
        if new == "approved" and not _approval_gate:
            raise TransitionError("'approved' can only be set by the approval gate after a verified approval")
        opp = self.get(opp_id)
        if not opp:
            raise TransitionError(f"no opportunity {opp_id}")
        old = opp["status"]
        if new != old and new not in TRANSITIONS[old]:
            raise TransitionError(f"cannot move {opp_id} from '{old}' to '{new}'")
        self.db.execute("UPDATE opportunities SET status=?, updated_at=? WHERE id=?", (new, now(), opp_id))
        self.event(opp_id, "status", {"from": old, "to": new, "note": note})

    # ------------------------------------------------------------------ side tables
    def add_sighting(self, opp_id: str, source: str, url: str | None) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO sightings (opportunity_id, source, source_url, seen_at) VALUES (?,?,?,?)",
            (opp_id, source, url or "", now()),
        )

    def sightings(self, opp_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM sightings WHERE opportunity_id=?", (opp_id,))]

    def event(self, opp_id: str | None, kind: str, data: dict | None = None) -> None:
        self.db.execute(
            "INSERT INTO events (opportunity_id, ts, kind, data) VALUES (?,?,?,?)",
            (opp_id, now(), kind, json.dumps(data or {}, ensure_ascii=False)),
        )

    def events(self, opp_id: str | None = None, kind: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM events WHERE 1=1", []
        if opp_id:
            sql += " AND opportunity_id=?"
            params.append(opp_id)
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY id", params)]

    def log_fetch(self, source: str, url: str, status: str, items: int = 0) -> None:
        self.db.execute(
            "INSERT INTO fetch_log (source, ts, url, status, items) VALUES (?,?,?,?,?)",
            (source, now(), url, status, items),
        )

    def fetches_today(self, source: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM fetch_log WHERE source=? AND substr(ts,1,10)=? AND status!='skipped' ORDER BY id",
            (source, today()))]

    def commit(self) -> None:
        self.db.commit()

    # ------------------------------------------------------------------ state/ round trip
    def export_state(self, state_dir: Path, *, public: bool) -> int:
        """Write one JSON file per opportunity. In public mode personal/strategic fields and the whole
        direct-outreach track are left out (they are kept in private/ by the caller instead)."""
        opp_dir = state_dir / "opportunities"
        opp_dir.mkdir(parents=True, exist_ok=True)
        written = set()
        for opp in self.all():
            if public and opp["track"] == "direct":
                continue
            record = dict(opp)
            if public:
                for f in PRIVATE_FIELDS:
                    record[f] = None
            record["sightings"] = [
                {k: s[k] for k in ("source", "source_url", "seen_at")} for s in self.sightings(opp["id"])
            ]
            record["events"] = [
                {k: e[k] for k in ("ts", "kind", "data")} for e in self.events(opp["id"])
            ]
            path = opp_dir / f"{opp['id']}.json"
            path.write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            written.add(path.name)
        for stale in opp_dir.glob("*.json"):
            if stale.name not in written:
                stale.unlink()
        fetch_rows = [dict(r) for r in self.db.execute(
            "SELECT source, ts, url, status, items FROM fetch_log ORDER BY id DESC LIMIT 500")]
        (state_dir / "fetch_log.json").write_text(
            json.dumps(list(reversed(fetch_rows)), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        dnc = [dict(r) for r in self.db.execute("SELECT * FROM do_not_contact ORDER BY contact_hash")]
        (state_dir / "do_not_contact.json").write_text(
            json.dumps(dnc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return len(written)

    def import_state(self, state_dir: Path) -> int:
        count = 0
        opp_dir = state_dir / "opportunities"
        for path in sorted(opp_dir.glob("*.json")) if opp_dir.exists() else []:
            rec = json.loads(path.read_text(encoding="utf-8"))
            sightings = rec.pop("sightings", [])
            events = rec.pop("events", [])
            cols = [c for c in rec if c in _COLUMNS]
            self.db.execute(
                f"INSERT OR REPLACE INTO opportunities ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                tuple(rec[c] for c in cols),
            )
            for s in sightings:
                self.db.execute("INSERT OR IGNORE INTO sightings VALUES (?,?,?,?)",
                                (rec["id"], s["source"], s.get("source_url", ""), s["seen_at"]))
            self.db.execute("DELETE FROM events WHERE opportunity_id=?", (rec["id"],))
            for e in events:
                self.db.execute("INSERT INTO events (opportunity_id, ts, kind, data) VALUES (?,?,?,?)",
                                (rec["id"], e["ts"], e["kind"], e.get("data")))
            count += 1
        fl = state_dir / "fetch_log.json"
        if fl.exists():
            self.db.execute("DELETE FROM fetch_log")
            for r in json.loads(fl.read_text(encoding="utf-8")):
                self.db.execute("INSERT INTO fetch_log (source, ts, url, status, items) VALUES (?,?,?,?,?)",
                                (r["source"], r["ts"], r.get("url"), r.get("status"), r.get("items", 0)))
        dnc = state_dir / "do_not_contact.json"
        if dnc.exists():
            for r in json.loads(dnc.read_text(encoding="utf-8")):
                self.db.execute("INSERT OR IGNORE INTO do_not_contact VALUES (?,?,?)",
                                (r["contact_hash"], r["ts"], r.get("reason")))
        self.commit()
        return count

    def private_payload(self) -> dict:
        """Private fields of every opportunity, plus whole direct-track records (see engine/privacy.py)."""
        payload = {}
        for o in self.all():
            if any(o[f] for f in PRIVATE_FIELDS) or o["track"] == "direct":
                entry = {f: o[f] for f in PRIVATE_FIELDS}
                if o["track"] == "direct":
                    entry["full"] = o
                payload[o["id"]] = entry
        return payload

    def apply_private(self, payload: dict) -> int:
        applied = 0
        for oid, fields in payload.items():
            fields = dict(fields)
            full = fields.pop("full", None)
            if full and not self.get(oid):
                cols = [c for c in full if c in _COLUMNS]
                self.db.execute(
                    f"INSERT OR REPLACE INTO opportunities ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                    tuple(full[c] for c in cols))
            if self.get(oid) and fields:
                sets = ",".join(f"{k}=?" for k in fields)
                self.db.execute(f"UPDATE opportunities SET {sets} WHERE id=?", (*fields.values(), oid))
                applied += 1
        self.commit()
        return applied

    # ------------------------------------------------------------------ do not contact
    def add_dnc(self, contact_hash: str, reason: str = "") -> None:
        self.db.execute("INSERT OR IGNORE INTO do_not_contact VALUES (?,?,?)", (contact_hash, now(), reason))

    def is_dnc(self, contact_hash: str) -> bool:
        return self.db.execute("SELECT 1 FROM do_not_contact WHERE contact_hash=?", (contact_hash,)).fetchone() is not None


_COLUMNS = {
    "id", "track", "title", "org", "location", "remote", "url", "description", "posted_at", "budget_min",
    "budget_max", "currency", "language", "segment", "service", "status", "score", "score_reason",
    "score_breakdown", "reject_reason", "fingerprint", "contact", "enrichment", "draft", "draft_hash", "approval",
    "created_at", "updated_at",
}
