"""Thin Onshape REST client: auth, URL parsing, and typed Part Studio calls.

Keys are read from ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY / ONSHAPE_BASE_URL, or
from a local .env (defaults to ../onshape-exp/.env so the existing keys just work).
Auth is HTTP Basic over the API-key pair — Onshape accepts this for API keys.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_BASE = "https://cad.onshape.com/api/v9"
# onshape-exp lives beside the repo (../../onshape-exp/.env from this file).
_ENV_FALLBACK = os.path.join(os.path.dirname(__file__), "..", "..", "onshape-exp", ".env")

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

    def __init__(self, base: str | None = None, load_dotenv: bool = True):
        if load_dotenv:
            load_env()
        self.base = (base or os.environ.get("ONSHAPE_BASE_URL") or DEFAULT_BASE).rstrip("/")
        try:
            access = os.environ["ONSHAPE_ACCESS_KEY"]
            secret = os.environ["ONSHAPE_SECRET_KEY"]
        except KeyError as e:  # pragma: no cover - config error
            raise RuntimeError(
                "Set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY (or provide a .env)"
            ) from e
        self._auth = "Basic " + base64.b64encode(f"{access}:{secret}".encode()).decode()

    def call(self, path: str, payload: dict | None = None, method: str | None = None):
        """GET when no payload; POST otherwise. `method` forces the verb."""
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method or ("POST" if data is not None else "GET"),
            headers={
                "Authorization": self._auth,
                "Accept": "application/json;charset=UTF-8; qs=0.09",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:  # surface Onshape's error body
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"Onshape {e.code} on {path}: {body[:500]}") from e

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
