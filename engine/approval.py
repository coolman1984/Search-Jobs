"""The approval gate. Nothing leaves the system in the owner's name unless this module says the owner approved
that exact draft.

How the owner approves: the daily report shows a link that opens github.com with a new file
`approvals/<opportunity id>.approve` whose content is the draft's SHA-256. He presses "Commit changes".
GitHub signs commits made on its website/app with its own "web-flow" key. An approval is valid only if

  1. the file exists on the approval branch and its content equals the current draft hash (any edit to the
     draft after approval breaks the match),
  2. the commit that last wrote the file is a normal (single-parent) commit that touches only approvals/,
  3. that commit carries a valid GPG signature from one of the trusted web-flow fingerprints, checked offline
     against the public keys in config/keys/ (an agent or script can push commits, but cannot produce that
     signature),
  4. the commit author is one of the owner's GitHub identities and the committer is GitHub itself.

This module only READS approvals. No code in this repository writes to approvals/ (a test enforces that).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .store import Store, now

APPROVALS_DIR = "approvals"


class ApprovalError(Exception):
    pass


@dataclass
class Approval:
    opportunity_id: str
    draft_hash: str
    commit: str
    author: str
    signed_by: str
    verified_at: str


def approval_link(cfg: Config, opp_id: str, draft_hash: str) -> str:
    repo = cfg.settings["repository"]
    query = urllib.parse.urlencode({"filename": f"{APPROVALS_DIR}/{opp_id}.approve", "value": draft_hash})
    return f"https://github.com/{repo['owner']}/{repo['name']}/new/{repo['approval_branch']}?{query}"


def _git(root: Path, *args: str, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env, check=check)


def fetch_approvals(cfg: Config) -> str:
    """Bring the owner's approval commits into this checkout (read-only fetch)."""
    branch = cfg.settings["repository"]["approval_branch"]
    res = _git(cfg.root, "fetch", "--quiet", "origin", branch, check=False)
    return "ok" if res.returncode == 0 else res.stderr.strip()[:200]


def _keyring(cfg: Config) -> str:
    home = tempfile.mkdtemp(prefix="sj-gpg-")
    os.chmod(home, 0o700)
    for key in sorted((cfg.root / "config" / "keys").glob("*.asc")):
        subprocess.run(["gpg", "--homedir", home, "--batch", "--quiet", "--import", str(key)],
                       capture_output=True, check=False)
    return home


def verify(cfg: Config, opp_id: str, draft_hash: str, *, ref: str | None = None,
           trusted: list[str] | None = None, keyring: str | None = None) -> Approval:
    """Raise ApprovalError unless the owner approved exactly this draft. Read-only."""
    root = cfg.root
    ref = ref or f"origin/{cfg.settings['repository']['approval_branch']}"
    path = f"{APPROVALS_DIR}/{opp_id}.approve"

    shown = _git(root, "show", f"{ref}:{path}", check=False)
    if shown.returncode != 0:
        raise ApprovalError(f"no approval file {path} on {ref}")
    if shown.stdout.strip().lower() != draft_hash.lower():
        raise ApprovalError("the approval is for a different version of the draft (the text changed after approval)")

    commit = _git(root, "log", "-1", "--format=%H", ref, "--", path).stdout.strip()
    if not commit:
        raise ApprovalError("cannot find the commit that wrote the approval")
    parents = _git(root, "rev-list", "--parents", "-n", "1", commit).stdout.split()
    if len(parents) != 2:
        raise ApprovalError("the approval must come from a single direct commit, not a merge")
    touched = [p for p in _git(root, "show", "--name-only", "--format=", commit).stdout.splitlines() if p.strip()]
    if not touched or any(not p.startswith(APPROVALS_DIR + "/") for p in touched):
        raise ApprovalError("the approval commit also changes other files")

    author = _git(root, "log", "-1", "--format=%ae", commit).stdout.strip().lower()
    committer = _git(root, "log", "-1", "--format=%ce", commit).stdout.strip().lower()
    owners = [e.lower() for e in cfg.settings["owner"]["author_emails"]]
    if author not in owners:
        raise ApprovalError(f"the approval was not made by the owner (author {author})")
    if trusted is None and committer != cfg.settings["approval"]["committer_email"].lower():
        raise ApprovalError("the approval was not committed through github.com")

    trusted = [t.upper().replace(" ", "") for t in (trusted or cfg.settings["approval"]["trusted_fingerprints"])]
    own_ring = keyring is None
    home = keyring or _keyring(cfg)
    try:
        env = dict(os.environ, GNUPGHOME=home)
        res = _git(root, "verify-commit", "--raw", commit, env=env, check=False)
        status = res.stderr + res.stdout
        signer = next((ln.split()[2] for ln in status.splitlines() if ln.startswith("[GNUPG:] VALIDSIG")), None)
        if res.returncode != 0 or not signer or signer.upper() not in trusted:
            raise ApprovalError("the approval commit is not signed by GitHub's web-flow key")
    finally:
        if own_ring:
            shutil.rmtree(home, ignore_errors=True)
    return Approval(opp_id, draft_hash, commit, author, signer, now())


def check_and_record(cfg: Config, store: Store, opp_id: str, **kw) -> Approval:
    """Verify and, if valid, move the opportunity to 'approved' (the only way into that status)."""
    opp = store.get(opp_id)
    if not opp or not opp.get("draft") or not opp.get("draft_hash"):
        raise ApprovalError(f"{opp_id} has no draft waiting for approval")
    from .cards import draft_hash as compute
    current = compute(json.loads(opp["draft"]))
    if current != opp["draft_hash"]:
        raise ApprovalError("stored draft and its hash disagree; the draft was edited outside the engine")
    appr = verify(cfg, opp_id, current, **kw)
    record = json.dumps(appr.__dict__, ensure_ascii=False)
    store.update(opp_id, approval=record)
    if opp["status"] != "approved":
        store.set_status(opp_id, "approved", f"approved in {appr.commit[:12]}", _approval_gate=True)
    store.commit()
    return appr


def sweep(cfg: Config, store: Store, **kw) -> dict:
    """Check every opportunity that waits for approval. Used by the daily and on-approval routines."""
    found, waiting, problems = [], [], {}
    for opp in store.all("status='awaiting_approval'"):
        try:
            check_and_record(cfg, store, opp["id"], **kw)
            found.append(opp["id"])
        except ApprovalError as exc:
            msg = str(exc)
            (waiting.append(opp["id"]) if msg.startswith("no approval file") else problems.__setitem__(opp["id"], msg))
    return {"approved": found, "waiting": waiting, "problems": problems}
