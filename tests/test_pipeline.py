"""Collect → merge → score → card → draft → report, offline, with synthetic fixtures."""
from __future__ import annotations

import json
import os
import sys
import unittest
import urllib.error
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.helpers import FIXTURES, fake_opener, load_cfg, new_store, opp, temp_root

from engine import cards, claims, collectors, followup, ingest, privacy, report, scoring
from engine.normalize import canonical_url, make_record, upsert
from engine.polite import Fetcher, Refused
from engine.store import Store, TransitionError


def fetcher(cfg, store, mapping):
    return Fetcher(cfg, store, sleep=lambda s: None, opener=fake_opener(mapping))


class ConfigAndPoliteTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_cfg(temp_root())
        self.store = new_store()

    def test_red_sources_and_domains_are_refused(self):
        f = fetcher(self.cfg, self.store, {})
        with self.assertRaises(Refused):
            f.get("linkedin", "https://www.linkedin.com/jobs")
        with self.assertRaises(Refused):
            f.get("remotive", "https://www.upwork.com/jobs/x")  # red domain through a green source
        with self.assertRaises(Refused):
            f.get("indeed", "https://example.com")  # connectors are not fetched over HTTP

    def test_daily_limit_and_block_stop_the_source(self):
        f = fetcher(self.cfg, self.store, {"remotive.com": b"{}"})
        f.get("remotive", "https://remotive.com/api/remote-jobs?limit=100")
        f.get("remotive", "https://remotive.com/api/remote-jobs?limit=100")
        with self.assertRaises(Refused):
            f.get("remotive", "https://remotive.com/api/remote-jobs?limit=100")  # limit is 2 a day
        err = urllib.error.HTTPError("https://remoteok.com/api", 429, "Too Many", {}, None)
        f2 = fetcher(self.cfg, self.store, {"remoteok.com": err})
        with self.assertRaises(Refused):
            f2.get("remoteok", "https://remoteok.com/api")

    def test_yellow_source_honours_robots(self):
        f = fetcher(self.cfg, self.store, {"robots.txt": b"User-agent: *\nDisallow: /private\n",
                                           "example.org/about": b"<html>ok</html>"})
        self.assertEqual(f.get("company_site", "https://example.org/about"), b"<html>ok</html>")
        with self.assertRaises(Refused):
            f.get("company_site", "https://example.org/private/team")


class CollectAndMergeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_cfg(temp_root())
        self.store = new_store()
        self.f = fetcher(self.cfg, self.store, {
            "remotive.com": (FIXTURES / "remotive.json").read_bytes(),
            "remoteok.com": (FIXTURES / "remoteok.json").read_bytes(),
            "weworkremotely.com": (FIXTURES / "wwr.rss").read_bytes(),
        })

    def test_collectors_keep_only_relevant_items(self):
        rem = collectors.remotive(self.cfg, self.f)
        self.assertEqual({r["title"] for r in rem}, {"Finance Automation Analyst", "n8n Workflow Automation Contractor"})
        self.assertEqual({r["track"] for r in rem}, {"job", "freelance"})
        self.assertNotIn("utm_source", rem[0]["url"])
        wwr = collectors.weworkremotely(self.cfg, self.f)
        self.assertEqual({r["org"] for r in wwr}, {"DataCo"})  # both feeds return the same fixture here

    def test_same_job_from_two_sources_becomes_one_record(self):
        for sid in ("remotive", "remoteok"):
            for rec in getattr(collectors, sid)(self.cfg, self.f):
                upsert(self.store, rec, sid)
        ledgerly = self.store.all("org='Ledgerly'")
        self.assertEqual(len(ledgerly), 1)
        self.assertEqual({s["source"] for s in self.store.sightings(ledgerly[0]["id"])}, {"remotive", "remoteok"})
        self.assertEqual(ledgerly[0]["budget_max"], 60000)  # filled from the second source

    def test_canonical_url_strips_tracking(self):
        self.assertEqual(canonical_url("https://X.com/a/?utm_source=x&id=5&trackingId=9"), "https://x.com/a?id=5")


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_cfg(temp_root())
        self.store = new_store()

    def test_connector_payload_is_ingested_and_red_is_refused(self):
        res = ingest.ingest_payload(self.cfg, self.store, {"source": "indeed", "items": [
            {"title": "Automation Engineer", "org": "Henkel", "location": "Cairo", "url": "https://to.indeed.com/x"}]})
        self.assertEqual(res["created"], 1)
        with self.assertRaises(ingest.IngestError):
            ingest.ingest_payload(self.cfg, self.store, {"source": "linkedin", "items": []})
        with self.assertRaises(ingest.IngestError):
            ingest.ingest_payload(self.cfg, self.store, {"source": "remotive", "items": []})

    def test_linkedin_alert_keeps_four_fields_only(self):
        items = ingest.parse_alert("jobalerts-noreply@linkedin.com", "automation", (FIXTURES / "linkedin_alert.txt").read_text())
        self.assertEqual([i["title"] for i in items], ["Senior Business Process Automation Lead", "AI Transformation Manager"])
        self.assertEqual(items[0]["org"], "Nile Logistics Group")
        self.assertEqual(items[1]["location"], "Riyadh, Saudi Arabia")
        self.assertNotIn("trackingId", items[0]["url"])
        self.assertTrue(all(set(i) <= {"track", "title", "org", "location", "url", "description"} for i in items))
        self.assertEqual(items[0]["url"], "https://www.linkedin.com/comm/jobs/view/4012345678")

    def test_upwork_alert(self):
        items = ingest.parse_alert("Upwork <donotreply@upwork.com>", "saved search", (FIXTURES / "upwork_alert.txt").read_text())
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["track"], "freelance")
        self.assertEqual(items[0]["title"], "Automate weekly sales report from 5 Excel files")
        self.assertEqual(items[1]["title"], "Build a Power BI dashboard for a small distributor")
        self.assertEqual(items[1]["budget_max"], "800")


def add_test_blocklist(cfg):
    """Made-up names so the public test suite never names the real employer (whose name is stored only as a hash)."""
    from engine.config import name_hash
    cfg.blocklist["employer"]["hashes"].append(name_hash("Nile Employer Group"))
    cfg.blocklist["employer"]["hashes"].append(name_hash("NileEmp"))
    cfg.blocklist["competitor"]["hashes"].append(name_hash("Rival Electronics"))
    return cfg


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.cfg = add_test_blocklist(load_cfg(temp_root()))

    def test_real_blocklist_is_not_empty(self):
        real = load_cfg(temp_root())
        self.assertGreaterEqual(len(real.blocklist["employer"]["hashes"]), 1)
        self.assertGreaterEqual(len(real.blocklist["competitor"]["hashes"]), 1)

    def test_score_is_bounded_and_explained(self):
        r = scoring.score(self.cfg, opp(posted_at=date.today().isoformat(), budget_max=1500, currency="USD"))
        self.assertTrue(0 <= r["score"] <= 100)
        self.assertGreater(r["score"], 55)
        self.assertIn("قوية في", r["reason"])
        weak = scoring.score(self.cfg, opp(title="Office cleaner", description="Clean the office daily."))
        self.assertLess(weak["score"], r["score"])

    def test_employer_and_competitors_are_blocked(self):
        emp = opp(track="job", org="Nile Employer Group Egypt")
        self.assertIn("employer", scoring.blocked(self.cfg, emp))
        comp_job = opp(track="job", org="Rival Electronics")
        comp_free = opp(track="freelance", org="Rival Electronics")
        self.assertIsNone(scoring.blocked(self.cfg, comp_job))
        self.assertIn("competitor", scoring.blocked(self.cfg, comp_free))
        mention = opp(track="freelance", org="Acme", description="We supply parts to NileEmp and need Excel automation.")
        self.assertIn("employer", scoring.blocked(self.cfg, mention))

    def test_red_flags_and_locations(self):
        self.assertTrue(scoring.red_flag(self.cfg, "Unpaid trial: scrape LinkedIn profiles"))
        self.assertFalse(scoring.job_location_ok(self.cfg, {"remote": 1, "location": "US only", "description": ""}))
        self.assertTrue(scoring.job_location_ok(self.cfg, {"remote": 0, "location": "Riyadh", "description": ""}))

    def test_job_level_and_nationality_filters(self):
        self.assertIn("junior", scoring.job_filter(self.cfg, opp(track="job", title="Finance Intern - Automation", location="Cairo")))
        self.assertIn("nationals", scoring.job_filter(self.cfg, opp(track="job", title="Head of Operations - Great Opportunity for Saudi Nationals", location="Riyadh")))
        self.assertIsNone(scoring.job_filter(self.cfg, opp(track="job", title="AI Transformation Manager", location="Riyadh")))
        lead = scoring.score(self.cfg, opp(track="job", title="Head of AI Transformation and Automation"))
        plain = scoring.score(self.cfg, opp(track="job", title="Office Assistant", description="Answer phones."))
        self.assertGreater(lead["parts"]["evidence_value"], plain["parts"]["evidence_value"])

    def test_salary_and_must_have_gaps(self):
        low = scoring.score(self.cfg, opp(track="job", title="AI Automation Specialist", budget_max=10000, currency="AED"))
        good = scoring.score(self.cfg, opp(track="job", title="AI Automation Lead", budget_max=40000, currency="AED"))
        self.assertEqual(low["parts"]["budget"], 0.0)
        self.assertGreater(good["parts"]["budget"], 10)
        gap = scoring.score(self.cfg, opp(track="job", title="ERP Finance Consultant",
                                          description="Requires 8+ years of hands-on Dynamics 365 F&O. Excel automation and finance."))
        self.assertIn("dynamics 365", gap["notes"]["skills_fit"])

    def test_score_all_rejects_and_records_reason(self):
        store = new_store()
        upsert(store, opp(track="freelance", org="Rival Electronics"), "remotive")
        upsert(store, opp(org="Good Co"), "remotive")
        stats = scoring.score_all(self.cfg, store)
        self.assertEqual(stats["rejected"], 1)
        rejected = store.all("status='rejected'")[0]
        self.assertIn("competitor", rejected["reject_reason"])


class ClaimsAndDraftTests(unittest.TestCase):
    def setUp(self):
        self.cfg = add_test_blocklist(load_cfg(temp_root()))
        self.store = new_store()
        oid, _ = upsert(self.store, opp(), "remotive")
        scoring.score_all(self.cfg, self.store)
        self.opp = self.store.get(oid)

    def test_baseline_drafts_pass_the_checker_in_both_languages(self):
        d = cards.baseline_draft(self.cfg, self.opp)
        self.assertEqual(claims.check(self.cfg, self.opp, d["body"],
                                      extra_allowed=[self.cfg.evidence["services"][self.opp["service"]]["duration"]]), [])
        ar = dict(self.opp, language="ar", description="محتاجين أتمتة تقارير الإكسل الشهرية للحسابات بدل النسخ اليدوي.")
        d_ar = cards.baseline_draft(self.cfg, ar)
        self.assertEqual(claims.check(self.cfg, ar, d_ar["body"],
                                      extra_allowed=[self.cfg.evidence["services"][ar["service"]]["duration"]]), [])

    def test_signature_carries_contact_links_and_still_passes(self):
        self.cfg.settings["contact"]["whatsapp"] = "201001234567"
        self.cfg.settings["contact"]["facebook_page"] = "https://www.facebook.com/example.page"
        d = cards.baseline_draft(self.cfg, self.opp)
        self.assertIn("wa.me/201001234567", d["body"])
        self.assertIn("mflma2030@gmail.com", d["body"])
        self.assertEqual(claims.check(self.cfg, self.opp, d["body"],
                                      extra_allowed=[self.cfg.evidence["services"][self.opp["service"]]["duration"]]), [])
        # a phone number written as plain prose is still treated as an unbacked number
        self.assertTrue(claims.check(self.cfg, self.opp, "Your reports are slow.\n\nCall me on 01001234567."))

    def test_invented_claims_are_refused(self):
        bad = ("You need faster reports.\n\nI have delivered 40 projects for hundreds of clients and I guarantee results. "
               "See github.com/coolman1984/opening-nerp-tcode")
        problems = " | ".join(claims.check(self.cfg, self.opp, bad))
        for word in ("40", "hundreds of clients", "guarantee", "opening-nerp-tcode"):
            self.assertIn(word, problems)

    def test_employer_name_and_self_opening_are_refused(self):
        p = claims.check(self.cfg, self.opp, "I am a finance expert at Nile Employer Group.\n\nLet's talk.")
        self.assertTrue(any("employer" in x for x in p))
        self.assertTrue(any("opens with the owner" in x for x in p))

    def test_cold_message_needs_opt_out(self):
        direct = dict(self.opp, track="direct")
        self.assertTrue(any("opt-out" in x for x in claims.check(self.cfg, direct, "Your bookings are on paper.")))

    def test_card_has_every_section(self):
        text = cards.render_card(self.cfg, self.store, self.opp["id"])
        for heading in ("هم مين", "فين", "الوجع", "الحل المقترح", "إزاي نوصلهم", "إزاي تقدّم", "السعر المقترح",
                        "نموذج/فيديو", "المخاطر", "المصادر"):
            self.assertIn(heading, text)

    def test_set_draft_refuses_invented_claims(self):
        bad = dict(cards.baseline_draft(self.cfg, self.opp), body="Reports take days.\n\nI automated 75 companies.")
        with self.assertRaises(cards.DraftRefused):
            cards.set_draft(self.cfg, self.store, self.opp["id"], bad)
        self.assertIsNone(self.store.get(self.opp["id"])["draft"])

    def test_set_draft_moves_to_awaiting_approval(self):
        res = cards.set_draft(self.cfg, self.store, self.opp["id"], cards.baseline_draft(self.cfg, self.opp))
        self.assertEqual(self.store.get(self.opp["id"])["status"], "awaiting_approval")
        self.assertEqual(len(res["hash"]), 64)


class StoreAndPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_root()
        self.cfg = load_cfg(self.root)
        self.store = new_store()

    def test_transitions_are_enforced(self):
        oid, _ = upsert(self.store, opp(), "remotive")
        with self.assertRaises(TransitionError):
            self.store.set_status(oid, "won")
        self.store.set_status(oid, "studied")
        self.store.set_status(oid, "rejected")

    def test_public_export_holds_no_private_fields_or_direct_track(self):
        oid, _ = upsert(self.store, opp(), "remotive")
        self.store.update(oid, contact=json.dumps({"email": "a@b.example"}), draft='{"body": "secret"}')
        did, _ = upsert(self.store, make_record(track="direct", title="clinic x", org="Clinic X"), "osm")
        n = self.store.export_state(self.root / "state", public=True)
        self.assertEqual(n, 1)
        text = (self.root / "state" / "opportunities" / f"{oid}.json").read_text()
        self.assertNotIn("a@b.example", text)
        self.assertNotIn("secret", text)
        self.assertFalse((self.root / "state" / "opportunities" / f"{did}.json").exists())
        fresh = Store(":memory:")
        self.assertEqual(fresh.import_state(self.root / "state"), 1)
        self.assertEqual(fresh.apply_private(self.store.private_payload()), 2)
        self.assertIn("secret", fresh.get(oid)["draft"])
        self.assertTrue(fresh.get(did))

    def test_encrypted_private_state_round_trip(self):
        os.environ[privacy.KEY_ENV] = "unit-test-key"
        try:
            self.assertEqual(privacy.mode(self.cfg, check_github=False), "encrypted")
            path = privacy.save(self.cfg, {"F-1": {"draft": "نص سري"}}, "encrypted")
            self.assertNotIn("نص سري".encode(), path.read_bytes())
            self.assertEqual(privacy.load(self.cfg, "encrypted")["F-1"]["draft"], "نص سري")
            os.environ[privacy.KEY_ENV] = "wrong"
            with self.assertRaises(privacy.PrivacyError):
                privacy.load(self.cfg, "encrypted")
        finally:
            os.environ.pop(privacy.KEY_ENV, None)
        self.assertEqual(privacy.mode(self.cfg, check_github=False), "public")


class FollowupAndReportTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_cfg(temp_root())
        self.store = new_store()
        self.oid, _ = upsert(self.store, opp(), "remotive")
        scoring.score_all(self.cfg, self.store)
        cards.set_draft(self.cfg, self.store, self.oid, cards.baseline_draft(self.cfg, self.store.get(self.oid)))

    def test_public_report_has_no_draft_text_and_full_report_has_the_link(self):
        public = report.daily(self.cfg, self.store, mode="public", stats={"created": 1}, part="public")
        full = report.daily(self.cfg, self.store, mode="public", stats={"created": 1}, part="full")
        body = json.loads(self.store.get(self.oid)["draft"])["body"]
        self.assertNotIn(body[:60], public)
        self.assertIn(body[:60], full)
        self.assertIn("/new/", full)
        self.assertIn("الخلاصة", public)

    def test_followup_due_after_waiting_days(self):
        self.store.db.execute("UPDATE opportunities SET status='sent' WHERE id=?", (self.oid,))
        self.store.event(self.oid, "status", {"from": "ready", "to": "sent"})
        later = date.today() + timedelta(days=self.cfg.settings["limits"]["followup_after_days"] + 1)
        self.assertEqual(followup.due(self.cfg, self.store, on=later)[0]["id"], self.oid)
        self.assertEqual(followup.due(self.cfg, self.store, on=date.today()), [])

    def test_learning_is_bounded(self):
        res = followup.learn(self.cfg, self.store)
        self.assertEqual(res["changes"], {})
        text = (self.cfg.root / "config" / "learned.toml").read_text()
        self.assertIn("[segments]", text)


if __name__ == "__main__":
    unittest.main()
