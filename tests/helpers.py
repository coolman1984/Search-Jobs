"""Shared test helpers: an isolated copy of the configuration in a temporary folder, a fake fetcher, and
a throw-away GPG key for signing commits in a temporary git repository."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
OWNER_EMAIL = "54982309+coolman1984@users.noreply.github.com"


def temp_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="sj-test-"))
    shutil.copytree(REPO / "config", root / "config", ignore=shutil.ignore_patterns("learned.toml"))
    (root / "IDEAS.md").write_text("# ideas\n", encoding="utf-8")
    return root


def load_cfg(root: Path):
    from engine.config import load
    return load(root)


def new_store():
    from engine.store import Store
    return Store(":memory:")


def opp(track="freelance", title="Excel automation for monthly finance reports", org="Acme Trading",
        description=None, **kw):
    from engine.normalize import make_record
    desc = description if description is not None else (
        "We need to automate our monthly finance reports in Excel. Today the team spends two days copying data "
        "from several workbooks and the totals often do not match. Looking for a Python or VBA automation "
        "with a simple dashboard. " * 2)
    return make_record(track=track, title=title, org=org, description=desc, **kw)


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_opener(mapping: dict):
    """mapping: url-substring -> bytes or an Exception instance."""
    calls = []

    def opener(req, timeout=None):
        url = req.full_url
        calls.append(url)
        for key, val in mapping.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return FakeResponse(val)
        raise AssertionError(f"unexpected URL {url}")

    opener.calls = calls
    return opener


def git(root: Path, *args, env=None, check=True) -> str:
    res = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env, check=False)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr}")
    return res.stdout.strip()


def init_repo(root: Path) -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Claude")
    git(root, "config", "user.email", "noreply@anthropic.com")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")


def make_signing_key(uid: str = "GitHub <noreply@github.com>") -> tuple[str, str]:
    """A brand-new key in a private keyring. Returns (GNUPGHOME, fingerprint)."""
    home = tempfile.mkdtemp(prefix="sj-gpg-test-")
    os.chmod(home, 0o700)
    subprocess.run(["gpg", "--homedir", home, "--batch", "--passphrase", "", "--quick-gen-key", uid,
                    "ed25519", "sign", "never"], capture_output=True, check=True)
    out = subprocess.run(["gpg", "--homedir", home, "--list-secret-keys", "--with-colons"], capture_output=True,
                         text=True, check=True).stdout
    fpr = next(line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:"))
    for key in (REPO / "config" / "keys").glob("*.asc"):
        subprocess.run(["gpg", "--homedir", home, "--batch", "--quiet", "--import", str(key)],
                       capture_output=True, check=False)
    return home, fpr


def commit_file(root: Path, rel: str, content: str, *, author_email=OWNER_EMAIL,
                committer_email="noreply@github.com", sign_home: str | None = None, sign_fpr: str | None = None,
                extra: dict | None = None, environment_signing: bool = False) -> str:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    git(root, "add", rel)
    for k, v in (extra or {}).items():
        (root / k).write_text(v, encoding="utf-8")
        git(root, "add", k)
    env = dict(os.environ, GIT_AUTHOR_NAME="coolman1984", GIT_AUTHOR_EMAIL=author_email,
               GIT_COMMITTER_NAME="GitHub", GIT_COMMITTER_EMAIL=committer_email)
    args = ["commit", "-q", "-m", f"approve {rel}"]
    if sign_home:
        env["GNUPGHOME"] = sign_home
        args = ["-c", "gpg.format=openpgp", "-c", f"user.signingkey={sign_fpr}", "-c", "gpg.program=gpg", *args, "-S"]
    elif environment_signing:
        # Whatever signing this machine's git does on its own (in the cloud: an SSH signature) - exactly what a
        # scheduled agent run would produce if it tried to commit an approval itself.
        try:
            git(root, "-c", "commit.gpgsign=true", *args, env=env)
            return git(root, "rev-parse", "HEAD")
        except RuntimeError:
            pass
    git(root, *args, env=env)
    return git(root, "rev-parse", "HEAD")


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path
