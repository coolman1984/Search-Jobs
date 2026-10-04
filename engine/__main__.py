"""Command line: python3 -m engine <command>. Every command prints a short result; mutating commands save
state/ (and the private part according to engine/privacy.py) before they exit."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import approval, cards, followup, privacy, release, report, scoring, solutions
from .collectors import COLLECTORS
from .config import ConfigError, load, name_hash
from .ingest import ingest_dir, ingest_payload, parse_alert
from .normalize import upsert
from .polite import Fetcher, Refused
from .store import STATUSES, Store, TransitionError, today

CONTEXT = os.environ.get("SEARCHJOBS_CONTEXT", "manual")  # "scheduled" inside routines


def _mode(cfg) -> str:
    cache = cfg.data_dir / "mode.json"
    try:
        c = json.loads(cache.read_text())
        if time.time() - c["at"] < 3600:
            return c["mode"]
    except Exception:
        pass
    m = privacy.mode(cfg)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"mode": m, "at": time.time()}))
    return m


@contextmanager
def session(save: bool = True):
    cfg = load()
    store = Store(cfg.data_dir / "searchjobs.db")
    mode = _mode(cfg)
    if not store.all():
        store.import_state(cfg.state_dir)
        store.apply_private(privacy.load(cfg, mode))
    try:
        yield cfg, store, mode
    finally:
        store.commit()
        if save:
            store.export_state(cfg.state_dir, public=(mode != "private"))
            if mode != "private":
                privacy.save(cfg, store.private_payload(), mode)


def log(cfg, line: str) -> None:
    path = cfg.root / "logs" / f"{today()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%H:%M")
    with path.open("a", encoding="utf-8") as fh:
        if path.stat().st_size == 0:
            fh.write(f"# سجل التشغيل · {today()}\n\n")
        fh.write(f"- {stamp} UTC · {CONTEXT} · {line}\n")


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1) if not isinstance(obj, str) else obj)


# ------------------------------------------------------------------------------------------- commands
def cmd_gather(a):
    with session() as (cfg, store, mode):
        fetcher = Fetcher(cfg, store)
        stats = {"created": 0, "merged": 0, "source_errors": [], "by_source": {}}
        for sid, fn in COLLECTORS.items():
            if a.only and sid not in a.only:
                continue
            spec = cfg.sources.get(sid, {})
            if sid == "osm" and mode != "private":
                continue  # direct outreach waits for a private repository
            if sid in ("greenhouse", "lever") and not (spec.get("boards") or spec.get("companies")):
                continue
            try:
                records = fn(cfg, fetcher)
            except Refused as exc:
                stats["source_errors"].append(f"{sid}: {exc}")
                continue
            except Exception as exc:  # a broken source never stops the run
                stats["source_errors"].append(f"{sid}: {type(exc).__name__}: {str(exc)[:120]}")
                continue
            c = m = 0
            for rec in records:
                _, new = upsert(store, rec, sid)
                c += new
                m += not new
            stats["by_source"][sid] = {"created": c, "merged": m}
            stats["created"] += c
            stats["merged"] += m
        inbox = ingest_dir(cfg, store, cfg.root / "inbox")
        stats["created"] += inbox["created"]
        stats["merged"] += inbox["merged"]
        stats["inbox"] = inbox
        stats.update(scoring.score_all(cfg, store))
        stats["archived"] = followup.archive_stale(cfg, store)
        stats["approvals_fetch"] = approval.fetch_approvals(cfg)
        stats["approvals"] = approval.sweep(cfg, store)
        stats["mode"] = mode
        stats["top"] = [{"id": o["id"], "score": o["score"], "title": o["title"], "org": o.get("org"),
                         "track": o["track"], "status": o["status"], "url": o.get("url")}
                        for o in report.top(cfg, store)]
        (cfg.data_dir / "last_gather.json").write_text(json.dumps(stats, ensure_ascii=False, default=str))
        log(cfg, f"gather: {stats['created']} new, {stats['merged']} merged, {stats.get('rejected', 0)} rejected, "
                 f"{len(stats['source_errors'])} source errors, mode={mode}")
        out(stats)


def cmd_ingest(a):
    with session() as (cfg, store, mode):
        p = Path(a.path)
        res = ingest_dir(cfg, store, p) if p.is_dir() else ingest_payload(cfg, store, json.loads(p.read_text()))
        scoring.score_all(cfg, store, only_new=True)
        log(cfg, f"ingest {p.name}: {res}")
        out(res)


def cmd_parse_alert(a):
    cfg = load()
    body = Path(a.body_file).read_text(encoding="utf-8")
    items = parse_alert(a.sender, a.subject or "", body)
    payload = {"source": "gmail_alerts", "query": f"alert from {a.sender}", "items": items}
    inbox = cfg.root / "inbox"
    inbox.mkdir(exist_ok=True)
    name = inbox / f"alert-{int(time.time() * 1000)}.json"
    name.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.delete_body:
        Path(a.body_file).unlink(missing_ok=True)
    out({"items": len(items), "file": str(name.relative_to(cfg.root))})


def cmd_score(a):
    with session() as (cfg, store, mode):
        out(scoring.score_all(cfg, store))


def cmd_top(a):
    with session(save=False) as (cfg, store, mode):
        out([{"id": o["id"], "score": o["score"], "title": o["title"], "org": o.get("org"), "status": o["status"],
              "segment": o.get("segment"), "service": o.get("service"), "url": o.get("url")}
             for o in report.top(cfg, store, a.n)])


def cmd_show(a):
    with session(save=False) as (cfg, store, mode):
        opp = store.get(a.id)
        if not opp:
            sys.exit(f"no opportunity {a.id}")
        opp["sightings"] = store.sightings(a.id)
        out(opp)


def cmd_card(a):
    with session(save=False) as (cfg, store, mode):
        text = cards.render_card(cfg, store, a.id)
        if a.write:
            folder = (cfg.root / "cards") if mode == "private" else (cfg.private_dir / "cards")
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{a.id}.md").write_text(text, encoding="utf-8")
        out(text)


def cmd_enrich(a):
    with session() as (cfg, store, mode):
        data = json.loads(Path(a.file).read_text(encoding="utf-8"))
        allowed = {"who", "size", "presence", "pain", "solution", "angle", "channel", "prototype", "risks",
                   "facts", "solutions", "sources", "contact"}
        bad = set(data) - allowed
        if bad:
            sys.exit(f"unknown enrichment fields: {', '.join(sorted(bad))}")
        contact = data.pop("contact", None)
        fields = {"enrichment": json.dumps(data, ensure_ascii=False)}
        if contact:
            if set(contact) - {"email", "name", "role", "source"}:
                sys.exit("contact may hold only email, name, role and source (business contacts published for business)")
            fields["contact"] = json.dumps(contact, ensure_ascii=False)
        store.update(a.id, **fields)
        if store.get(a.id)["status"] == "new":
            store.set_status(a.id, "studied", "enriched")
        log(cfg, f"enrich {a.id}")
        out({"ok": a.id})


def cmd_draft(a):
    with session() as (cfg, store, mode):
        opp = store.get(a.id)
        if not opp:
            sys.exit(f"no opportunity {a.id}")
        if a.file:
            d = json.loads(Path(a.file).read_text(encoding="utf-8"))
            base = cards.baseline_draft(cfg, opp)
            draft = {**base, **{k: d[k] for k in ("subject", "body", "lang", "evidence") if k in d}, "author": "agent"}
        else:
            draft = cards.baseline_draft(cfg, opp)
        try:
            res = cards.set_draft(cfg, store, a.id, draft)
        except cards.DraftRefused as exc:
            log(cfg, f"draft refused {a.id}: {exc}")
            sys.exit(f"REFUSED: {exc}")
        res["approval_link"] = approval.approval_link(cfg, a.id, res["hash"])
        log(cfg, f"draft {a.id} {res['hash'][:12]}")
        out(res)


def cmd_approvals(a):
    with session() as (cfg, store, mode):
        fetched = approval.fetch_approvals(cfg)
        res = approval.sweep(cfg, store)
        res["fetch"] = fetched
        log(cfg, f"approvals: {len(res['approved'])} approved, {len(res['problems'])} problems")
        out(res)


def cmd_release(a):
    with session() as (cfg, store, mode):
        approval.fetch_approvals(cfg)
        try:
            packet = release.release(cfg, store, a.id, cfg.root / "outbox")
        except release.ReleaseRefused as exc:
            log(cfg, f"release refused {a.id}: {exc}")
            sys.exit(f"REFUSED: {exc}")
        log(cfg, f"release {a.id} via {packet['channel']}")
        out(packet)


def cmd_status(a):
    with session() as (cfg, store, mode):
        try:
            store.set_status(a.id, a.status, a.note or "")
        except TransitionError as exc:
            sys.exit(f"REFUSED: {exc}")
        log(cfg, f"status {a.id} -> {a.status}")
        out({"id": a.id, "status": a.status, "label": STATUSES[a.status]})


def cmd_dnc(a):
    with session() as (cfg, store, mode):
        h = release.do_not_contact(store, a.contact, a.reason or "asked not to be contacted")
        log(cfg, "do-not-contact added")
        out({"hash": h})


def cmd_solution(a):
    with session() as (cfg, store, mode):
        base = (cfg.root / "solutions") if mode == "private" else (cfg.private_dir / "solutions")
        folder = solutions.create(cfg, store, a.id, base)
        log(cfg, f"solution folder for {a.id}")
        out({"folder": str(folder.relative_to(cfg.root))})


def cmd_report(a):
    with session() as (cfg, store, mode):
        stats = {}
        try:
            stats = json.loads((cfg.data_dir / "last_gather.json").read_text())
        except Exception:
            pass
        if a.idea:
            stats["idea"] = a.idea
        public = report.daily(cfg, store, mode=mode, stats=stats, part="public")
        full = report.daily(cfg, store, mode=mode, stats=stats, part="full")
        rdir = cfg.root / "reports"
        rdir.mkdir(exist_ok=True)
        (rdir / f"daily-{today()}.md").write_text(full if mode == "private" else public, encoding="utf-8")
        pdir = cfg.private_dir
        pdir.mkdir(exist_ok=True)
        (pdir / f"daily-full-{today()}.md").write_text(full, encoding="utf-8")
        log(cfg, f"daily report written (mode={mode})")
        out({"mode": mode, "issue_body": f"reports/daily-{today()}.md",
             "private_full": None if mode == "private" else f"private/daily-full-{today()}.md"})


def cmd_weekly(a):
    with session() as (cfg, store, mode):
        learned = followup.learn(cfg, store)
        text = report.weekly(cfg, store, learned)
        rdir = cfg.root / "reports"
        rdir.mkdir(exist_ok=True)
        path = rdir / f"weekly-{today()}.md"
        path.write_text(text, encoding="utf-8")
        log(cfg, f"weekly report, {len(learned['changes'])} weight changes")
        out({"file": str(path.relative_to(cfg.root)), "changes": learned["changes"]})


def cmd_due(a):
    with session(save=False) as (cfg, store, mode):
        out(followup.due(cfg, store))


def cmd_idea(a):
    cfg = load()
    path = cfg.root / "IDEAS.md"
    text = path.read_text(encoding="utf-8")
    header = f"## {today()}"
    if header not in text:
        text = text.rstrip() + f"\n\n{header}\n"
    n = len([ln for ln in text.split(header, 1)[1].splitlines() if ln[:1].isdigit()]) + 1
    text = text.rstrip() + f"\n{n}. {a.text.strip()}\n"
    path.write_text(text, encoding="utf-8")
    log(cfg, "idea added")
    out({"ok": True})


def cmd_blocklist(a):
    cfg = load()
    path = cfg.root / "config" / "blocklist.toml"
    h = name_hash(a.name)
    text = path.read_text(encoding="utf-8")
    if h in text:
        out({"exists": True})
        return
    marker = f"[{a.scope}]\nhashes = [\n"
    if marker not in text:
        sys.exit("unexpected blocklist.toml layout")
    path.write_text(text.replace(marker, marker + f'  "{h}",\n', 1), encoding="utf-8")
    out({"added": a.scope})


def cmd_link(a):
    with session(save=False) as (cfg, store, mode):
        opp = store.get(a.id)
        if not opp or not opp.get("draft_hash"):
            sys.exit("no draft")
        out(approval.approval_link(cfg, a.id, opp["draft_hash"]))


def cmd_doctor(a):
    cfg = load()
    checks = {"config": "ok", "python": sys.version.split()[0]}
    import shutil
    import subprocess
    checks["git"] = bool(shutil.which("git"))
    checks["gpg"] = bool(shutil.which("gpg"))
    checks["openssl"] = bool(shutil.which("openssl"))
    checks["web_flow_keys"] = sorted(p.name for p in (cfg.root / "config" / "keys").glob("*.asc"))
    checks["mode"] = privacy.mode(cfg)
    checks["encryption_key"] = bool(os.environ.get(privacy.KEY_ENV))
    reach = {}
    import urllib.request
    for sid, spec in cfg.sources.items():
        url = spec.get("url") or (spec.get("urls") or [None])[0]
        if spec["color"] == "red" or not url or "{" in url:
            continue
        host = url.split("/")[2]
        try:
            urllib.request.urlopen(urllib.request.Request(f"https://{host}/", method="HEAD",
                                                          headers={"User-Agent": cfg.settings["http"]["user_agent"]}),
                                   timeout=10)
            reach[host] = "ok"
        except Exception as exc:
            reach[host] = "blocked" if "403" in str(exc) or "Tunnel" in str(exc) else f"{type(exc).__name__}"
    checks["network"] = reach
    out(checks)


def cmd_export(a):
    with session() as (cfg, store, mode):
        out({"mode": mode, "opportunities": len(store.all())})


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m engine", description="Search-Jobs engine")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("gather", help="collect, ingest inbox/, merge, score, check approvals")
    s.add_argument("--only", nargs="*")
    s.set_defaults(fn=cmd_gather)
    s = sub.add_parser("ingest", help="ingest a connector JSON file or a folder")
    s.add_argument("path")
    s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("parse-alert", help="turn one alert email body into an inbox/ file")
    s.add_argument("--sender", required=True)
    s.add_argument("--subject")
    s.add_argument("--body-file", required=True)
    s.add_argument("--delete-body", action="store_true")
    s.set_defaults(fn=cmd_parse_alert)
    sub.add_parser("score").set_defaults(fn=cmd_score)
    s = sub.add_parser("top")
    s.add_argument("-n", type=int, default=None)
    s.set_defaults(fn=cmd_top)
    s = sub.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("card")
    s.add_argument("id")
    s.add_argument("--write", action="store_true")
    s.set_defaults(fn=cmd_card)
    s = sub.add_parser("enrich")
    s.add_argument("id")
    s.add_argument("--file", required=True)
    s.set_defaults(fn=cmd_enrich)
    s = sub.add_parser("draft", help="write the baseline draft, or --file with {subject, body, lang}")
    s.add_argument("id")
    s.add_argument("--file")
    s.set_defaults(fn=cmd_draft)
    sub.add_parser("approvals", help="fetch and verify the owner's approvals").set_defaults(fn=cmd_approvals)
    s = sub.add_parser("approval-link")
    s.add_argument("id")
    s.set_defaults(fn=cmd_link)
    s = sub.add_parser("release", help="after approval only: prepare the packet the owner sends himself")
    s.add_argument("id")
    s.set_defaults(fn=cmd_release)
    s = sub.add_parser("status")
    s.add_argument("id")
    s.add_argument("status", choices=[k for k in STATUSES if k != "approved"])
    s.add_argument("--note")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("dnc", help="record a do-not-contact request")
    s.add_argument("contact")
    s.add_argument("--reason")
    s.set_defaults(fn=cmd_dnc)
    s = sub.add_parser("solution")
    s.add_argument("id")
    s.set_defaults(fn=cmd_solution)
    s = sub.add_parser("report")
    s.add_argument("--idea")
    s.set_defaults(fn=cmd_report)
    sub.add_parser("weekly").set_defaults(fn=cmd_weekly)
    sub.add_parser("due").set_defaults(fn=cmd_due)
    s = sub.add_parser("idea")
    s.add_argument("text")
    s.set_defaults(fn=cmd_idea)
    s = sub.add_parser("blocklist")
    s.add_argument("action", choices=["add"])
    s.add_argument("name")
    s.add_argument("--scope", choices=["employer", "competitor"], required=True)
    s.set_defaults(fn=cmd_blocklist)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    sub.add_parser("export").set_defaults(fn=cmd_export)
    a = p.parse_args(argv)
    try:
        a.fn(a)
    except ConfigError as exc:
        sys.exit(f"CONFIG: {exc}")


if __name__ == "__main__":
    main()
