"""The red rules, proven: nothing is released without the owner's approval of that exact draft, a scheduled
run cannot approve for itself, and no code path can send or write approvals."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.helpers import (OWNER_EMAIL, REPO, commit_file, git, init_repo, load_cfg, make_signing_key, new_store, opp,
                           temp_root)

from engine import approval, cards, release
from engine.store import TransitionError


def drafted(cfg, store, **kw):
    from engine.normalize import upsert
    oid, _ = upsert(store, opp(**kw), "remotive")
    store.update(oid, service="excel_automation", segment="accounting")
    cards.set_draft(cfg, store, oid, cards.baseline_draft(cfg, store.get(oid)))
    return oid


class ApprovalGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.attacker_home, cls.attacker_fpr = make_signing_key("GitHub <noreply@github.com>")

    def setUp(self):
        self.root = temp_root()
        init_repo(self.root)
        self.cfg = load_cfg(self.root)
        self.store = new_store()
        self.oid = drafted(self.cfg, self.store)
        self.h = self.store.get(self.oid)["draft_hash"]
        self.outbox = self.root / "outbox"

    # 1 ---------------------------------------------------------------------------------------------------
    def test_release_is_refused_without_any_approval(self):
        with self.assertRaises(release.ReleaseRefused):
            release.release(self.cfg, self.store, self.oid, self.outbox, ref="HEAD")
        self.assertFalse(self.outbox.exists())
        self.assertEqual(self.store.get(self.oid)["status"], "awaiting_approval")

    # 2 ---------------------------------------------------------------------------------------------------
    def test_scheduled_run_cannot_approve_itself(self):
        """A run commits the approval file itself, with the right hash and even the owner's e-mail as author.
        Its commit has no GitHub web-flow signature, so the gate refuses it and nothing is released."""
        os.environ["SEARCHJOBS_CONTEXT"] = "scheduled"
        try:
            commit_file(self.root, f"approvals/{self.oid}.approve", self.h, environment_signing=True)  # spoofed identity
            with self.assertRaises(approval.ApprovalError) as ctx:
                approval.verify(self.cfg, self.oid, self.h, ref="HEAD")
            self.assertIn("web-flow", str(ctx.exception))
            with self.assertRaises(release.ReleaseRefused):
                release.release(self.cfg, self.store, self.oid, self.outbox, ref="HEAD")
            sweep = approval.sweep(self.cfg, self.store, ref="HEAD")
            self.assertEqual(sweep["approved"], [])
            self.assertEqual(self.store.get(self.oid)["status"], "awaiting_approval")
        finally:
            os.environ.pop("SEARCHJOBS_CONTEXT", None)

    def test_a_key_pretending_to_be_github_is_refused(self):
        commit_file(self.root, f"approvals/{self.oid}.approve", self.h, sign_home=self.attacker_home,
                    sign_fpr=self.attacker_fpr)
        with self.assertRaises(approval.ApprovalError):
            approval.verify(self.cfg, self.oid, self.h, ref="HEAD", keyring=self.attacker_home)

    def test_status_cannot_be_set_to_approved_directly(self):
        with self.assertRaises(TransitionError):
            self.store.set_status(self.oid, "approved")

    # 3 ---------------------------------------------------------------------------------------------------
    def _owner_approves(self, content=None, **kw):
        """Stand-in for the owner: a commit signed by a key the test declares trusted."""
        return commit_file(self.root, f"approvals/{self.oid}.approve", content or self.h,
                           sign_home=self.attacker_home, sign_fpr=self.attacker_fpr, **kw)

    def _verify_kw(self):
        return {"ref": "HEAD", "trusted": [self.attacker_fpr], "keyring": self.attacker_home}

    def test_valid_approval_releases_a_packet_and_never_sends(self):
        self._owner_approves()
        packet = release.release(self.cfg, self.store, self.oid, self.outbox, **self._verify_kw())
        self.assertEqual(self.store.get(self.oid)["status"], "ready")
        self.assertNotIn("send it", packet["instruction"].replace("Never send it", ""))
        self.assertTrue((self.outbox / f"{self.oid}.json").exists())

    def test_editing_the_draft_after_approval_cancels_it(self):
        self._owner_approves()
        d = json.loads(self.store.get(self.oid)["draft"])
        d["body"] = d["body"].replace("20-minute", "30-minute")
        cards.set_draft(self.cfg, self.store, self.oid, d)
        with self.assertRaises(release.ReleaseRefused):
            release.release(self.cfg, self.store, self.oid, self.outbox, **self._verify_kw())

    def test_approval_for_another_text_is_refused(self):
        self._owner_approves(content="0" * 64)
        with self.assertRaises(approval.ApprovalError):
            approval.verify(self.cfg, self.oid, self.h, **self._verify_kw())

    def test_approval_commit_that_changes_other_files_is_refused(self):
        self._owner_approves(extra={"config/settings.toml": "tampered = true\n"})
        with self.assertRaises(approval.ApprovalError):
            approval.verify(self.cfg, self.oid, self.h, **self._verify_kw())

    def test_approval_by_someone_else_is_refused(self):
        self._owner_approves(author_email="someone@example.com")
        with self.assertRaises(approval.ApprovalError):
            approval.verify(self.cfg, self.oid, self.h, **self._verify_kw())

    def test_approval_link_points_at_the_owner_branch_with_the_hash(self):
        link = approval.approval_link(self.cfg, self.oid, self.h)
        self.assertIn(f"approvals%2F{self.oid}.approve", link)
        self.assertIn(self.h, link)
        self.assertIn("github.com/coolman1984/Search-Jobs/new/", link)


class ReleaseLimitsTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_root()
        init_repo(self.root)
        self.cfg = load_cfg(self.root)
        self.store = new_store()
        self.home, self.fpr = make_signing_key()

    def _approved_direct(self, n, email):
        from engine.normalize import make_record, upsert
        rec = make_record(track="direct", title=f"clinic {n}: booking", org=f"Clinic {n}",
                          description="Private clinic with phone bookings and paper receipts.")
        oid, _ = upsert(self.store, rec, "osm")
        self.store.update(oid, service="business_system", contact=json.dumps({"email": email}))
        cards.set_draft(self.cfg, self.store, oid, cards.baseline_draft(self.cfg, self.store.get(oid)))
        commit_file(self.root, f"approvals/{oid}.approve", self.store.get(oid)["draft_hash"],
                    sign_home=self.home, sign_fpr=self.fpr)
        return oid

    def kw(self):
        return {"ref": "HEAD", "trusted": [self.fpr], "keyring": self.home}

    def test_daily_ceiling_for_cold_messages(self):
        ids = [self._approved_direct(i, f"info{i}@clinic{i}.example") for i in range(3)]
        out = self.root / "outbox"
        release.release(self.cfg, self.store, ids[0], out, **self.kw())
        release.release(self.cfg, self.store, ids[1], out, **self.kw())
        with self.assertRaises(release.ReleaseRefused) as ctx:
            release.release(self.cfg, self.store, ids[2], out, **self.kw())
        self.assertIn("ceiling", str(ctx.exception))

    def test_opt_out_is_respected_immediately(self):
        oid = self._approved_direct(9, "info@stop.example")
        release.do_not_contact(self.store, "INFO@stop.example ")
        self.assertEqual(self.store.get(oid)["status"], "archived")
        with self.assertRaises(cards.DraftRefused):
            cards.set_draft(self.cfg, self.store, oid, cards.baseline_draft(self.cfg, self.store.get(oid)))


class NoSendPathTests(unittest.TestCase):
    """Static guarantees over the code itself."""

    def engine_sources(self):
        return {p: p.read_text(encoding="utf-8") for p in (REPO / "engine").rglob("*.py")}

    def test_no_code_writes_into_approvals(self):
        for path, text in self.engine_sources().items():
            for line in text.splitlines():
                if "approvals" in line.lower() and re.search(r"write_text|write_bytes|open\(.*['\"]w|mkdir|touch\(|rename\(", line):
                    self.fail(f"{path.name} writes into approvals/: {line.strip()}")

    def test_no_code_sends_email(self):
        for path, text in self.engine_sources().items():
            self.assertNotRegex(text, r"smtplib|sendmail|send_message\(|\.send\(", f"{path.name} contains a send path")

    def test_project_settings_deny_send_and_api_write_tools(self):
        settings = json.loads((REPO / ".claude" / "settings.json").read_text())
        deny = set(settings["permissions"]["deny"])
        for tool in ("mcp__Gmail__send_message", "mcp__Gmail__reply", "mcp__Gmail__forward",
                     "mcp__github__create_or_update_file", "mcp__github__push_files", "mcp__github__merge_pull_request"):
            self.assertIn(tool, deny)


class GuardHookTests(unittest.TestCase):
    def run_guard(self, event) -> int:
        return subprocess.run([sys.executable, str(REPO / ".claude" / "hooks" / "guard.py")],
                              input=json.dumps(event), capture_output=True, text=True).returncode

    def test_blocks_sending_and_api_writes(self):
        self.assertEqual(self.run_guard({"tool_name": "mcp__Gmail__send_message", "tool_input": {}}), 2)
        self.assertEqual(self.run_guard({"tool_name": "mcp__github__create_or_update_file", "tool_input": {}}), 2)

    def test_blocks_writing_approvals(self):
        for cmd in ("echo abc > approvals/F-1.approve", "git add approvals/F-1.approve",
                    "cp x approvals/F-1.approve", "mkdir -p approvals/"):
            self.assertEqual(self.run_guard({"tool_name": "Bash", "tool_input": {"command": cmd}}), 2, cmd)
        self.assertEqual(self.run_guard({"tool_name": "Write", "tool_input": {"file_path": "/x/approvals/a.approve"}}), 2)

    def test_blocks_rest_writes(self):
        cmd = "curl -X PUT https://api.github.com/repos/coolman1984/Search-Jobs/contents/approvals/F-1.approve -d @x"
        self.assertEqual(self.run_guard({"tool_name": "Bash", "tool_input": {"command": cmd}}), 2)

    def test_allows_normal_work(self):
        for cmd in ("python3 -m engine gather", "git log -- approvals/", "git fetch origin main",
                    "curl -s https://api.github.com/repos/coolman1984/Search-Jobs"):
            self.assertEqual(self.run_guard({"tool_name": "Bash", "tool_input": {"command": cmd}}), 0, cmd)
        self.assertEqual(self.run_guard({"tool_name": "mcp__Gmail__create_draft", "tool_input": {}}), 0)


if __name__ == "__main__":
    unittest.main()
