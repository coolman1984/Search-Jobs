"""Where private fields (contacts, enrichment notes, drafts, the direct-outreach track) are kept.

Three modes, picked automatically:
  private   - GitHub confirms the repository is private: everything is stored in plain state/.
  encrypted - repository is public but the environment provides SEARCHJOBS_KEY: private fields are stored
              in state/private.enc (AES-256 via the system's openssl, key never in the repository).
  public    - repository is public and no key: private fields are kept in private/ (git-ignored) for the
              current session only. Approvals cannot survive to the next day in this mode; the daily report
              says so plainly.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

from .config import Config

KEY_ENV = "SEARCHJOBS_KEY"
_OPENSSL = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-md", "sha256"]


class PrivacyError(Exception):
    pass


def repo_is_private(cfg: Config, timeout: float = 15.0) -> bool:
    """Ask GitHub. Any failure (no network, rate limit, odd answer) counts as *public*, the safe side."""
    setting = cfg.settings["repository"].get("visibility", "auto")
    if setting == "public":
        return False
    owner, name = cfg.settings["repository"]["owner"], cfg.settings["repository"]["name"]
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{owner}/{name}",
                                     headers={"Accept": "application/vnd.github+json",
                                              "User-Agent": cfg.settings["http"]["user_agent"]})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("private") is True
    except Exception:
        return False


def mode(cfg: Config, *, check_github: bool = True) -> str:
    if check_github and repo_is_private(cfg):
        return "private"
    if os.environ.get(KEY_ENV):
        return "encrypted"
    return "public"


def _encrypt(data: bytes) -> bytes:
    out = subprocess.run([*_OPENSSL, "-salt", "-pass", f"env:{KEY_ENV}"], input=data,
                         capture_output=True, check=False)
    if out.returncode != 0:
        raise PrivacyError("encryption failed: " + out.stderr.decode(errors="replace")[:200])
    return out.stdout


def _decrypt(data: bytes) -> bytes:
    out = subprocess.run([*_OPENSSL, "-d", "-pass", f"env:{KEY_ENV}"], input=data, capture_output=True, check=False)
    if out.returncode != 0:
        raise PrivacyError("cannot decrypt state/private.enc: the key in SEARCHJOBS_KEY does not match")
    return out.stdout


def save(cfg: Config, payload: dict, current_mode: str) -> Path | None:
    raw = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    if current_mode == "private":
        return None  # already in plain state/ via export_state(public=False)
    if current_mode == "encrypted":
        path = cfg.state_dir / "private.enc"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_encrypt(raw))
        return path
    path = cfg.private_dir / "private_fields.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def load(cfg: Config, current_mode: str) -> dict:
    enc = cfg.state_dir / "private.enc"
    if current_mode in ("encrypted", "private") and enc.exists() and os.environ.get(KEY_ENV):
        return json.loads(_decrypt(enc.read_bytes()).decode("utf-8"))
    plain = cfg.private_dir / "private_fields.json"
    if plain.exists():
        return json.loads(plain.read_text(encoding="utf-8"))
    return {}
