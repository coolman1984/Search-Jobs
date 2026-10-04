"""The only door to the internet for collectors. It refuses red sources, unknown URLs and anything over the
daily limit, waits between requests, honours robots.txt for yellow sources, and logs every request."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

from .config import Config
from .store import Store


class Refused(Exception):
    """The request would break a source's terms or our own limits. Never retried, never worked around."""


def host_of(url: str) -> str:
    host = urllib.parse.urlparse(url).hostname or ""
    return host[4:] if host.startswith("www.") else host


def is_red_url(cfg: Config, url: str) -> bool:
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in cfg.red_domains())


class Fetcher:
    def __init__(self, cfg: Config, store: Store, *, sleep=time.sleep, opener=None):
        self.cfg = cfg
        self.store = store
        self.sleep = sleep
        self.opener = opener or urllib.request.urlopen
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _guard(self, source_id: str, url: str) -> dict:
        spec = self.cfg.source(source_id)
        if spec["color"] == "red":
            raise Refused(f"{source_id} is a red source: no automated access, ever")
        if is_red_url(self.cfg, url):
            raise Refused(f"{host_of(url)} belongs to a red platform: no automated access, ever")
        if spec.get("kind") == "connector":
            raise Refused(f"{source_id} is read through its official connector by the agent, not over HTTP")
        limit = spec.get("max_requests_per_day")
        if limit is not None and len(self.store.fetches_today(source_id)) >= limit:
            self.store.log_fetch(source_id, url, "skipped", 0)
            raise Refused(f"{source_id}: daily limit of {limit} requests reached")
        return spec

    def _wait(self, source_id: str, spec: dict) -> None:
        gap = float(spec.get("min_interval_seconds", 10))
        last = self._last.get(source_id)
        if last is not None:
            remaining = gap - (time.monotonic() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last[source_id] = time.monotonic()

    def _robots_ok(self, url: str) -> bool:
        parts = urllib.parse.urlparse(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                body = self._raw_get(base + "/robots.txt").decode("utf-8", "replace")
                rp.parse(body.splitlines())
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    rp.disallow_all = True
                else:
                    rp.allow_all = True
            except Exception:
                rp = None  # robots.txt unreachable: treat as "not allowed" for yellow sources
            self._robots[base] = rp
        rp = self._robots[base]
        return bool(rp and rp.can_fetch(self.cfg.settings["http"]["user_agent"], url))

    def _raw_get(self, url: str, accept: str = "*/*") -> bytes:
        req = urllib.request.Request(url, headers={
            "User-Agent": self.cfg.settings["http"]["user_agent"],
            "Accept": accept,
        })
        with self.opener(req, timeout=self.cfg.settings["http"]["timeout_seconds"]) as resp:
            return resp.read()

    def get(self, source_id: str, url: str, accept: str = "application/json") -> bytes:
        spec = self._guard(source_id, url)
        if spec["color"] == "yellow" and not self._robots_ok(url):
            self.store.log_fetch(source_id, url, "robots-disallow", 0)
            raise Refused(f"robots.txt of {host_of(url)} does not allow this page")
        self._wait(source_id, spec)
        try:
            body = self._raw_get(url, accept)
        except urllib.error.HTTPError as exc:
            self.store.log_fetch(source_id, url, f"http-{exc.code}", 0)
            if exc.code in (403, 429):
                # Blocked or rate-limited: stop for today. Never retry around a block.
                raise Refused(f"{source_id} answered {exc.code}; stopping this source for today") from exc
            raise
        except urllib.error.URLError as exc:
            self.store.log_fetch(source_id, url, "network-error", 0)
            raise Refused(f"{source_id} unreachable ({exc.reason}); is the domain allowed in the environment?") from exc
        self.store.log_fetch(source_id, url, "ok", 0)
        return body

    def get_json(self, source_id: str, url: str):
        return json.loads(self.get(source_id, url).decode("utf-8"))
