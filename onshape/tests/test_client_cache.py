"""Offline tests for the disk cache decision (no network).

Regression guard: rollback featurescript POSTs carry a `?rollbackBarIndex=N` query
string, so the old `path.endswith("/featurescript")` check treated them as
uncacheable — every reconstruction re-hit the API (and burned the daily quota).
These calls are the priciest and most-repeated in the pipeline; they MUST cache.
"""
import io
import json

import onshape.client as C
from onshape.client import Onshape


def _make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "ak")
    monkeypatch.setenv("ONSHAPE_SECRET_KEY", "sk")
    return Onshape(load_dotenv=False)


def test_rollback_featurescript_post_is_cached(tmp_path, monkeypatch):
    api = _make_client(tmp_path, monkeypatch)
    calls = [0]
    # urlopen is used as a context manager; return an object supporting `with`.
    class _CM:
        def __enter__(self):
            calls[0] += 1
            return io.BytesIO(json.dumps({"result": 1}).encode())
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(C.urllib.request, "urlopen",
                        lambda req, timeout=None: _CM())

    path = "/partstudios/x/featurescript?rollbackBarIndex=3"
    first = api.call(path, {"script": "s", "queries": {}})
    second = api.call(path, {"script": "s", "queries": {}})
    assert first == second == {"result": 1}
    assert calls[0] == 1  # second call served from disk, no second network hit


def test_plain_featurescript_post_still_cached(tmp_path, monkeypatch):
    api = _make_client(tmp_path, monkeypatch)
    calls = [0]
    class _CM:
        def __enter__(self):
            calls[0] += 1
            return io.BytesIO(json.dumps({"result": 2}).encode())
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(C.urllib.request, "urlopen",
                        lambda req, timeout=None: _CM())
    path = "/partstudios/x/featurescript"
    api.call(path, {"script": "s"})
    api.call(path, {"script": "s"})
    assert calls[0] == 1


def test_distinct_rollback_indices_get_distinct_cache_keys(tmp_path, monkeypatch):
    api = _make_client(tmp_path, monkeypatch)
    seq = iter([{"result": "rb2"}, {"result": "rb4"}])
    class _CM:
        def __enter__(self):
            return io.BytesIO(json.dumps(next(seq)).encode())
        def __exit__(self, *a):
            return False
    monkeypatch.setattr(C.urllib.request, "urlopen",
                        lambda req, timeout=None: _CM())
    r2 = api.call("/partstudios/x/featurescript?rollbackBarIndex=2", {"script": "s"})
    r4 = api.call("/partstudios/x/featurescript?rollbackBarIndex=4", {"script": "s"})
    assert r2 == {"result": "rb2"} and r4 == {"result": "rb4"}  # not cross-contaminated
