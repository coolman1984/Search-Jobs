"""Load the plain-text configuration. Everything the owner may change lives in config/*.toml."""
from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(os.environ.get("SEARCHJOBS_ROOT", Path(__file__).resolve().parent.parent))

REQUIRED = {
    "settings.toml": ["owner", "repository", "approval", "limits", "http"],
    "sources.toml": [],
    "scoring.toml": ["weights", "skills", "budget", "thresholds", "segments", "segment_points"],
    "evidence.toml": ["owner", "evidence", "services"],
    "blocklist.toml": ["employer", "competitor"],
}


class ConfigError(Exception):
    pass


def normalise(text: str) -> str:
    """Lower case, punctuation removed, single spaces. Shared by the blocklist and de-duplication."""
    text = (text or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def name_hash(name: str) -> str:
    return hashlib.sha256(normalise(name).encode("utf-8")).hexdigest()


@dataclass
class Config:
    root: Path
    settings: dict
    sources: dict
    scoring: dict
    evidence: dict
    blocklist: dict
    learned: dict = field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def private_dir(self) -> Path:
        """Never committed while the repository is public (see .gitignore)."""
        return self.root / "private"

    def source(self, source_id: str) -> dict:
        if source_id not in self.sources:
            raise ConfigError(f"unknown source '{source_id}' (not in config/sources.toml)")
        return self.sources[source_id]

    def red_domains(self) -> set[str]:
        out: set[str] = set()
        for spec in self.sources.values():
            if spec.get("color") == "red":
                out.update(spec.get("domains", []))
        return out


def _load(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"missing configuration file: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid TOML: {exc}") from exc


def load(root: Path | None = None) -> Config:
    root = Path(root or ROOT)
    cfg_dir = root / "config"
    loaded = {}
    for name, keys in REQUIRED.items():
        data = _load(cfg_dir / name)
        missing = [k for k in keys if k not in data]
        if missing:
            raise ConfigError(f"{name} is missing section(s): {', '.join(missing)}")
        loaded[name] = data
    for sid, spec in loaded["sources.toml"].items():
        if spec.get("color") not in {"green", "yellow", "red"}:
            raise ConfigError(f"source '{sid}' needs color = green, yellow or red")
    learned_path = cfg_dir / "learned.toml"
    learned = _load(learned_path) if learned_path.exists() else {}
    return Config(
        root=root,
        settings=loaded["settings.toml"],
        sources=loaded["sources.toml"],
        scoring=loaded["scoring.toml"],
        evidence=loaded["evidence.toml"],
        blocklist=loaded["blocklist.toml"],
        learned=learned,
    )
