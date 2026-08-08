"""Thin Onshape REST client: auth, URL parsing, and typed Part Studio calls.

Keys are read from ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY / ONSHAPE_BASE_URL, or
from a local .env (defaults to ../onshape-exp/.env so the existing keys just work).
Auth is HTTP Basic over the API-key pair — Onshape accepts this for API keys.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_BASE = "https://cad.onshape.com/api/v9"
# onshape-exp lives beside the repo (../../onshape-exp/.env from this file).
_ENV_FALLBACK = os.path.join(os.path.dirname(__file__), "..", "..", "onshape-exp", ".env")
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "out", "cache")
# Persisted per-key lockout {access_key: unlock_epoch}: so a key known to be daily-capped
# is skipped across runs WITHOUT spending a call to rediscover the lock (frugality).
_KEY_STATE = os.path.join(os.path.dirname(__file__), "out", "key_state.json")
_MAX_RETRIES = 6
_TIMEOUT = 45.0          # per-request socket timeout (seconds)
_MIN_INTERVAL = 0.35     # min spacing between live requests (proactive throttle)
_MAX_BACKOFF = 30.0      # cap any single retry sleep; longer Retry-After -> fail fast
_last_request = 0.0
LAST_RETRY_AFTER = None   # Retry-After (s) from the most recent 429, for telemetry


class RateLimited(RuntimeError):
    """Raised when EVERY configured key's daily cap is hit (Retry-After exceeds
    _MAX_BACKOFF). Surfaces immediately instead of sleeping for hours."""


class _KeyCapped(Exception):
    """Internal: the current key hit its daily cap — the caller fails over to the next."""
    def __init__(self, retry_after: float):
        self.retry_after = retry_after


class CacheMiss(RuntimeError):
    """Raised in cache-only mode when a request isn't already on disk. It is NOT a
    RateLimited, so the per-feature resolvers (extract.py) treat it as an ordinary miss
    and skip that feature, letting reconstruction run offline on whatever IS cached."""


def _throttle() -> None:
    global _last_request
    now = time.monotonic()
    wait = _MIN_INTERVAL - (now - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()

# .../documents/<did>/<wvm>/<wid>/e/<eid>  — wvm is w (workspace), v (version) or m (microversion)
_URL_RE = re.compile(
    r"/documents/([0-9a-f]{24})/([wvm])/([0-9a-f]{24})/e/([0-9a-f]{24})"
)


def load_env(path: str | None = None) -> None:
    """Populate os.environ from a .env file (does not overwrite existing vars)."""
    path = path or _ENV_FALLBACK
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


@dataclass(frozen=True)
class PartStudio:
    """The four ids that address a Part Studio element."""

    did: str
    wvm: str  # 'w' | 'v' | 'm'
    wid: str
    eid: str
    url: str = ""

    @property
    def path(self) -> str:
        return f"/partstudios/d/{self.did}/{self.wvm}/{self.wid}/e/{self.eid}"


def parse_url(url: str) -> PartStudio:
    m = _URL_RE.search(url)
    if not m:
        raise ValueError(
            "URL must look like .../documents/<did>/w|v|m/<wid>/e/<eid>"
        )
    did, wvm, wid, eid = m.groups()
    return PartStudio(did=did, wvm=wvm, wid=wid, eid=eid, url=url)


class Onshape:
    """Minimal authenticated Onshape API client."""

    def __init__(self, base: str | None = None, load_dotenv: bool = True, cache: bool = True,
                 cache_only: bool | None = None):
        if load_dotenv:
            load_env()
        self.cache = cache  # cache GET responses on disk (dev iteration; avoids 429s)
        self.base = (base or os.environ.get("ONSHAPE_BASE_URL") or DEFAULT_BASE).rstrip("/")
        # Support N key pairs: ONSHAPE_ACCESS_KEY/SECRET_KEY (primary) then
        # ONSHAPE_ACCESS_KEY_2/SECRET_2, _3, ... When one key hits its daily cap the client
        # fails over to the next available one (see call()). Both keys share a small daily
        # budget, so we skip keys known-locked (persisted) rather than probing them.
        self._keys = self._load_keys()
        if not self._keys:  # pragma: no cover - config error
            raise RuntimeError("Set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY (or provide a .env)")
        self._locks: dict = self._load_locks()
        # Cache-only when asked, OR automatically when every key is currently capped: serve
        # from disk and raise CacheMiss (not RateLimited) on a miss, so offline iteration
        # runs on whatever's cached instead of aborting on the first uncached call.
        self.cache_only = cache_only if cache_only is not None else (self._available_key() is None)

    @staticmethod
    def _load_keys() -> list[dict]:
        keys: list[dict] = []
        def add(a: str | None, s: str | None):
            if a and s:
                auth = "Basic " + base64.b64encode(f"{a}:{s}".encode()).decode()
                keys.append({"access": a, "auth": auth})
        add(os.environ.get("ONSHAPE_ACCESS_KEY"), os.environ.get("ONSHAPE_SECRET_KEY"))
        i = 2
        while os.environ.get(f"ONSHAPE_ACCESS_KEY_{i}"):
            add(os.environ.get(f"ONSHAPE_ACCESS_KEY_{i}"), os.environ.get(f"ONSHAPE_SECRET_KEY_{i}"))
            i += 1
        return keys

    @staticmethod
    def _load_locks() -> dict:
        try:
            return json.load(open(_KEY_STATE))
        except Exception:  # noqa: BLE001
            return {}

    def _save_locks(self) -> None:
        try:
            os.makedirs(os.path.dirname(_KEY_STATE), exist_ok=True)
            json.dump(self._locks, open(_KEY_STATE, "w"))
        except Exception:  # noqa: BLE001
            pass

    def _available_key(self) -> int | None:
        """Index of the first key whose daily-cap lock has expired, else None."""
        now = time.time()
        for i, k in enumerate(self._keys):
            if self._locks.get(k["access"], 0) <= now:
                return i
        return None

    def _lock_key(self, i: int, retry_after: float) -> None:
        self._locks[self._keys[i]["access"]] = time.time() + float(retry_after)
        self._save_locks()

    def call(self, path: str, payload: dict | None = None, method: str | None = None):
        """GET when no payload; POST otherwise. `method` forces the verb.

        Retries 429/5xx with exponential backoff (honoring Retry-After). GET (and
        /featurescript POST) responses are cached on disk. When a key hits its daily
        cap the request is transparently retried on the next available key; RateLimited
        is raised only once EVERY key is exhausted.
        """
        data = json.dumps(payload).encode() if payload is not None else None
        verb = method or ("POST" if data is not None else "GET")

        # Cache GETs, and POSTs to /featurescript (pure geometry queries, keyed by
        # script+payload) so reconstruction can be iterated offline after one call.
        cache_path = None
        # Rollback evaluations POST to /featurescript?rollbackBarIndex=N; strip the
        # query string so those (the priciest, most-repeated) calls are cached too.
        base_path = path.split("?", 1)[0]
        cacheable = verb == "GET" or (verb == "POST" and base_path.endswith("/featurescript"))
        if self.cache and cacheable:
            keysrc = self.base + path + (data.decode() if data else "")
            key = hashlib.sha256(keysrc.encode()).hexdigest()[:32]
            cache_path = os.path.join(_CACHE_DIR, f"{key}.json")
            if os.path.exists(cache_path):
                return json.load(open(cache_path))

        if self.cache_only:                      # offline: never touch the network
            raise CacheMiss(f"cache-only: not cached: {path}")

        while True:                              # fail over across keys on a daily cap
            ki = self._available_key()
            if ki is None:
                soon = (min(self._locks.values()) - time.time()) if self._locks else 0.0
                raise RateLimited(
                    f"all {len(self._keys)} Onshape key(s) rate-limited; soonest reset in "
                    f"~{max(soon, 0) / 3600:.1f}h on {path}")
            try:
                result = self._request(path, data, verb, self._keys[ki]["auth"])
            except _KeyCapped as e:
                self._lock_key(ki, e.retry_after)   # remember + skip this key next time
                continue
            if cache_path:
                os.makedirs(_CACHE_DIR, exist_ok=True)
                json.dump(result, open(cache_path, "w"))
            return result

    def _request(self, path: str, data: bytes | None, verb: str, auth: str):
        """One request on a specific key. Retries transient 5xx/short-429; raises
        _KeyCapped on a daily-cap 429 (multi-hour Retry-After) so call() can fail over."""
        req = urllib.request.Request(
            self.base + path, data=data, method=verb,
            headers={"Authorization": auth,
                     "Accept": "application/json;charset=UTF-8; qs=0.09",
                     "Content-Type": "application/json"})
        _throttle()  # stay under the burst limit
        for attempt in range(_MAX_RETRIES):
            try:
                # A timeout is essential: without it a stalled socket hangs forever
                # and never reaches the retry logic below.
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    global LAST_RETRY_AFTER
                    ra = e.headers.get("Retry-After")
                    LAST_RETRY_AFTER = ra
                    # Onshape's daily-cap 429 carries a multi-HOUR Retry-After. Don't sleep
                    # it off (hangs ~21h); signal call() to fail over to the next key.
                    if ra and float(ra) > _MAX_BACKOFF:
                        raise _KeyCapped(float(ra)) from e
                if e.code in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES - 1:
                    wait = min(float(e.headers.get("Retry-After") or 0) or 2**attempt,
                               _MAX_BACKOFF)
                    time.sleep(wait)
                    continue
                body = e.read().decode(errors="replace")
                raise RuntimeError(f"Onshape {e.code} on {path}: {body[:500]}") from e
            except (urllib.error.URLError, TimeoutError) as e:  # stalled/dropped socket
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise RuntimeError(f"Onshape request error on {path}: {e}") from e
        raise RuntimeError(f"Onshape request failed after {_MAX_RETRIES} retries: {path}")

    # --- typed Part Studio endpoints -------------------------------------------------
    def features(self, ps: PartStudio) -> dict:
        return self.call(f"{ps.path}/features")

    def sketches(self, ps: PartStudio) -> dict:
        return self.call(f"{ps.path}/sketches?includeGeometry=true")

    def featurespecs(self, ps: PartStudio) -> dict:
        return self.call(f"{ps.path}/featurespecs")

    def parts(self, ps: PartStudio) -> list | dict:
        return self.call(f"{ps.path}/parts")

    def massproperties(self, ps: PartStudio) -> dict:
        return self.call(f"{ps.path}/massproperties")

    def bounding_boxes(self, ps: PartStudio) -> dict:
        return self.call(f"{ps.path}/boundingboxes")

    def featurescript(self, ps: PartStudio, script: str, queries: dict | None = None) -> dict:
        return self.call(
            f"{ps.path}/featurescript",
            {"script": script, "queries": queries or {}},
        )
